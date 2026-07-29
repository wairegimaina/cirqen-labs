"""calSchedules.locker.status — lock-status reporting, convenience wrappers, unlock."""
from datetime import date, datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from django.db import transaction
from django.db.models import Q, Count, Max, Min
import logging
from .. import grouping
logger = logging.getLogger(__name__)

# sibling modules in this package
from .core import lock_completed_schedules


def get_lock_status(planning_logic='department'):
    """
    Get comprehensive lock status report

    Returns:
        dict: Lock status information
    """
    from .models import CalibrationSchedule

    logger.info("[LOCKER] Generating lock status report")

    # Total completed schedules
    total_completed = CalibrationSchedule.objects.filter(
        status='completed',
        equipment__active_status=True
    ).count()

    # Locked schedules
    if hasattr(CalibrationSchedule, 'is_locked'):
        locked_completed = CalibrationSchedule.objects.filter(
            status='completed',
            is_locked=True,
            equipment__active_status=True
        ).count()

        unlocked_completed = CalibrationSchedule.objects.filter(
            status='completed',
            is_locked=False,
            equipment__active_status=True
        ).count()
    else:
        locked_completed = 0
        unlocked_completed = total_completed

    # Group completion status
    group_stats = _analyze_group_completion(planning_logic)

    status = {
        'total_completed': total_completed,
        'locked': locked_completed,
        'unlocked': unlocked_completed,
        'lock_percentage': (locked_completed / total_completed * 100) if total_completed > 0 else 0,
        'groups': group_stats,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }

    logger.info(
        f"[LOCKER] Status: {locked_completed}/{total_completed} locked "
        f"({status['lock_percentage']:.1f}%)"
    )

    return status


def _analyze_group_completion(planning_logic):
    """
    Analyze group completion status for locking decisions

    Args:
        planning_logic: 'department' or 'description'

    Returns:
        dict: Group analysis results
    """
    from .models import CalibrationSchedule

    if planning_logic == 'department':
        group_field = 'equipment__department_id'
    else:
        group_field = 'equipment__description_id'

    # Get all active schedules grouped
    groups = CalibrationSchedule.objects.filter(
        equipment__active_status=True,
        active_status=True
    ).values(
        group_field,
        'scheduled_month'
    ).annotate(
        total=Count('id'),
        completed=Count('id', filter=Q(status='completed'))
    )

    fully_complete_groups = 0
    partially_complete_groups = 0
    pending_groups = 0

    for group in groups:
        if group['completed'] == group['total'] and group['total'] > 0:
            fully_complete_groups += 1
        elif group['completed'] > 0:
            partially_complete_groups += 1
        else:
            pending_groups += 1

    return {
        'total_groups': groups.count(),
        'fully_complete': fully_complete_groups,
        'partially_complete': partially_complete_groups,
        'pending': pending_groups,
        'completion_rate': (fully_complete_groups / groups.count() * 100) if groups.count() > 0 else 0
    }


def auto_lock_and_reschedule(planning_logic='department'):
    """
    Convenience function: Auto-lock completed schedules and reschedule

    This is the recommended function to call regularly (e.g., daily via cron)
    """
    return lock_completed_schedules(
        check_hours=None,  # Check all completed schedules
        force_lock=False,  # Wait for group completion
        wait_for_group=True,
        planning_logic=planning_logic
    )


def force_lock_all_completed():
    """
    Emergency function: Force lock all completed schedules immediately

    Use with caution - this bypasses group synchronization
    """
    logger.warning("[LOCKER] FORCE LOCK initiated - bypassing group checks")

    return lock_completed_schedules(
        check_hours=None,
        force_lock=True,
        wait_for_group=False,
        planning_logic='department'
    )


def lock_recent_completed(hours=24, planning_logic='department'):
    """
    Lock recently completed schedules only

    Args:
        hours: Only process schedules completed in last N hours
        planning_logic: 'department' or 'description'
    """
    return lock_completed_schedules(
        check_hours=hours,
        force_lock=False,
        wait_for_group=True,
        planning_logic=planning_logic
    )


def unlock_schedule(schedule_id, reason="Manual unlock"):
    """
    Unlock a specific schedule (use with caution)

    Args:
        schedule_id: ID of schedule to unlock
        reason: Reason for unlocking (for audit trail)

    Returns:
        dict: Unlock result
    """
    from .models import CalibrationSchedule

    try:
        schedule = CalibrationSchedule.objects.get(id=schedule_id)

        if not hasattr(schedule, 'is_locked'):
            return {
                'success': False,
                'message': 'is_locked field not available'
            }

        if not schedule.is_locked:
            return {
                'success': False,
                'message': 'Schedule is not locked'
            }

        schedule.is_locked = False
        schedule.save(update_fields=['is_locked'])

        logger.warning(
            f"[LOCKER] ⚠️ Unlocked schedule {schedule_id} - Reason: {reason}"
        )

        return {
            'success': True,
            'schedule_id': schedule_id,
            'reason': reason
        }

    except CalibrationSchedule.DoesNotExist:
        return {
            'success': False,
            'message': f'Schedule {schedule_id} not found'
        }
    except Exception as e:
        logger.error(f"[LOCKER] Error unlocking schedule {schedule_id}: {e}")
        return {
            'success': False,
            'message': str(e)
        }


def unlock_group(group_id, scheduled_month, planning_logic='department', reason="Manual unlock"):
    """
    Unlock an entire group of schedules (use with extreme caution)

    Args:
        group_id: Department or Description ID
        scheduled_month: Month of schedules to unlock (date)
        planning_logic: 'department' or 'description'
        reason: Reason for unlocking

    Returns:
        dict: Unlock results
    """
    from .models import CalibrationSchedule

    if planning_logic == 'department':
        filter_key = 'equipment__department_id'
    else:
        filter_key = 'equipment__description_id'

    try:
        schedules = CalibrationSchedule.objects.filter(
            **{filter_key: group_id},
            scheduled_month=scheduled_month
        )

        if not hasattr(CalibrationSchedule, 'is_locked'):
            return {
                'success': False,
                'message': 'is_locked field not available'
            }

        locked_schedules = schedules.filter(is_locked=True)
        unlocked_count = locked_schedules.update(is_locked=False)

        logger.warning(
            f"[LOCKER] ⚠️ Unlocked {unlocked_count} schedules in group {group_id} - "
            f"Reason: {reason}"
        )

        return {
            'success': True,
            'unlocked': unlocked_count,
            'group_id': group_id,
            'reason': reason
        }

    except Exception as e:
        logger.error(f"[LOCKER] Error unlocking group {group_id}: {e}")
        return {
            'success': False,
            'message': str(e)
        }
