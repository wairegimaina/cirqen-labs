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


@shared_task(name="calSchedules.tasks.push_overdue_schedules")
def push_overdue_schedules():
    """Push overdue pending schedules forward to current/future months."""
    today = date.today().replace(day=1)
    overdue = CalibrationSchedule.objects.filter(
        scheduled_month__lt=today, status='pending', equipment__active_status=True
    )
    pushed_count = total_months = 0

    for sched in overdue:
        months_pushed = 0
        while sched.scheduled_month < today:
            sched.scheduled_month += relativedelta(months=1)
            months_pushed += 1
        sched.status = 'pushed'
        sched.save()
        pushed_count += 1
        total_months += months_pushed
        logger.info(f"[PUSH] Pushed schedule {sched.id} forward {months_pushed} month(s)")

    result = f"Pushed {pushed_count} overdue schedules forward ({total_months} total months adjusted)."
    logger.info(f"[PUSH] {result}")
    return result


@shared_task(name="calSchedules.tasks.cleanup_orphaned_calibration_schedules")
def cleanup_orphaned_calibration_schedules():
    """Remove calibration schedules for missing or inactive equipment."""
    orphaned = CalibrationSchedule.objects.filter(equipment__isnull=True)
    orphaned_count = orphaned.count()
    orphaned.delete()

    inactive = CalibrationSchedule.objects.filter(equipment__active_status=False)
    inactive_count = inactive.count()
    if inactive_count:
        inactive.delete()

    result = f"Cleaned up {orphaned_count + inactive_count} orphaned schedules ({inactive_count} inactive equipment)."
    logger.info(f"[CLEANUP] {result}")
    return result


@shared_task(name="calSchedules.tasks.validate_calibration_schedules")
def validate_calibration_schedules():
    """Validate calibration schedules and fix common issues."""
    issues_fixed = 0

    inactive = CalibrationSchedule.objects.filter(equipment__active_status=False)
    inactive_count = inactive.count()
    if inactive_count:
        inactive.delete()
        issues_fixed += inactive_count
        logger.info(f"[VALIDATE] Removed {inactive_count} schedules for inactive equipment")

    duplicates = (
        CalibrationSchedule.objects.filter(equipment__active_status=True)
        .values('equipment_id').annotate(count=Count('equipment_id')).filter(count__gt=1)
    )
    for dup in duplicates:
        schedules = CalibrationSchedule.objects.filter(
            equipment_id=dup['equipment_id'], equipment__active_status=True
        ).order_by('scheduled_month')
        to_delete = list(schedules[1:])
        for s in to_delete:
            s.delete()
            issues_fixed += 1
        if to_delete:
            logger.info(f"[VALIDATE] Removed {len(to_delete)} duplicate schedules for equipment {dup['equipment_id']}")

    result = f"Fixed {issues_fixed} calibration scheduling issues ({inactive_count} inactive equipment schedules)."
    logger.info(f"[VALIDATE] {result}")
    return result


@shared_task(name="calSchedules.tasks.remove_inactive_equipment_schedules")
def remove_inactive_equipment_schedules():
    """Remove all calibration schedules for inactive equipment."""
    qs = CalibrationSchedule.objects.filter(equipment__active_status=False)
    count = qs.count()

    if count:
        for s in qs:
            logger.info(f"[REMOVE_INACTIVE] Removing schedule {s.id} for inactive equipment {s.equipment.id}")
        qs.delete()
        result = f"Removed {count} calibration schedules for inactive equipment"
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

    scheduled_ids = set(
        CalibrationSchedule.objects.filter(equipment__active_status=True).values_list('equipment_id', flat=True)
    )
    unscheduled = Equipment.objects.filter(active_status=True).exclude(id__in=scheduled_ids).select_related('department', 'description')

    if not unscheduled.exists():
        logger.info("[AUTO_SCHEDULE] No unscheduled equipment to schedule")
        return "No unscheduled equipment to schedule"

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

    result = f"Auto-scheduled {created_count} equipment items. Failed: {failed_count}"
    logger.info(f"[AUTO_SCHEDULE] {result}")
    return result
