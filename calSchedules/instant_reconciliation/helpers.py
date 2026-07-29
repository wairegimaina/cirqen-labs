"""calSchedules.instant_reconciliation.helpers — group lookup / completion / immediate-lock helpers."""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from datetime import date, datetime, timezone
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import logging
import uuid
from django.db.models import Count, Min, Q
from ..models import CalibrationSchedule
from ..reconciliation import trigger_sync_for_changes
from .. import grouping
logger = logging.getLogger(__name__)


def lock_schedule_immediately(schedule):
    """
    Lock a schedule immediately without any conditions.

    Args:
        schedule: CalibrationSchedule instance

    Returns:
        bool: True if locked, False if already locked or failed
    """
    if not hasattr(schedule, 'is_locked'):
        logger.warning(f"[LOCK] Schedule {schedule.id} has no is_locked field")
        return False

    if schedule.is_locked:
        logger.debug(f"[LOCK] Schedule {schedule.id} already locked")
        return False

    try:
        schedule.is_locked = True
        schedule.save(update_fields=['is_locked', 'updated_at'])

        logger.info(f"[LOCK] ✅ Immediately locked schedule {schedule.id}")

        # Sync to HQ
        trigger_sync_for_changes([schedule.id], operation='u')

        return True

    except Exception as e:
        logger.error(f"[LOCK] ❌ Failed to lock schedule {schedule.id}: {e}")
        return False


def lock_all_completed_schedules():
    """
    🔒 IMMEDIATE CHECKER: Lock ALL completed schedules without any waiting.

    This function:
    - Finds ALL completed schedules
    - Locks them immediately
    - No group checking
    - No waiting periods
    - No conditions

    Returns:
        dict: Results with locked count
    """
    logger.info("[LOCK_ALL] Starting immediate lock of ALL completed schedules")

    # Find ALL completed schedules that are not locked
    completed_unlocked = CalibrationSchedule.objects.filter(
        status='completed',
        is_locked=False
    ).select_related('equipment')

    total_to_lock = completed_unlocked.count()

    if total_to_lock == 0:
        logger.info("[LOCK_ALL] No unlocked completed schedules found")
        return {
            'locked': 0,
            'already_locked': 0,
            'failed': 0,
            'total': 0
        }

    logger.info(f"[LOCK_ALL] Found {total_to_lock} completed schedules to lock")

    locked_count = 0
    failed_count = 0
    schedule_ids = []

    # Lock each one immediately
    for schedule in completed_unlocked:
        try:
            schedule.is_locked = True
            schedule.save(update_fields=['is_locked', 'updated_at'])
            schedule_ids.append(schedule.id)
            locked_count += 1

            logger.debug(f"[LOCK_ALL] ✅ Locked {schedule.id} (Equipment: {schedule.equipment.id})")

        except Exception as e:
            logger.error(f"[LOCK_ALL] ❌ Failed to lock {schedule.id}: {e}")
            failed_count += 1

    # Sync all locked schedules to HQ
    if schedule_ids:
        trigger_sync_for_changes(schedule_ids, operation='u')
        logger.info(f"[LOCK_ALL] Synced {len(schedule_ids)} locked schedules to HQ")

    # Check how many were already locked
    already_locked = CalibrationSchedule.objects.filter(
        status='completed',
        is_locked=True
    ).count()

    result = {
        'locked': locked_count,
        'already_locked': already_locked,
        'failed': failed_count,
        'total': locked_count + already_locked
    }

    logger.info(
        f"[LOCK_ALL] ✅ Complete! Locked {locked_count} schedules. "
        f"Already locked: {already_locked}, Failed: {failed_count}"
    )

    return result


def get_group_scheduled_month(equipment, planning_logic='department'):
    """
    Get the month where this equipment's group is ALREADY scheduled.

    This ensures all equipment in the same group stay together in one month.

    Args:
        equipment: Equipment object
        planning_logic: 'department' or 'description'

    Returns:
        date: The month where this group is scheduled, or current month if new group
    """
    today = date.today().replace(day=1)

    if planning_logic == 'department':
        dept_id = equipment.department_id if hasattr(equipment, 'department_id') else None

        # Find where this department is ALREADY scheduled
        existing_group = CalibrationSchedule.objects.filter(
            equipment__department_id=dept_id,
            equipment__active_status=True,
            status__in=['pending', 'pushed'],
            scheduled_month__gte=today  # Only future or current schedules
        ).values('scheduled_month').annotate(
            count=Count('id')
        ).order_by('-count', 'scheduled_month').first()

        if existing_group:
            group_month = existing_group['scheduled_month']
            logger.info(
                f"[GROUP_ALIGNMENT] Department {dept_id} group found at "
                f"{group_month.strftime('%B %Y')} with {existing_group['count']} equipment"
            )
            return group_month
        else:
            logger.info(f"[GROUP_ALIGNMENT] New department {dept_id} group, using current month")
            return today

    else:  # description
        desc_id = equipment.description_id if hasattr(equipment, 'description_id') else None

        # Find where this description is ALREADY scheduled
        existing_group = CalibrationSchedule.objects.filter(
            equipment__description_id=desc_id,
            equipment__active_status=True,
            status__in=['pending', 'pushed'],
            scheduled_month__gte=today  # Only future or current schedules
        ).values('scheduled_month').annotate(
            count=Count('id')
        ).order_by('-count', 'scheduled_month').first()

        if existing_group:
            group_month = existing_group['scheduled_month']
            logger.info(
                f"[GROUP_ALIGNMENT] Description {desc_id} group found at "
                f"{group_month.strftime('%B %Y')} with {existing_group['count']} equipment"
            )
            return group_month
        else:
            logger.info(f"[GROUP_ALIGNMENT] New description {desc_id} group, using current month")
            return today


def get_next_group_month(equipment, current_month, period=12, planning_logic='department'):
    """
    Calculate next schedule month for ENTIRE group (not individual equipment).

    Ensures the whole group moves together to the next period.

    Args:
        equipment: Equipment object (to identify the group)
        current_month: Current scheduled month for the group
        period: Calibration period in months
        planning_logic: 'department' or 'description'

    Returns:
        date: Next scheduled month for the entire group
    """
    # Calculate next period from current group month
    next_month = grouping.next_period_month(current_month, period)

    # Verify this makes sense (not too far in future)
    today = date.today()
    clamped = grouping.clamp_far_future_month(next_month, today, period, slack_months=6)
    if clamped != next_month:
        # Current month is too old, recalculate from today
        logger.warning(
            f"[GROUP_NEXT] Calculated next month {next_month.strftime('%Y-%m')} "
            f"is too far in future. Using today as base instead."
        )
        next_month = clamped

    logger.info(
        f"[GROUP_NEXT] Group next month: {next_month.strftime('%B %Y')} "
        f"(from {current_month.strftime('%B %Y')} + {period} months)"
    )

    return next_month


def find_group_members(equipment, scheduled_month, planning_logic='department'):
    """
    Find all equipment in the same group scheduled in the same month.

    Args:
        equipment: Equipment object
        scheduled_month: The month to check
        planning_logic: 'department' or 'description'

    Returns:
        QuerySet: All schedules for the group in that month

    Historical filter for this module: equipment ``active_status=True`` (does NOT
    filter on the schedule's own active_status). Preserved via explicit flags.
    """
    return grouping.group_members_qs(
        equipment, scheduled_month, planning_logic,
        require_equipment_active=True, require_schedule_active=False,
    )


def check_group_completion_status(equipment, scheduled_month, planning_logic='department'):
    """
    Check if ALL equipment in a group have completed their calibrations.

    Args:
        equipment: Equipment object
        scheduled_month: The month to check
        planning_logic: 'department' or 'description'

    Returns:
        dict: {
            'total': total equipment in group,
            'completed': number completed,
            'all_completed': boolean,
            'members': queryset of group members
        }
    """
    members = find_group_members(equipment, scheduled_month, planning_logic)

    stats = grouping.completion_stats(
        members.count(), members.filter(status='completed').count()
    )
    total = stats['total']
    completed = stats['completed']
    all_completed = stats['all_completed']

    if planning_logic == 'department':
        dept_name = equipment.department.name if hasattr(equipment, 'department') and equipment.department else 'N/A'
        group_name = f"Department: {dept_name}"
    else:
        desc_name = equipment.description.name if hasattr(equipment, 'description') and equipment.description else 'N/A'
        group_name = f"Description: {desc_name}"

    logger.info(
        f"[GROUP_STATUS] {group_name} in {scheduled_month.strftime('%B %Y')}: "
        f"{completed}/{total} completed (All done: {all_completed})"
    )

    return {
        'total': total,
        'completed': completed,
        'all_completed': all_completed,
        'members': members,
        'group_name': group_name
    }
