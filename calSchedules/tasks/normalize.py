"""calSchedules.tasks.normalize — normalize and smart-reorganize on logic change."""
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
from .helpers import _apply_schedule_update, _find_optimal_month, _is_protected, _planning_year


@shared_task(name="calSchedules.tasks.normalize_existing_schedules")
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


@shared_task(name="calSchedules.tasks.smart_reorganize_on_logic_change")
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

    # Special-class groups ignore the per-month cap (all land on fallback_start).
    special_ids = [str(x) for x in (special_class_departments if is_dept else special_class_descriptions)]

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
                    is_special=str(gid) in special_ids
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
