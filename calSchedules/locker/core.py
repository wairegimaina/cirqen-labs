"""calSchedules.locker.core — locking + rescheduling core."""
from datetime import date, datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from django.db import transaction
from django.db.models import Q, Count, Max, Min
import logging
from .. import grouping
logger = logging.getLogger(__name__)


def lock_completed_schedules(
    check_hours=None,
    force_lock=False,
    wait_for_group=True,
    planning_logic='department'
):
    """
    Lock completed schedules and initiate rescheduling

    This is the main locking function that:
    1. Finds completed schedules
    2. Checks if entire group is complete (if wait_for_group=True)
    3. Locks completed schedules
    4. Triggers rescheduling for locked groups

    Args:
        check_hours: Only check schedules completed in last N hours (None = all)
        force_lock: Lock individual schedules even if group incomplete
        wait_for_group: Wait for entire group completion before locking
        planning_logic: 'department' or 'description' for grouping

    Returns:
        dict: Results with locked count, waiting count, and rescheduled count
    """
    from .models import CalibrationSchedule

    logger.info(
        f"[LOCKER] Starting lock operation "
        f"(wait_for_group={wait_for_group}, force={force_lock})"
    )

    # Build query for completed schedules
    query = Q(
        status='completed',
        equipment__active_status=True
    )

    # Filter by is_locked if field exists
    try:
        query &= Q(is_locked=False)
    except Exception:
        logger.warning("[LOCKER] is_locked field not available")

    # Add time filter if specified
    if check_hours:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=check_hours)
        query &= Q(updated_at__gte=cutoff_time)

    # Get completed schedules
    completed_schedules = CalibrationSchedule.objects.filter(query).select_related(
        'equipment__department',
        'equipment__description'
    )

    if not completed_schedules.exists():
        logger.info("[LOCKER] No unlocked completed schedules found")
        return {
            'locked': 0,
            'waiting_for_group': 0,
            'rescheduled': 0,
            'skipped': 0
        }

    logger.info(f"[LOCKER] Found {completed_schedules.count()} completed schedules")

    # Process with or without group awareness
    if wait_for_group and not force_lock:
        return _lock_with_group_awareness(
            completed_schedules,
            planning_logic
        )
    else:
        return _lock_immediately(
            completed_schedules,
            planning_logic
        )


def _lock_with_group_awareness(completed_schedules, planning_logic):
    """
    Lock schedules only when entire group is complete

    This ensures that groups stay synchronized and reschedule together.
    """
    from .models import CalibrationSchedule

    locked_count = 0
    waiting_count = 0
    rescheduled_count = 0
    skipped_count = 0
    processed_groups = set()

    # Try to use instant_reconciliation for group checking
    try:
        from .instant_reconciliation import (
            check_group_completion_status,
            get_next_group_month
        )
        has_instant_reconciliation = True
    except ImportError:
        logger.warning(
            "[LOCKER] instant_reconciliation not available - "
            "using basic group checking"
        )
        has_instant_reconciliation = False

    for schedule in completed_schedules:
        # ✅ CRITICAL: Use the planning_logic stored in the schedule
        # This ensures we group by the SAME logic that was used to create the schedule
        logic = schedule.planning_logic or planning_logic

        logger.debug(
            f"[LOCKER] Processing schedule {schedule.id} with "
            f"planning_logic='{logic}'"
        )

        # Create unique group identifier based on the schedule's planning logic
        if logic == 'department':
            group_key = f"dept_{schedule.equipment.department_id}_{schedule.scheduled_month}"
            group_id = schedule.equipment.department_id
            group_type = 'department'
        else:
            group_key = f"desc_{schedule.equipment.description_id}_{schedule.scheduled_month}"
            group_id = schedule.equipment.description_id
            group_type = 'description'

        # Skip if we've already processed this group
        if group_key in processed_groups:
            continue

        processed_groups.add(group_key)

        # Check group completion status
        if has_instant_reconciliation:
            group_status = check_group_completion_status(
                schedule.equipment,
                schedule.scheduled_month,
                logic
            )
        else:
            group_status = _basic_group_status(
                group_id,
                schedule.scheduled_month,
                logic
            )

        if group_status['all_completed']:
            # ✅ Entire group complete - lock all and reschedule
            logger.info(
                f"[LOCKER] Group complete ({group_type}): {group_status['completed']}/"
                f"{group_status['total']} schedules in "
                f"{schedule.scheduled_month.strftime('%B %Y')} - "
                f"Planning logic: {logic}"
            )

            with transaction.atomic():
                # Lock all group members
                group_schedules = group_status['members'].filter(
                    status='completed'
                )

                for member in group_schedules:
                    if _lock_schedule(member):
                        locked_count += 1

                # Trigger rescheduling for the group
                reschedule_result = _reschedule_locked_group(
                    group_schedules,
                    schedule.scheduled_month,
                    schedule.calibration_period or 12,
                    logic
                )

                rescheduled_count += reschedule_result['created']
                skipped_count += reschedule_result['skipped']
        else:
            # ⏸️ Group not complete - wait
            waiting_count += group_status['completed']
            logger.info(
                f"[LOCKER] Waiting for {group_type} group: {group_status['completed']}/"
                f"{group_status['total']} complete in "
                f"{schedule.scheduled_month.strftime('%B %Y')} - "
                f"Planning logic: {logic}"
            )

    logger.info(
        f"[LOCKER] Complete: Locked {locked_count}, "
        f"Waiting {waiting_count}, Rescheduled {rescheduled_count}"
    )

    return {
        'locked': locked_count,
        'waiting_for_group': waiting_count,
        'rescheduled': rescheduled_count,
        'skipped': skipped_count
    }


def _lock_immediately(completed_schedules, planning_logic):
    """
    Lock schedules immediately without waiting for group completion

    Use this when force_lock=True or wait_for_group=False
    """
    locked_count = 0
    rescheduled_count = 0
    skipped_count = 0

    logger.info(f"[LOCKER] Force locking {completed_schedules.count()} schedules")

    with transaction.atomic():
        for schedule in completed_schedules:
            if _lock_schedule(schedule):
                locked_count += 1

                # Trigger individual rescheduling
                reschedule_result = _reschedule_single_schedule(
                    schedule,
                    planning_logic
                )

                if reschedule_result['created']:
                    rescheduled_count += 1
                else:
                    skipped_count += 1

    logger.info(
        f"[LOCKER] Force lock complete: Locked {locked_count}, "
        f"Rescheduled {rescheduled_count}"
    )

    return {
        'locked': locked_count,
        'waiting_for_group': 0,
        'rescheduled': rescheduled_count,
        'skipped': skipped_count
    }


def _lock_schedule(schedule):
    """
    Lock a single schedule

    Args:
        schedule: CalibrationSchedule instance

    Returns:
        bool: True if locked, False if already locked or error
    """
    try:
        # Check if already locked
        if hasattr(schedule, 'is_locked') and schedule.is_locked:
            return False

        # Set locked flag
        if hasattr(schedule, 'is_locked'):
            schedule.is_locked = True
            schedule.save(update_fields=['is_locked'])

            logger.debug(
                f"[LOCKER] ✅ Locked schedule {schedule.id} "
                f"(Equipment: {schedule.equipment.id})"
            )
            return True
        else:
            logger.warning(
                f"[LOCKER] is_locked field not available for schedule {schedule.id}"
            )
            return False

    except Exception as e:
        logger.error(f"[LOCKER] Error locking schedule {schedule.id}: {e}")
        return False


def _reschedule_locked_group(group_schedules, current_month, period, planning_logic):
    """
    Create next period schedules for a locked group

    Args:
        group_schedules: QuerySet of completed group members
        current_month: Current scheduled month (date)
        period: Calibration period in months
        planning_logic: 'department' or 'description'

    Returns:
        dict: Results with created and skipped counts
    """
    from .models import CalibrationSchedule

    # Calculate next scheduled month
    next_month = grouping.next_period_month(current_month, period)

    created_count = 0
    skipped_count = 0

    logger.info(
        f"[LOCKER] Rescheduling group to {next_month.strftime('%B %Y')} "
        f"({group_schedules.count()} equipment)"
    )

    for schedule in group_schedules:
        # Check if next schedule already exists
        existing = CalibrationSchedule.objects.filter(
            equipment=schedule.equipment,
            scheduled_month=next_month,
            active_status=True
        ).exists()

        if not existing:
            # Create new schedule
            new_schedule = CalibrationSchedule.objects.create(
                equipment=schedule.equipment,
                scheduled_month=next_month,
                status='pending',
                calibration_period=period,
                planning_logic=planning_logic,
                generation_source='locker',  # Mark as created by locker
                active_status=True
            )

            created_count += 1

            logger.debug(
                f"[LOCKER] ✅ Created schedule for {schedule.equipment.id} "
                f"in {next_month.strftime('%B %Y')}"
            )
        else:
            skipped_count += 1
            logger.debug(
                f"[LOCKER] ⏭️ Schedule already exists for {schedule.equipment.id} "
                f"in {next_month.strftime('%B %Y')}"
            )

    # Trigger sync for new schedules
    if created_count > 0:
        try:
            from .reconciliation import trigger_sync_for_changes
            new_schedule_ids = CalibrationSchedule.objects.filter(
                scheduled_month=next_month,
                generation_source='locker',
                created_at__gte=datetime.now(timezone.utc) - timedelta(minutes=5)
            ).values_list('id', flat=True)

            trigger_sync_for_changes(list(new_schedule_ids), operation='u')
        except Exception as e:
            logger.warning(f"[LOCKER] Could not trigger sync: {e}")

    return {
        'created': created_count,
        'skipped': skipped_count
    }


def _reschedule_single_schedule(schedule, planning_logic):
    """
    Create next period schedule for a single locked schedule

    Args:
        schedule: CalibrationSchedule instance
        planning_logic: 'department' or 'description'

    Returns:
        dict: Results with created flag
    """
    from .models import CalibrationSchedule

    period = schedule.calibration_period or 12
    next_month = grouping.next_period_month(schedule.scheduled_month, period)

    # Check if next schedule already exists
    existing = CalibrationSchedule.objects.filter(
        equipment=schedule.equipment,
        scheduled_month=next_month,
        active_status=True
    ).exists()

    if not existing:
        CalibrationSchedule.objects.create(
            equipment=schedule.equipment,
            scheduled_month=next_month,
            status='pending',
            calibration_period=period,
            planning_logic=planning_logic,
            generation_source='locker',
            active_status=True
        )

        logger.debug(
            f"[LOCKER] ✅ Created individual schedule for {schedule.equipment.id} "
            f"in {next_month.strftime('%B %Y')}"
        )

        return {'created': True}
    else:
        logger.debug(
            f"[LOCKER] ⏭️ Schedule already exists for {schedule.equipment.id} "
            f"in {next_month.strftime('%B %Y')}"
        )

        return {'created': False}


def _basic_group_status(group_id, scheduled_month, planning_logic):
    """
    Fallback group status check when instant_reconciliation unavailable

    Args:
        group_id: Department or Description ID
        scheduled_month: Month to check (date)
        planning_logic: 'department' or 'description'

    Returns:
        dict: Group status information
    """
    from .models import CalibrationSchedule

    if planning_logic == 'department':
        filter_key = 'equipment__department_id'
    else:
        filter_key = 'equipment__description_id'

    # Get all schedules in this group
    group_schedules = CalibrationSchedule.objects.filter(
        **{filter_key: group_id},
        scheduled_month=scheduled_month,
        equipment__active_status=True,
        active_status=True
    )

    total_count = group_schedules.count()
    completed_count = group_schedules.filter(status='completed').count()

    return {
        **grouping.completion_stats(total_count, completed_count),
        'members': group_schedules,
    }
