"""calSchedules.tasks.advance — auto-advance completed calibrations + status updates."""
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

# sibling modules in this package
from .helpers import _basic_group_completion_check, _get_group_key, _get_group_members


@shared_task(name="calSchedules.tasks.auto_advance_completed_calibrations")
def auto_advance_completed_calibrations():
    """
    GROUP-AWARE: Advance completed calibrations to next period.
    Waits for ALL group members to complete before rescheduling.
    """
    from .instant_reconciliation import check_group_completion_status, get_next_group_month

    logger.info("[AUTO_ADVANCE] Starting GROUP-AWARE auto-advance")

    completed_schedules = CalibrationSchedule.objects.filter(
        status='completed', equipment__active_status=True, active_status=True
    ).select_related('equipment__department', 'equipment__description')

    if not completed_schedules.exists():
        logger.info("[AUTO_ADVANCE] No completed schedules to advance")
        return "No completed schedules to advance"

    advanced_count = skipped_count = waiting_count = 0
    processed_groups = set()

    for schedule in completed_schedules:
        planning_logic = schedule.planning_logic or 'department'
        group_key = _get_group_key(schedule, planning_logic)

        if group_key in processed_groups:
            continue
        processed_groups.add(group_key)

        try:
            group_status = check_group_completion_status(schedule.equipment, schedule.scheduled_month, planning_logic)
        except Exception as e:
            logger.warning(f"[AUTO_ADVANCE] Could not check group status for {group_key}: {e}")
            group_status = _basic_group_completion_check(schedule, planning_logic)

        if not group_status['all_completed']:
            waiting_count += group_status.get('completed', 0)
            logger.info(
                f"[AUTO_ADVANCE] Waiting: {group_status.get('completed', 0)}/{group_status.get('total', 0)} done "
                f"in {schedule.scheduled_month.strftime('%B %Y')} ({planning_logic})"
            )
            continue

        period = schedule.calibration_period or 12
        try:
            next_month = get_next_group_month(schedule.equipment, schedule.scheduled_month, period, planning_logic)
        except Exception:
            next_month = schedule.scheduled_month + relativedelta(months=period)

        logger.info(
            f"[AUTO_ADVANCE] Group complete: {group_status['completed']}/{group_status['total']} in "
            f"{schedule.scheduled_month.strftime('%B %Y')} ({planning_logic})"
        )

        with transaction.atomic():
            for member in _get_group_members(schedule, planning_logic):
                already_exists = CalibrationSchedule.objects.filter(
                    equipment=member.equipment, scheduled_month=next_month, active_status=True
                ).exists()

                if already_exists:
                    skipped_count += 1
                    continue

                CalibrationSchedule.objects.create(
                    equipment=member.equipment,
                    scheduled_month=next_month,
                    status='pending',
                    calibration_period=period,
                    planning_logic=member.planning_logic or planning_logic,
                    generation_source='auto_advance',
                    workshop=member.workshop,
                    active_status=True
                )
                if hasattr(member, 'is_locked') and not member.is_locked:
                    member.is_locked = True
                    member.save(update_fields=['is_locked'])
                advanced_count += 1

        logger.info(f"[AUTO_ADVANCE] Advanced to {next_month.strftime('%B %Y')} ({planning_logic})")

    result = (
        f"Advanced {advanced_count} equipment to next period. "
        f"Waiting for {waiting_count} (groups incomplete). "
        f"Skipped {skipped_count} (already scheduled)."
    )
    logger.info(f"[AUTO_ADVANCE] {result}")
    return result


@shared_task(name="calSchedules.tasks.lock_and_reschedule_completed")
def lock_and_reschedule_completed(wait_for_group=True, force_lock=False, check_hours=None, planning_logic='department'):
    """GROUP-AWARE: Lock completed schedules and reschedule. Recommended automatic rescheduler."""
    logger.info(f"[LOCK_RESCHEDULE] Starting (wait_for_group={wait_for_group}, force={force_lock})")
    result = lock_completed_schedules(
        check_hours=check_hours, force_lock=force_lock,
        wait_for_group=wait_for_group, planning_logic=planning_logic
    )
    logger.info(
        f"[LOCK_RESCHEDULE] Complete: Locked {result['locked']}, "
        f"Waiting {result['waiting_for_group']}, Rescheduled {result['rescheduled']}, Skipped {result['skipped']}"
    )
    return result


@shared_task(name="calSchedules.tasks.daily_lock_and_reschedule")
def daily_lock_and_reschedule():
    """RECOMMENDED: Daily task — lock completed schedules and reschedule groups."""
    logger.info("[DAILY] Starting daily lock and reschedule")
    result = auto_lock_and_reschedule(planning_logic='department')
    logger.info(
        f"[DAILY] Complete: Locked {result['locked']}, "
        f"Waiting {result['waiting_for_group']}, Rescheduled {result['rescheduled']}"
    )
    return result


@shared_task(name="calSchedules.tasks.update_calibration_statuses")
def update_calibration_statuses():
    """Update calibration statuses and auto-advance completed ones (GROUP-AWARE)."""
    today = date.today()
    updated_count = CalibrationSchedule.objects.filter(
        status='pushed', equipment__active_status=True,
        scheduled_month__year=today.year, scheduled_month__month=today.month
    ).update(status='pending')

    advance_result = auto_advance_completed_calibrations()
    result = f"{updated_count} schedule(s) updated to 'pending'. {advance_result}"
    logger.info(f"[UPDATE_STATUS] {result}")
    return result
