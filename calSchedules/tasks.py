from celery import shared_task
from datetime import date, datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from django.db import transaction
from django.db.models import Q, Count
import logging

from Inventory.models import Equipment, Department, EquipmentDescription
from .models import CalibrationSchedule
from workshop.models import Workshop

logger = logging.getLogger(__name__)

from .reconciliation import (
    full_reconciliation,
    auto_reschedule_completed_calibrations,
    ensure_grouping_consistency,
    diagnose_schedules
)
from .locker import lock_completed_schedules, auto_lock_and_reschedule, get_lock_status


# ─── PROTECTED SOURCES ────────────────────────────────────────────────────────
#
# Denylist approach: any generation_source NOT in PROTECTED_SOURCES is
# considered normalizable/reorganisable. This matches models.py so that
# is_normalizable() on an instance and the queryset filters here always agree.
#
# Protected (never moved):
#   signal      - auto-created after completion; maintains calibration interval
#   locker      - created by the locker module; must stay put
#   job_card    - triggered by actual job-card work; interval matters
#
# auto_advance is intentionally removed from PROTECTED_SOURCES — those schedules
# are future pending entries that should be reorganisable if the planning logic
# changes, just like normalization-created ones.

PROTECTED_SOURCES = ['signal', 'locker', 'job_card']


# ─── SHARED HELPERS ───────────────────────────────────────────────────────────

def _planning_year():
    """Return the current planning year (next year if past June)."""
    today = date.today()
    return today.year + 1 if today.month > 6 else today.year


def _get_group_key(schedule, planning_logic):
    """Unique group key: (planning_logic, group_id, scheduled_month)."""
    if planning_logic == 'department':
        group_id = schedule.equipment.department_id if schedule.equipment and schedule.equipment.department else 'no_department'
    else:
        group_id = schedule.equipment.description_id if schedule.equipment and schedule.equipment.description else 'no_description'
    month_key = schedule.scheduled_month.strftime('%Y-%m') if schedule.scheduled_month else 'unknown'
    return f"{planning_logic}_{group_id}_{month_key}"


def _get_group_members(schedule, planning_logic):
    """All active CalibrationSchedule members in the same group/month."""
    if planning_logic == 'department':
        group_id = schedule.equipment.department_id if schedule.equipment else None
        return CalibrationSchedule.objects.filter(
            equipment__department_id=group_id,
            scheduled_month=schedule.scheduled_month,
            active_status=True
        ).select_related('equipment')
    else:
        group_id = schedule.equipment.description_id if schedule.equipment else None
        return CalibrationSchedule.objects.filter(
            equipment__description_id=group_id,
            scheduled_month=schedule.scheduled_month,
            active_status=True
        ).select_related('equipment')


def _basic_group_completion_check(schedule, planning_logic):
    """Fallback group completion check when instant_reconciliation is unavailable."""
    members = _get_group_members(schedule, planning_logic)
    total = members.count()
    completed = members.filter(status='completed').count()
    return {'total': total, 'completed': completed, 'all_completed': total > 0 and total == completed, 'members': members}


def _find_optimal_month(start_date, month_count, group_id, month_map, max_per_month, is_special, max_attempts=36):
    """
    Find the first available month for a group.
    Returns (scheduled_month, updated month_map entry set).
    """
    if group_id in month_map:
        return month_map[group_id]

    for offset in range(max_attempts):
        candidate = start_date + relativedelta(months=offset)
        count = sum(1 for m in month_map.values() if m == candidate)
        if is_special or count < max_per_month:
            month_map[group_id] = candidate
            return candidate

    fallback = start_date + relativedelta(months=max_attempts)
    month_map[group_id] = fallback
    return fallback


def _is_protected(schedule):
    """True if the schedule must never be touched by normalization/reorganization."""
    return (
        schedule.status == 'completed' or
        schedule.is_locked or
        schedule.generation_source in PROTECTED_SOURCES
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


# ─── GROUP-AWARE RESCHEDULING ──────────────────────────────────────────────────

@shared_task
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


# ─── LOCK AND RESCHEDULE ──────────────────────────────────────────────────────

@shared_task
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


@shared_task
def daily_lock_and_reschedule():
    """RECOMMENDED: Daily task — lock completed schedules and reschedule groups."""
    logger.info("[DAILY] Starting daily lock and reschedule")
    result = auto_lock_and_reschedule(planning_logic='department')
    logger.info(
        f"[DAILY] Complete: Locked {result['locked']}, "
        f"Waiting {result['waiting_for_group']}, Rescheduled {result['rescheduled']}"
    )
    return result


# ─── NORMALIZATION ────────────────────────────────────────────────────────────

@shared_task
def normalize_existing_schedules(
    planning_logic='description_based',
    base_month=1,
    max_departments=100,
    max_descriptions=100,
    calibration_period=12,
    special_class_departments=None,
    special_class_descriptions=None
):
    """
    Normalize schedules for the current planning year only.
    THREE-LAYER PROTECTION: never touches completed, locked, or signal-created schedules.
    Year is auto-selected: current year if ≤ June, next year if > June.
    """
    if planning_logic not in ('description_based', 'date_based'):
        result = f"Error: Unknown planning_logic '{planning_logic}'. Expected 'description_based' or 'date_based'."
        logger.error(f"[NORMALIZE] {result}")
        return result

    special_class_departments = special_class_departments or []
    special_class_descriptions = special_class_descriptions or []

    base_year = _planning_year()
    start_date = date(base_year, base_month, 1)
    end_date = date(base_year, 12, 31)
    today = date.today()

    logger.info(f"[NORMALIZE] Starting normalization for {base_year} (today: {today})")

    schedules = CalibrationSchedule.objects.filter(
        active_status=True,
        status__in=['pending', 'pushed'],
        is_locked=False,
        scheduled_month__gte=start_date,
        scheduled_month__lte=end_date
    ).exclude(
        generation_source__in=PROTECTED_SOURCES
    ).select_related('equipment__department', 'equipment__description').order_by('id')

    if not schedules.exists():
        logger.info(f"[NORMALIZE] No schedules available for normalization in {base_year}")
        return f"No schedules available for normalization in {base_year}"

    # Warn on logic mismatches and stamp warning flag
    existing_logics = set(
        schedules.exclude(planning_logic=planning_logic).exclude(planning_logic__isnull=True)
        .values_list('planning_logic', flat=True).distinct()
    )
    if existing_logics:
        mismatch_count = schedules.exclude(planning_logic=planning_logic).count()
        logger.warning(
            f"[NORMALIZE] LOGIC MISMATCH: {mismatch_count} schedules use {existing_logics}, "
            f"overriding to '{planning_logic}'."
        )
        CalibrationSchedule.objects.filter(
            id__in=schedules.exclude(planning_logic=planning_logic).values_list('id', flat=True)
        ).update(logic_change_warning=(
            f"Auto-flagged by normalize_existing_schedules: logic overridden "
            f"from {existing_logics} to '{planning_logic}' on {today.strftime('%Y-%m-%d')}."
        ))

    # Log protection counts
    for label, qs in [
        ('completed', CalibrationSchedule.objects.filter(active_status=True, status='completed')),
        ('locked', CalibrationSchedule.objects.filter(active_status=True, is_locked=True, status__in=['pending', 'pushed'])),
        ('system-created', CalibrationSchedule.objects.filter(active_status=True, generation_source__in=PROTECTED_SOURCES, status__in=['pending', 'pushed'])),
        ('future', CalibrationSchedule.objects.filter(active_status=True, status__in=['pending', 'pushed'], scheduled_month__gt=end_date)),
    ]:
        logger.info(f"[NORMALIZE] Protected ({label}): {qs.count()}")

    counts = {'normalized': 0, 'skipped': 0, 'duplicates_removed': 0}

    # Determine grouping key and special-class list based on planning_logic
    if planning_logic == 'description_based':
        get_group_id = lambda s: s.equipment.description_id if s.equipment and s.equipment.description else 'no_desc'
        special_ids = [str(x) for x in special_class_descriptions]
        sort_sentinel = 'no_desc'
        max_per_month = max_descriptions
        label = 'description'
    else:  # date_based
        get_group_id = lambda s: s.equipment.department_id if s.equipment and s.equipment.department else 'no_dept'
        special_ids = [str(x) for x in special_class_departments]
        sort_sentinel = 'no_dept'
        max_per_month = max_departments
        label = 'department'

    # Group schedules
    groups = defaultdict(list)
    for schedule in schedules:
        if not schedule.equipment or not schedule.equipment.active_status or _is_protected(schedule):
            counts['skipped'] += 1
            continue
        groups[get_group_id(schedule)].append(schedule)

    month_offset = 0
    month_map = {}
    for group_id in sorted(groups, key=lambda x: (x == sort_sentinel, str(x))):
        is_special = str(group_id) in special_ids
        target_month = _find_optimal_month(start_date, {}, group_id, month_map, max_per_month, is_special)
        if is_special:
            month_offset += 1
        for schedule in groups[group_id]:
            _apply_schedule_update(schedule, target_month, planning_logic, calibration_period, counts)

    result = (
        f"Normalized {counts['normalized']} schedules by {label} in {base_year}. "
        f"{len(groups)} {label}s processed. "
        f"Removed {counts['duplicates_removed']} duplicates. Skipped {counts['skipped']}."
    )
    logger.info(f"[NORMALIZE] {result}")
    return result


# ─── SMART REORGANIZER ────────────────────────────────────────────────────────

@shared_task
def smart_reorganize_on_logic_change(
    new_planning_logic,
    base_month=1,
    max_departments=100,
    max_descriptions=100,
    calibration_period=12,
    special_class_departments=None,
    special_class_descriptions=None,
    dry_run=False
):
    """
    Safely re-normalise ALL pending/pushed schedules when planning logic changes.

    Key design decisions:
    - No year window — works across all years so schedules in future planning
      cycles (e.g. June 2027) are not silently skipped.
    - Directly moves schedules via _apply_schedule_update rather than delegating
      to normalize_existing_schedules (which is year-scoped and would miss them).
    - Clears logic_change_warning on every schedule that ends up on the correct
      logic, whether it was moved or was already correct.
    - Three-layer protection: completed, is_locked, and PROTECTED_SOURCES are
      never touched.
    """
    special_class_departments = special_class_departments or []
    special_class_descriptions = special_class_descriptions or []

    logger.info("=" * 80)
    logger.info(
        f"[SMART_REORG] {'DRY RUN — ' if dry_run else ''}Smart reorganization "
        f"-> '{new_planning_logic}' (all years)"
    )
    logger.info("=" * 80)

    # ── 1. Build the candidate queryset — no year restriction ─────────────────
    # Uses denylist so any new generation_source is automatically included.
    base_qs = CalibrationSchedule.objects.filter(
        active_status=True,
        status__in=['pending', 'pushed'],
        is_locked=False,
    ).exclude(
        generation_source__in=PROTECTED_SOURCES
    ).select_related('equipment__department', 'equipment__description')

    # Schedules to act on: wrong logic OR have an outstanding warning
    all_to_reorg = base_qs.filter(
        Q(logic_change_warning__gt='') |
        ~Q(planning_logic=new_planning_logic)
    )
    total = all_to_reorg.count()

    # ── 2. Count protected items for reporting ─────────────────────────────────
    protected = {
        'completed':     CalibrationSchedule.objects.filter(active_status=True, status='completed').count(),
        'locked':        CalibrationSchedule.objects.filter(active_status=True, is_locked=True, status__in=['pending', 'pushed']).count(),
        'signal_created':CalibrationSchedule.objects.filter(active_status=True, generation_source__in=PROTECTED_SOURCES, status__in=['pending', 'pushed']).count(),
    }
    logger.info(f"[SMART_REORG] Found {total} to reorganize. Protected: {protected}")

    if total == 0:
        result = {
            'status': 'nothing_to_do',
            'reorganized': 0,
            'warnings': [],
            'dry_run': dry_run,
            'message': f"All schedules already use '{new_planning_logic}' or are protected.",
        }
        logger.info(f"[SMART_REORG] {result['message']}")
        return result

    # ── 3. Log what will change ────────────────────────────────────────────────
    warnings_list = []
    for sched in all_to_reorg:
        equip = sched.equipment
        group_label = (
            f"Department: {equip.department.name}" if equip.department else "No Department"
        ) if new_planning_logic == 'date_based' else (
            f"Description: {equip.description.name}" if equip.description else "No Description"
        )
        msg = (
            f"SCHEDULE {sched.id} — '{equip.description}' ({group_label}): "
            f"'{sched.planning_logic or 'not_set'}' -> '{new_planning_logic}'. "
            f"Scheduled: {sched.scheduled_month.strftime('%B %Y')} | "
            f"Source: {sched.generation_source}"
        )
        warnings_list.append(msg)
        logger.warning(f"[SMART_REORG] {msg}")

    logger.warning(
        f"[SMART_REORG] {len(warnings_list)} schedules will be reorganized. "
        f"{'DRY RUN — no changes saved.' if dry_run else 'Proceeding...'}"
    )

    if dry_run:
        return {
            'status': 'dry_run',
            'reorganized': 0,
            'would_reorganize': total,
            'warnings': warnings_list,
            'dry_run': True,
            'message': f"DRY RUN: {total} schedules would be reorganized.",
        }

    # ── 4. Group schedules by their group key and find the correct target month ─
    #
    # Strategy: for each group (department or description), find the month that
    # the majority of that group is already scheduled for — and align any
    # outliers to it. This preserves existing group months rather than
    # bulldozing everything to a computed offset.
    #
    # If a group has no existing consensus (e.g. all are misaligned), fall back
    # to _find_optimal_month to assign a free month from base_month onwards.

    is_dept = new_planning_logic == 'date_based'

    def get_group_id(sched):
        eq = sched.equipment
        if is_dept:
            return eq.department_id if eq and eq.department else 'no_dept'
        return eq.description_id if eq and eq.description else 'no_desc'

    # Build group -> consensus_month map from ALL schedules on the new logic
    # (not just the ones being moved) so we align to what's already correct.
    consensus_filter = dict(
        active_status=True,
        status__in=['pending', 'pushed', 'completed'],
        planning_logic=new_planning_logic,
    )
    if is_dept:
        consensus_filter['equipment__department__isnull'] = False
    else:
        consensus_filter['equipment__description__isnull'] = False

    group_month_votes = defaultdict(lambda: defaultdict(int))
    for s in CalibrationSchedule.objects.filter(**consensus_filter).select_related('equipment'):
        gid = get_group_id(s)
        group_month_votes[gid][s.scheduled_month] += 1

    consensus_month = {}
    for gid, votes in group_month_votes.items():
        consensus_month[gid] = max(votes, key=votes.get)

    # Fallback month map for groups with no consensus
    fallback_month_map = {}
    today = date.today()
    fallback_start = date(today.year if today.month <= 6 else today.year + 1, base_month, 1)
    max_per_month = max_departments if is_dept else max_descriptions

    # ── 5. Apply moves ─────────────────────────────────────────────────────────
    counts = {'normalized': 0, 'skipped': 0, 'duplicates_removed': 0}

    with transaction.atomic():
        for sched in all_to_reorg:
            gid = get_group_id(sched)

            if gid in consensus_month:
                target = consensus_month[gid]
            else:
                target = _find_optimal_month(
                    fallback_start, {}, gid, fallback_month_map, max_per_month,
                    is_special=False
                )

            _apply_schedule_update(sched, target, new_planning_logic, calibration_period, counts)

    logger.info(
        f"[SMART_REORG] Moves complete — "
        f"moved: {counts['normalized']}, skipped: {counts['skipped']}, "
        f"duplicates removed: {counts['duplicates_removed']}"
    )

    # ── 6. Clear logic_change_warning on everything now correctly aligned ──────
    cleared_count = 0
    for sched in all_to_reorg:
        sched.refresh_from_db()
        if sched.planning_logic == new_planning_logic:
            sched.logic_change_warning = ''
            sched.previous_planning_logic = ''
            sched.save(update_fields=['logic_change_warning', 'previous_planning_logic', 'needs_sync'])
            cleared_count += 1

    logger.info(f"[SMART_REORG] Cleared logic warnings on {cleared_count} schedules.")
    logger.info("=" * 80)

    return {
        'status': 'complete',
        'reorganized': counts['normalized'],
        'warnings_cleared': cleared_count,
        'duplicates_removed': counts['duplicates_removed'],
        'skipped': counts['skipped'],
        'warnings': warnings_list,
        'protected': protected,
        'dry_run': False,
        'message': (
            f"Reorganized {counts['normalized']} schedules to '{new_planning_logic}' logic. "
            f"Cleared warnings on {cleared_count}. "
            f"Removed {counts['duplicates_removed']} duplicates. "
            f"Skipped {counts['skipped']}."
        ),
    }


# ─── INITIALIZE ───────────────────────────────────────────────────────────────

@shared_task
def initialize_calibration_schedule_with_logic(
    planning_logic='department',
    calibration_period=12,
    base_month=1,
    base_year=None,
    max_departments=100,
    max_descriptions=100,
    selected_descriptions=None,
    preserve_existing=True,
    specific_equipment_ids=None,
    normalize_existing=True,
    special_class_departments=None,
    special_class_descriptions=None
):
    """
    Initialize calibration schedules globally.
    Optionally normalizes existing schedules first, then schedules any unscheduled equipment.
    """
    if base_year is None:
        base_year = _planning_year()

    start_date = date(base_year, base_month, 1)
    special_class_departments = special_class_departments or []
    special_class_descriptions = special_class_descriptions or []

    logger.info(f"[INITIALIZE] Starting with {planning_logic} logic. Normalize existing: {normalize_existing}")

    if normalize_existing:
        normalize_planning_logic = 'date_based' if planning_logic == 'department' else 'description_based'
        normalize_result = normalize_existing_schedules(
            planning_logic=normalize_planning_logic, base_month=base_month,
            max_departments=max_departments, max_descriptions=max_descriptions,
            calibration_period=calibration_period,
            special_class_departments=special_class_departments,
            special_class_descriptions=special_class_descriptions,
        )
        logger.info(f"[INITIALIZE] Normalization result: {normalize_result}")

    existing_schedules = CalibrationSchedule.objects.filter(
        equipment__active_status=True
    ).select_related('equipment__department', 'equipment__description')

    existing_equipment_ids = set(existing_schedules.values_list('equipment_id', flat=True))

    # Build month usage maps
    month_dept_count = defaultdict(int)
    month_desc_count = defaultdict(int)
    dept_month_map, desc_month_map = {}, {}

    for sched in existing_schedules:
        month_key = sched.scheduled_month.replace(day=1)
        if sched.equipment and sched.equipment.department:
            dept_id = sched.equipment.department_id
            month_dept_count[month_key] += 1
            dept_month_map[dept_id] = month_key
        if sched.equipment and sched.equipment.description:
            desc_id = sched.equipment.description_id
            month_desc_count[month_key] += 1
            desc_month_map[desc_id] = month_key

    equipment_query = Equipment.objects.filter(active_status=True).select_related('department', 'description')
    if specific_equipment_ids:
        equipment_query = equipment_query.filter(id__in=specific_equipment_ids)
        if preserve_existing:
            equipment_query = equipment_query.exclude(id__in=existing_equipment_ids)
    elif preserve_existing:
        equipment_query = equipment_query.exclude(id__in=existing_equipment_ids)

    if planning_logic == 'description' and selected_descriptions:
        equipment_query = equipment_query.filter(description_id__in=selected_descriptions)

    equipment_list = equipment_query.order_by('department__name', 'description__name')
    logger.info(f"[INITIALIZE] Found {equipment_list.count()} equipment items to schedule.")

    # Group by department or description
    is_dept = planning_logic == 'department'
    groups = defaultdict(list)
    for equip in equipment_list:
        key = (equip.department_id if equip.department else 'no_dept') if is_dept else (equip.description_id if equip.description else 'no_desc')
        groups[key].append(equip)

    month_map = dept_month_map if is_dept else desc_month_map
    max_per_month = max_departments if is_dept else max_descriptions
    special_ids = [str(x) for x in (special_class_departments if is_dept else special_class_descriptions)]

    created_count = skipped_count = 0

    for group_id, equip_list in groups.items():
        is_special = str(group_id) in special_ids
        scheduled_month = _find_optimal_month(start_date, {}, group_id, month_map, max_per_month, is_special)

        for equip in equip_list:
            if not equip.active_status:
                continue
            if CalibrationSchedule.objects.filter(equipment_id=equip.id).exists():
                skipped_count += 1
                continue
            try:
                CalibrationSchedule.objects.create(
                    equipment=equip,
                    scheduled_month=scheduled_month,
                    status='pending',
                    calibration_period=calibration_period,
                    planning_logic=planning_logic,
                    generation_source='initialization',
                    active_status=True
                )
                created_count += 1
                logger.debug(
                    f"[INITIALIZE] Scheduled equipment {equip.id} "
                    f"({equip.description.name if equip.description else 'N/A'}) "
                    f"-> {scheduled_month.strftime('%B %Y')}"
                )
            except Exception as e:
                logger.error(f"[INITIALIZE] Failed for equipment {equip.id}: {e}")

        if is_dept:
            month_dept_count[scheduled_month] += len(equip_list)
        else:
            month_desc_count[scheduled_month] += len(equip_list)

    result = f"Created {created_count} new schedules with {planning_logic} logic. Skipped {skipped_count} (already scheduled)."
    logger.info(f"[INITIALIZE] {result}")
    return result


# ─── MAINTENANCE TASKS ────────────────────────────────────────────────────────

@shared_task
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


@shared_task
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


@shared_task
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


@shared_task
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


@shared_task
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


@shared_task
def bulk_update_calibration_periods(equipment_ids, new_period):
    """Bulk update calibration periods for specific equipment."""
    updated_count = CalibrationSchedule.objects.filter(
        equipment_id__in=equipment_ids, equipment__active_status=True
    ).update(calibration_period=new_period)
    result = f"Updated calibration period to {new_period} months for {updated_count} schedules."
    logger.info(f"[BULK_UPDATE] {result}")
    return result


@shared_task
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


@shared_task
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


# ─── RECONCILIATION TASK WRAPPERS ─────────────────────────────────────────────

def _log_wrap(tag, fn, kwargs, log_result=True):
    """Run fn(**kwargs), logging start and result under [TAG]."""
    logger.info(f"[{tag}] Starting")
    result = fn(**kwargs)
    if log_result:
        logger.info(f"[{tag}] Complete: {result}")
    return result


@shared_task
def run_calibration_reconciliation(planning_logic=None, fix_issues=True, dry_run=False, auto_reschedule=True, check_hours=1):
    """Celery wrapper for full_reconciliation(). Performs a complete reconciliation pass."""
    logger.info(f"[TASK:run_calibration_reconciliation] Starting (logic={planning_logic}, fix={fix_issues}, dry_run={dry_run}, check_hours={check_hours})")
    result = full_reconciliation(
        planning_logic=planning_logic or 'department',
        fix_issues=fix_issues, dry_run=dry_run, auto_reschedule=auto_reschedule,
    )
    logger.info(f"[TASK:run_calibration_reconciliation] Complete: {result}")
    return result


@shared_task
def auto_reschedule_completed_task(check_hours=0.5):
    """Celery wrapper for auto_reschedule_completed_calibrations()."""
    logger.info(f"[TASK:auto_reschedule_completed_task] Starting (check_hours={check_hours})")
    result = auto_reschedule_completed_calibrations(check_hours=check_hours)
    logger.info(
        f"[TASK:auto_reschedule_completed_task] Complete: "
        f"rescheduled={result.get('rescheduled', 0)}, "
        f"waiting={result.get('waiting_for_group', 0)}, "
        f"skipped={result.get('skipped', 0)}"
    )
    return result


@shared_task
def ensure_grouping_consistency_task(planning_logic=None, fix_misalignments=True, dry_run=False, calibration_period=12):
    """Celery wrapper for ensure_grouping_consistency(). Detects and fixes split groups."""
    logger.info(f"[TASK:ensure_grouping_consistency_task] Starting (logic={planning_logic}, fix={fix_misalignments}, dry_run={dry_run})")
    result = ensure_grouping_consistency(
        planning_logic=planning_logic or 'department',
        fix_misalignments=fix_misalignments, dry_run=dry_run, calibration_period=calibration_period,
    )
    logger.info(
        f"[TASK:ensure_grouping_consistency_task] Complete: "
        f"status={result.get('status')}, "
        f"alignment={result.get('alignment_percentage', 'n/a')}%, "
        f"split_groups={result.get('split_groups', 'n/a')}"
    )
    return result


@shared_task
def diagnose_calibration_schedules():
    """Celery wrapper for diagnose_schedules(). Daily read-only diagnostic pass."""
    logger.info("[TASK:diagnose_calibration_schedules] Starting daily diagnostics")
    result = diagnose_schedules()
    logger.info(f"[TASK:diagnose_calibration_schedules] Complete: {result}")
    return result
