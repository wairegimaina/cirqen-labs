"""ppms.tasks.normalize — normalize and smart-reorganize PPM schedules."""
from celery import shared_task
from Inventory.models import Equipment, Department, EquipmentDescription
from ..models import PPMSchedule
from workshop.models import Workshop
from django.utils.timezone import now
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import logging
logger = logging.getLogger(__name__)


@shared_task(name="ppms.tasks.smart_reorganize_ppm_schedules")
def smart_reorganize_ppm_schedules(
    new_planning_logic='department',
    base_month=1,
    max_departments=100,
    max_descriptions=100,
    workshop_id=None,
    dry_run=False
):
    """
    SMART REORGANIZER for PPM: safely re-normalize schedules when planning logic changes.

    This is the PPM equivalent of calSchedules' smart_reorganize_on_logic_change.
    It detects mismatched logic, emits warnings, and moves normalizable schedules.

    Returns:
        dict with status, message, reorganized count, warnings, etc.
    """
    today = date.today()
    if today.month > 6:
        base_year = today.year + 1
    else:
        base_year = today.year

    start_date = date(base_year, base_month, 1)
    end_date = date(base_year, 12, 31)

    logger.info("=" * 80)
    logger.info(
        f"[PPM_SMART_REORG] {'DRY RUN - ' if dry_run else ''}Starting smart reorganization "
        f"for logic change -> '{new_planning_logic}' in {base_year}"
    )
    logger.info("=" * 80)

    # Find schedules that NEED reorganizing (different logic from target)
    mismatched = PPMSchedule.objects.filter(
        active_status=True,
        status__in=['pending', 'pushed'],
        is_locked=False,
        generation_source__in=['manual', 'normalization', 'initialization', 'bulk_import'],
        scheduled_month__gte=start_date,
        scheduled_month__lte=end_date,
    ).exclude(
        planning_logic=new_planning_logic
    ).select_related('equipment__department', 'equipment__description').order_by('id')

    if workshop_id:
        mismatched = mismatched.filter(workshop_id=workshop_id)

    # Workshops with a scheduling plan are placed by the plan, not repacked.
    from scheduling.planner import planned_workshop_ids
    mismatched = mismatched.exclude(workshop_id__in=planned_workshop_ids('ppm'))

    total_to_reorg = mismatched.count()

    # Count protected schedules
    protected_completed = PPMSchedule.objects.filter(
        active_status=True, status='completed',
        scheduled_month__year=base_year,
    )
    if workshop_id:
        protected_completed = protected_completed.filter(workshop_id=workshop_id)

    logger.info(
        f"[PPM_SMART_REORG] Found {total_to_reorg} schedules to reorganize. "
        f"Protected (will NOT be changed): {protected_completed.count()} completed."
    )

    if total_to_reorg == 0:
        result = {
            'status': 'nothing_to_do',
            'message': (
                f"All PPM schedules already use '{new_planning_logic}' logic "
                f"or are protected. No reorganization needed."
            ),
            'reorganized': 0,
            'warnings': [],
            'dry_run': dry_run,
        }
        logger.info(f"[PPM_SMART_REORG] {result['message']}")
        return result

    # Emit warnings for each affected schedule
    warnings_list = []
    for sched in mismatched:
        old_logic = sched.planning_logic or 'not_set'
        equip = sched.equipment
        group_label = (
            f"Department: {equip.department.name}" if equip and equip.department else "No Department"
        ) if new_planning_logic == 'department' else (
            f"Description: {equip.description.name}" if equip and equip.description else "No Description"
        )
        warning_msg = (
            f"PPM SCHEDULE {sched.id} - Equipment '{equip.description if equip else 'N/A'}' "
            f"({group_label}): "
            f"logic '{old_logic}' -> '{new_planning_logic}'. "
            f"Current scheduled_month: {sched.scheduled_month.strftime('%B %Y')} "
            f"(will be moved to new group month under {new_planning_logic})."
        )
        warnings_list.append(warning_msg)
        logger.warning(f"[PPM_SMART_REORG] {warning_msg}")

    logger.warning(
        f"[PPM_SMART_REORG] {len(warnings_list)} schedules will be reorganized. "
        f"{'DRY RUN - no changes saved.' if dry_run else 'Proceeding with reorganization...'}"
    )

    if dry_run:
        return {
            'status': 'dry_run',
            'message': f"DRY RUN: {total_to_reorg} PPM schedules would be reorganized.",
            'reorganized': 0,
            'would_reorganize': total_to_reorg,
            'warnings': warnings_list,
            'dry_run': True,
        }

    # Perform normalization with new logic
    logger.info("[PPM_SMART_REORG] Calling normalize_ppm_schedules with new logic...")

    normalize_result = normalize_ppm_schedules(
        planning_logic=new_planning_logic,
        base_month=base_month,
        max_departments=max_departments,
        max_descriptions=max_descriptions,
        workshop_id=workshop_id,
    )

    logger.info(f"[PPM_SMART_REORG] Normalize result: {normalize_result}")

    # Clear logic_change_warning on successfully moved schedules
    cleared_count = 0
    for sched in mismatched:
        sched.refresh_from_db()
        if sched.planning_logic == new_planning_logic and sched.logic_change_warning:
            sched.logic_change_warning = ''
            sched.previous_planning_logic = ''
            sched.save(update_fields=['logic_change_warning', 'previous_planning_logic', 'needs_sync'])
            cleared_count += 1

    logger.info(
        f"[PPM_SMART_REORG] Reorganization complete. "
        f"Aligned {cleared_count} schedules."
    )
    logger.info("=" * 80)

    return {
        'status': 'complete',
        'message': str(normalize_result),
        'reorganized': cleared_count,
        'warnings': warnings_list,
        'protected': {
            'completed': protected_completed.count(),
        },
        'dry_run': False,
    }


@shared_task(name="ppms.tasks.normalize_ppm_schedules")
def normalize_ppm_schedules(
    planning_logic='department',
    maintenance_period=6,
    base_month=1,
    max_departments=100,
    max_descriptions=100,
    workshop_id=None
):
    """
    Normalize PPM schedules so every group (department OR description)
    is scheduled in the SAME month.

    THREE-LAYER PROTECTION (mirrors calibration normalization):
      1. Never touch completed schedules.
      2. Only normalize within the active planning year (auto-selected).
      3. Pending-delete schedules are skipped.

    The target year is automatically determined:
      - current month <= 6  -> current year
      - current month > 6   -> next year

    Args:
        planning_logic (str): 'department' or 'description'
        maintenance_period (int): PPM interval in months (3, 6, 9, 12)
        base_month (int): Starting month (1-12)
        max_departments (int): Max department groups per month
        max_descriptions (int): Max description groups per month
        workshop_id: Limit to a specific workshop UUID (None = all workshops)

    Returns:
        str: Human-readable summary of the normalization run.
    """
    from calendar import monthrange

    today = date.today()
    if today.month > 6:
        base_year = today.year + 1
    else:
        base_year = today.year

    start_date = date(base_year, base_month, 1)
    end_date = date(base_year, 12, 31)

    logger.info(
        f"[PPM_NORMALIZE] Starting normalization for {base_year} | "
        f"logic={planning_logic} | period={maintenance_period}m | "
        f"workshop={workshop_id or 'ALL'}"
    )

    # Base queryset: only normalizable, within the target year
    # THREE-LAYER PROTECTION (mirrors calibration normalization):
    #   1. Never touch completed schedules (status != 'completed').
    #   2. Never touch locked schedules (is_locked=False).
    #   3. Never touch signal/locker/auto_advance schedules.
    base_qs = PPMSchedule.objects.filter(
        status__in=['pending', 'pushed'],
        is_locked=False,
        generation_source__in=['manual', 'normalization', 'initialization', 'bulk_import'],
        active_status=True,
        scheduled_month__gte=start_date,
        scheduled_month__lte=end_date,
        equipment__active_status=True,
    ).select_related('equipment__department', 'equipment__description')

    if workshop_id:
        base_qs = base_qs.filter(workshop_id=workshop_id)

    # Workshops with a scheduling plan are placed by the plan, not repacked.
    from scheduling.planner import planned_workshop_ids
    base_qs = base_qs.exclude(workshop_id__in=planned_workshop_ids('ppm'))

    if not base_qs.exists():
        msg = f"[PPM_NORMALIZE] No normalizable PPM schedules found for {base_year}."
        logger.info(msg)
        return msg

    # Count protected (completed) for the report
    protected_count = PPMSchedule.objects.filter(
        status='completed',
        scheduled_month__year=base_year,
    ).count()

    normalized_count = 0
    skipped_count = 0
    duplicates_removed = 0
    max_groups_per_month = max_departments if planning_logic == 'department' else max_descriptions

    # GROUP BY department or description
    grouped = defaultdict(list)
    for schedule in base_qs:
        equip = schedule.equipment
        if planning_logic == 'department' or planning_logic == 'date_based':
            key = equip.department_id if equip.department else 'no_department'
        else:
            key = equip.description_id if equip.description else 'no_description'
        grouped[key].append(schedule)

    sorted_keys = sorted(grouped.keys(), key=lambda x: (x in ('no_department', 'no_description'), str(x)))

    month_group_count = defaultdict(int)  # {date: number_of_groups_assigned}

    for group_id in sorted_keys:
        group_schedules = grouped[group_id]

        # Find the next available month for this group
        month_offset = 0
        target_month = None
        while month_offset < 60:
            candidate = start_date + relativedelta(months=month_offset)
            if candidate > end_date:
                break
            if month_group_count[candidate] < max_groups_per_month:
                target_month = candidate
                month_group_count[candidate] += 1
                break
            month_offset += 1

        if target_month is None:
            # Fallback: just use next available slot even beyond year end
            target_month = start_date + relativedelta(months=month_offset)
            month_group_count[target_month] += 1

        # Log group assignment
        if planning_logic == 'department' or planning_logic == 'date_based':
            dept = Department.objects.filter(id=group_id).first()
            group_name = dept.name if dept else str(group_id)
        else:
            desc = EquipmentDescription.objects.filter(id=group_id).first()
            group_name = desc.name if desc else str(group_id)

        logger.info(
            f"[PPM_NORMALIZE] '{group_name}' -> {target_month.strftime('%B %Y')} "
            f"({len(group_schedules)} schedules)"
        )

        for schedule in group_schedules:
            # Safety re-check: three-layer protection
            if schedule.status == 'completed':
                skipped_count += 1
                continue
            if schedule.is_locked:
                skipped_count += 1
                continue
            if schedule.generation_source in ['signal', 'locker', 'auto_advance']:
                skipped_count += 1
                continue

            # Already in the right month with right config?
            if (schedule.scheduled_month == target_month
                    and schedule.maintenance_period == maintenance_period):
                continue

            # Resolve conflicts at target month for this equipment
            if schedule.scheduled_month != target_month:
                conflict = PPMSchedule.objects.filter(
                    equipment=schedule.equipment,
                    scheduled_month=target_month,
                ).exclude(id=schedule.id).first()

                if conflict:
                    if conflict.status == 'completed':
                        logger.warning(
                            f"[PPM_NORMALIZE] Cannot move schedule {schedule.id} "
                            f"completed schedule already at target. Skipping."
                        )
                        skipped_count += 1
                        continue
                    if conflict.generation_source in ['signal', 'locker', 'auto_advance']:
                        logger.warning(
                            f"[PPM_NORMALIZE] Cannot move schedule {schedule.id} "
                            f"system-created schedule at target. Skipping."
                        )
                        skipped_count += 1
                        continue
                    # Safe to remove the conflicting normalizable duplicate
                    logger.warning(
                        f"[PPM_NORMALIZE] Removing duplicate schedule "
                        f"{conflict.id} (status={conflict.status}, source={conflict.generation_source})"
                    )
                    conflict.delete()
                    duplicates_removed += 1

            try:
                schedule.scheduled_month = target_month
                schedule.maintenance_period = maintenance_period
                schedule.generation_source = 'normalization'
                schedule.needs_sync = True
                schedule.save(update_fields=[
                    'scheduled_month',
                    'maintenance_period',
                    'generation_source',
                    'needs_sync',
                ])
                normalized_count += 1
            except Exception as exc:
                logger.error(
                    f"[PPM_NORMALIZE] Failed to normalize schedule {schedule.id}: {exc}"
                )
                skipped_count += 1

    result = (
        f"[PPM_NORMALIZE] Normalized {normalized_count} PPM schedules "
        f"using '{planning_logic}' logic for {base_year}. "
        f"{len(sorted_keys)} group(s) processed. "
        f"Protected: {protected_count} completed schedules untouched. "
        f"Duplicates removed: {duplicates_removed}. "
        f"Skipped: {skipped_count}."
    )
    logger.info(result)
    return result
