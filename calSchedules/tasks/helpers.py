"""calSchedules.tasks.helpers — pure planning/group/protection helpers."""
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


def _planning_year():
    """Return the current planning year (next year if past June)."""
    return grouping.planning_year()


def _get_group_key(schedule, planning_logic):
    """Unique group key: (planning_logic, group_id, scheduled_month)."""
    return grouping.group_key(schedule, planning_logic)


def _get_group_members(schedule, planning_logic):
    """All active CalibrationSchedule members in the same group/month.

    Uses the canonical rule: the equipment and the schedule must both be
    active. This module used to filter on the schedule only, so a retired
    device stayed a member and its group never completed.
    """
    return grouping.group_members_qs(
        schedule.equipment, schedule.scheduled_month, planning_logic,
        # Canonical rule: both must be active (see grouping.group_members_qs).
        # This previously ignored the equipment flag, so a retired device
        # held its group open and the next period was never created.
        require_schedule_active=True, require_equipment_active=True,
    )


def _basic_group_completion_check(schedule, planning_logic):
    """Fallback group completion check when instant_reconciliation is unavailable."""
    members = _get_group_members(schedule, planning_logic)
    stats = grouping.completion_stats(
        members.count(), members.filter(status='completed').count()
    )
    return {**stats, 'members': members}


def _find_optimal_month(start_date, month_count, group_id, month_map, max_per_month, is_special, max_attempts=36):
    """
    Find the first available month for a group.
    Returns (scheduled_month, updated month_map entry set).
    """
    return grouping.find_optimal_month(
        start_date, month_count, group_id, month_map, max_per_month,
        is_special, max_attempts=max_attempts,
    )


def _is_protected(schedule):
    """True if the schedule must never be touched by normalization/reorganization."""
    return grouping.is_protected(
        schedule.status, schedule.is_locked, schedule.generation_source, PROTECTED_SOURCES
    )


def _apply_schedule_update(schedule, target_month, planning_logic, calibration_period, counts):
    """
    Move a single schedule to target_month. Updates counts dict in-place.
    Handles conflict detection and duplicate removal.
    Returns True if updated, False if skipped.
    """
    old_month = schedule.scheduled_month

    if _is_protected(schedule):
        logger.error(f"[NORMALIZE] Protected schedule {schedule.id} in loop! Skipping.")
        counts['skipped'] += 1
        return False

    if (schedule.scheduled_month == target_month and
            schedule.planning_logic == planning_logic and
            schedule.calibration_period == calibration_period):
        return False  # Already correct — no-op

    if schedule.scheduled_month != target_month:
        conflict = CalibrationSchedule.objects.filter(
            equipment=schedule.equipment,
            scheduled_month=target_month
        ).exclude(id=schedule.id).first()

        if conflict:
            if conflict.status == 'completed' or conflict.generation_source in PROTECTED_SOURCES:
                logger.error(
                    f"[NORMALIZE] Cannot move schedule {schedule.id} — protected conflict at target. Skipping."
                )
                counts['skipped'] += 1
                return False
            logger.warning(f"[NORMALIZE] Removing duplicate schedule {conflict.id} (source: {conflict.generation_source})")
            conflict.delete()
            counts['duplicates_removed'] += 1

    try:
        schedule.scheduled_month = target_month
        schedule.planning_logic = planning_logic
        schedule.calibration_period = calibration_period
        schedule.generation_source = 'normalization'
        schedule.save(update_fields=['scheduled_month', 'planning_logic', 'calibration_period', 'generation_source', 'needs_sync'])
        counts['normalized'] += 1
        logger.debug(f"[NORMALIZE] Schedule {schedule.id}: {old_month.strftime('%B %Y')} -> {target_month.strftime('%B %Y')}")
        return True
    except Exception as e:
        logger.error(f"[NORMALIZE] Failed to normalize schedule {schedule.id}: {e}")
        counts['skipped'] += 1
        return False


def _log_wrap(tag, fn, kwargs, log_result=True):
    """Run fn(**kwargs), logging start and result under [TAG]."""
    logger.info(f"[{tag}] Starting")
    result = fn(**kwargs)
    if log_result:
        logger.info(f"[{tag}] Complete: {result}")
    return result
