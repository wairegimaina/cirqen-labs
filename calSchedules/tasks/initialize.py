"""calSchedules.tasks.initialize — initialize calibration schedules with logic."""
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
PROTECTED_SOURCES = ['signal', 'locker', 'job_card', 'plan']

# sibling modules in this package
from .helpers import _find_optimal_month, _planning_year
from .normalize import normalize_existing_schedules


@shared_task(name="calSchedules.tasks.initialize_calibration_schedule_with_logic")
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

    # Retired rows (inactive or pending deletion) don't make equipment scheduled.
    existing_equipment_ids = set(
        existing_schedules.filter(active_status=True, pending_delete=False)
        .values_list('equipment_id', flat=True)
    )

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

    # Workshops with a scheduling plan are placed by the plan, not here.
    from scheduling.planner import planned_workshop_ids
    equipment_query = Equipment.objects.filter(active_status=True).exclude(
        department__workshop_id__in=planned_workshop_ids('calibration')
    ).select_related('department', 'description')
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
            if CalibrationSchedule.objects.filter(
                equipment_id=equip.id, active_status=True, pending_delete=False
            ).exists():
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
