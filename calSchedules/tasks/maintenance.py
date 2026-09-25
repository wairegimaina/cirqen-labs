"""calSchedules.tasks.maintenance — push/cleanup/validate/report/schedule tasks."""
from celery import shared_task
from datetime import date, datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from django.db import transaction
from django.db.models import Q, Count
import logging
from Inventory.models import Equipment, Department, EquipmentDescription
from ..models import CalibrationSchedule
from workshop.models import Workshop
from .. import grouping
logger = logging.getLogger(__name__)
from ..reconciliation import full_reconciliation, auto_reschedule_completed_calibrations, ensure_grouping_consistency, diagnose_schedules
from ..locker import lock_completed_schedules, auto_lock_and_reschedule, get_lock_status
PROTECTED_SOURCES = ['signal', 'locker', 'job_card']


def _retire(queryset):
    """Take schedules out of the plan without deleting them.

    ``update()`` skips ``save()``, so ``needs_sync`` and ``updated_at`` are set
    here for the sync agent to pick the change up.
    """
    from django.utils import timezone as dj_timezone
    return queryset.update(active_status=False, needs_sync=True, updated_at=dj_timezone.now())


@shared_task(name="calSchedules.tasks.push_overdue_schedules")
def push_overdue_schedules():
    """Report overdue calibration schedules. Nothing is moved.

    This used to walk every overdue schedule forward to the current month,
    which lost the month the device was actually due in and shifted its
    whole cycle. The due month is now permanent; ``is_overdue`` reports it.
    """
    month_start = date.today().replace(day=1)
    overdue = CalibrationSchedule.open_schedules().filter(
        scheduled_month__lt=month_start, equipment__active_status=True
    ).count()
    result = f"{overdue} overdue calibration schedule(s) (left in their due month)."
    logger.info(f"[PUSH] {result}")
    return result


@shared_task(name="calSchedules.tasks.cleanup_orphaned_calibration_schedules")
def cleanup_orphaned_calibration_schedules():
    """Remove orphaned schedules; retire open schedules of inactive equipment.

    Completed schedules of inactive equipment are calibration history and are
    kept; only work still to be done is taken out of the plan.
    """
    orphaned = CalibrationSchedule.objects.filter(equipment__isnull=True)
    orphaned_count = orphaned.count()
    orphaned.delete()

    inactive_count = _retire(
        CalibrationSchedule.open_schedules().filter(equipment__active_status=False)
    )

    result = f"Cleaned up {orphaned_count} orphaned schedules; retired {inactive_count} open schedules of inactive equipment."
    logger.info(f"[CLEANUP] {result}")
    return result


@shared_task(name="calSchedules.tasks.validate_calibration_schedules")
def validate_calibration_schedules():
    """Validate calibration schedules and fix common issues."""
    issues_fixed = 0

    inactive_count = _retire(
        CalibrationSchedule.open_schedules().filter(equipment__active_status=False)
    )
    if inactive_count:
        issues_fixed += inactive_count
        logger.info(f"[VALIDATE] Retired {inactive_count} open schedules for inactive equipment")

    # More than one OPEN schedule for an equipment: keep the earliest, retire
    # the rest. Completed schedules are history, not duplicates.
    open_qs = CalibrationSchedule.open_schedules().filter(equipment__active_status=True)
    duplicates = (
        open_qs.values('equipment_id').annotate(count=Count('id')).filter(count__gt=1)
    )
    for dup in duplicates:
        extra_ids = list(
            open_qs.filter(equipment_id=dup['equipment_id'])
            .order_by('scheduled_month', 'created_at')
            .values_list('id', flat=True)[1:]
        )
        retired = _retire(CalibrationSchedule.objects.filter(id__in=extra_ids))
        issues_fixed += retired
        if retired:
            logger.info(f"[VALIDATE] Retired {retired} duplicate open schedules for equipment {dup['equipment_id']}")

    result = f"Fixed {issues_fixed} calibration scheduling issues ({inactive_count} inactive equipment schedules)."
    logger.info(f"[VALIDATE] {result}")
    return result


@shared_task(name="calSchedules.tasks.remove_inactive_equipment_schedules")
def remove_inactive_equipment_schedules():
    """Retire open calibration schedules of inactive equipment.

    Completed schedules are kept: they are the device's calibration history.
    This used to delete every schedule, completed ones included.
    """
    qs = CalibrationSchedule.open_schedules().filter(equipment__active_status=False)
    count = _retire(qs)

    if count:
        result = f"Retired {count} open calibration schedules for inactive equipment"
    else:
        result = "No schedules to remove"

    logger.info(f"[REMOVE_INACTIVE] {result}")
    return result


@shared_task(name="calSchedules.tasks.bulk_update_calibration_periods")
def bulk_update_calibration_periods(equipment_ids, new_period):
    """Bulk update calibration periods for specific equipment."""
    updated_count = CalibrationSchedule.objects.filter(
        equipment_id__in=equipment_ids, equipment__active_status=True
    ).update(calibration_period=new_period)
    result = f"Updated calibration period to {new_period} months for {updated_count} schedules."
    logger.info(f"[BULK_UPDATE] {result}")
    return result


@shared_task(name="calSchedules.tasks.generate_monthly_calibration_report")
def generate_monthly_calibration_report(target_month=None, target_year=None):
    """Generate a report of calibration schedules for a specific month."""
    target_month = target_month or date.today().month
    target_year = target_year or date.today().year

    target_date = date(target_year, target_month, 1)
    next_month = target_date + relativedelta(months=1)

    schedules = CalibrationSchedule.objects.filter(
        scheduled_month__gte=target_date, scheduled_month__lt=next_month,
        equipment__active_status=True
    ).select_related('equipment__department', 'equipment__description')

    report_data = {
        'month': target_date.strftime('%B %Y'),
        'total_schedules': schedules.count(),
        'pending': schedules.filter(status='pending').count(),
        'completed': schedules.filter(status='completed').count(),
        'pushed': schedules.filter(status='pushed').count(),
        'by_department': {}
    }

    for schedule in schedules:
        dept_name = schedule.equipment.department.name if schedule.equipment.department else 'No Department'
        dept_data = report_data['by_department'].setdefault(dept_name, {'total': 0, 'pending': 0, 'completed': 0, 'pushed': 0})
        dept_data['total'] += 1
        dept_data[schedule.status] += 1

    logger.info(
        f"[REPORT] Generated calibration report for {target_date.strftime('%B %Y')}: "
        f"{schedules.count()} active equipment schedules"
    )
    return report_data


def _resume_calibration_chains(equipment_ids):
    """Give equipment whose calibration chain broke its next schedule.

    ``equipment_ids`` have completed calibrations but no open schedule,
    usually because their group waited on a member that was never done. Each
    resumes from its own latest completed schedule, one calibration period on
    and stepped forward by whole periods to this month or later, so it keeps
    its month-of-year slot (see ``grouping.resume_month``).

    Returns the number of equipment rescheduled.
    """
    this_month = date.today().replace(day=1)
    latest = {}
    completed = CalibrationSchedule.objects.filter(
        equipment_id__in=equipment_ids, status='completed', pending_delete=False,
    ).select_related('equipment').order_by('equipment_id', '-scheduled_month')
    for sched in completed:
        latest.setdefault(sched.equipment_id, sched)

    resumed = 0
    for equipment_id, last in latest.items():
        period = last.calibration_period or 12
        target = grouping.resume_month(last.scheduled_month, period, this_month)
        try:
            with transaction.atomic():
                # A retired, never-completed row can already sit in that
                # month (unique per equipment and month): bring it back.
                stale = CalibrationSchedule.objects.filter(
                    equipment_id=equipment_id, scheduled_month=target,
                ).exclude(status='completed').first()
                if stale:
                    stale.status = 'pending'
                    stale.active_status = True
                    stale.pending_delete = False
                    stale.generation_source = 'signal'
                    stale.parent_schedule = last
                    stale.save()
                else:
                    CalibrationSchedule.objects.create(
                        equipment=last.equipment,
                        workshop=last.workshop,
                        scheduled_month=target,
                        status='pending',
                        calibration_period=period,
                        planning_logic=last.planning_logic,
                        generation_source='signal',
                        parent_schedule=last,
                        active_status=True,
                    )
            resumed += 1
            logger.info(
                f"[RESUME] Equipment {equipment_id}: last completed "
                f"{last.scheduled_month.strftime('%B %Y')} + {period}m -> {target.strftime('%B %Y')}"
            )
        except Exception as e:
            logger.error(f"[RESUME] Could not reschedule equipment {equipment_id}: {e}")
    return resumed


@shared_task(name="calSchedules.tasks.auto_schedule_unscheduled_equipment")
def auto_schedule_unscheduled_equipment(
    planning_logic='department',
    calibration_period=12,
    base_month=None,
    base_year=None,
    max_departments=100,
    max_descriptions=100
):
    """Automatically schedule unscheduled equipment using default settings."""
    today = date.today()
    base_month = base_month or today.month
    if base_year is None:
        base_year = today.year + 1 if today.month > 6 else today.year

    start_date = date(base_year, base_month, 1)

    # Unscheduled = no open schedule. Completed history alone does not count:
    # that is a chain that stopped, not a scheduled device.
    scheduled_ids = set(
        CalibrationSchedule.open_schedules().filter(equipment__active_status=True)
        .values_list('equipment_id', flat=True)
    )
    unscheduled = Equipment.objects.filter(active_status=True).exclude(id__in=scheduled_ids)

    # Equipment with completed history continues its own cycle.
    with_history = set(
        CalibrationSchedule.objects.filter(
            equipment__in=unscheduled, status='completed', pending_delete=False
        ).values_list('equipment_id', flat=True)
    )
    resumed = _resume_calibration_chains(with_history) if with_history else 0

    # Only equipment never calibrated (or whose rows were all retired) is
    # placed afresh below.
    unscheduled = unscheduled.exclude(id__in=with_history).select_related('department', 'description')

    if not unscheduled.exists():
        logger.info(f"[AUTO_SCHEDULE] Resumed {resumed} broken chains; no never-scheduled equipment")
        return f"Resumed {resumed} broken chains. No unscheduled equipment to schedule"

    logger.info(f"[AUTO_SCHEDULE] Found {unscheduled.count()} unscheduled items")

    month_dept_count = defaultdict(int)
    month_desc_count = defaultdict(int)
    for sched in CalibrationSchedule.objects.filter(equipment__active_status=True).select_related('equipment__department', 'equipment__description'):
        mk = sched.scheduled_month.replace(day=1)
        if sched.equipment.department:
            month_dept_count[mk] += 1
        if sched.equipment.description:
            month_desc_count[mk] += 1

    created_count = failed_count = 0
    is_dept = planning_logic == 'department'
    month_count = month_dept_count if is_dept else month_desc_count
    max_per_month = max_departments if is_dept else max_descriptions
    attr = 'department' if is_dept else 'description'

    for equip in unscheduled:
        scheduled_month = None
        for offset in range(36):
            candidate = start_date + relativedelta(months=offset)
            if month_count[candidate] < max_per_month or not getattr(equip, attr):
                scheduled_month = candidate
                month_count[candidate] += 1
                break
        if scheduled_month is None:
            scheduled_month = start_date + relativedelta(months=36)

        try:
            CalibrationSchedule.objects.create(
                equipment=equip, scheduled_month=scheduled_month,
                status='pending', calibration_period=calibration_period,
                planning_logic=planning_logic, generation_source='auto_schedule', active_status=True
            )
            created_count += 1
            logger.debug(f"[AUTO_SCHEDULE] Scheduled equipment {equip.id} -> {scheduled_month.strftime('%B %Y')}")
        except Exception as e:
            logger.error(f"[AUTO_SCHEDULE] Failed for equipment {equip.id}: {e}")
            failed_count += 1

    result = f"Auto-scheduled {created_count} equipment items, resumed {resumed} broken chains. Failed: {failed_count}"
    logger.info(f"[AUTO_SCHEDULE] {result}")
    return result
