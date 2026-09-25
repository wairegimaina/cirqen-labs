"""ppms.tasks.initialize — initialize PPM schedules with planning logic."""
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


@shared_task(name="ppms.tasks.initialize_ppm_schedule_with_logic", bind=True)
def initialize_ppm_schedule_with_logic(self, workshop_id, planning_logic='department',
                                       maintenance_period=6, base_month=1, base_year=None,
                                       max_departments=100, max_descriptions=100,
                                       selected_descriptions=None, preserve_existing=True,
                                       specific_equipment_ids=None):
    """
    Initialize PPM schedules with NORMALIZED grouping.

    KEY FEATURE: All equipment in the same group (department OR description)
    will be scheduled in the SAME month.

    - Department logic: All equipment in Department A -> Month X
    - Description logic: All equipment of Type B -> Month Y
    """
    try:
        logger.info(f"Starting NORMALIZED PPM initialization for workshop {workshop_id}")
        logger.info(f"Planning logic: {planning_logic} | Period: {maintenance_period} months")

        # Validate workshop exists
        try:
            workshop = Workshop.objects.get(id=workshop_id)
            logger.info(f"Workshop found: {workshop.name}")
        except Workshop.DoesNotExist:
            error_msg = f"Workshop with id {workshop_id} does not exist"
            logger.error(error_msg)
            return {'status': 'error', 'message': error_msg}

        from scheduling.planner import active_plan
        if active_plan(workshop_id, 'ppm'):
            msg = (f"{workshop.name} is scheduled by its scheduling plan; "
                   "change the plan instead of re-initializing")
            logger.warning(msg)
            return {'status': 'error', 'message': msg}

        # Set base year if not provided
        if base_year is None:
            base_year = date.today().year
            if date.today().month > 6:
                base_year += 1

        start_date = date(base_year, base_month, 1)

        # Track existing schedules. Retired rows (inactive or pending
        # deletion) don't count: equipment with only those is unscheduled.
        existing_schedules = PPMSchedule.objects.select_related(
            'equipment__department',
            'equipment__description'
        ).filter(workshop_id=workshop_id, active_status=True, pending_delete=False)

        existing_equipment_ids = set(existing_schedules.values_list('equipment_id', flat=True))
        logger.info(f"Found {len(existing_equipment_ids)} existing schedules")

        # Get equipment to schedule
        equipment_query = Equipment.objects.filter(
            workshop_id=workshop_id,
            active_status=True
        ).select_related('department', 'description')

        # Apply filters
        if specific_equipment_ids:
            equipment_query = equipment_query.filter(id__in=specific_equipment_ids)
            if preserve_existing:
                equipment_query = equipment_query.exclude(id__in=existing_equipment_ids)
        elif preserve_existing:
            equipment_query = equipment_query.exclude(id__in=existing_equipment_ids)

        if planning_logic == 'description' and selected_descriptions:
            equipment_query = equipment_query.filter(description_id__in=selected_descriptions)

        equipment_list = list(equipment_query.order_by('department__name', 'description__name'))
        total_equipment = len(equipment_list)

        logger.info(f"Found {total_equipment} active equipment items to schedule")

        if total_equipment == 0:
            msg = "No equipment found to schedule"
            logger.warning(msg)
            return {'status': 'success', 'message': msg, 'created': 0, 'skipped': 0}

        # KEY CHANGE: GROUP EQUIPMENT BY DEPARTMENT OR DESCRIPTION
        if planning_logic == 'description' or planning_logic == 'description_based':
            # Group all equipment by description
            grouped_equipment = defaultdict(list)
            for equip in equipment_list:
                desc_id = equip.description_id if equip.description else 'no_description'
                grouped_equipment[desc_id].append(equip)

            logger.info(f"Grouped into {len(grouped_equipment)} descriptions")
        else:
            # Group all equipment by department
            grouped_equipment = defaultdict(list)
            for equip in equipment_list:
                dept_id = equip.department_id if equip.department else 'no_department'
                grouped_equipment[dept_id].append(equip)

            logger.info(f"Grouped into {len(grouped_equipment)} departments")

        # ASSIGN EACH GROUP TO A MONTH (normalized scheduling)
        month_group_count = defaultdict(int)
        created_schedules = 0
        skipped_already_scheduled = 0
        current_month_offset = 0
        max_groups_per_month = max_departments if planning_logic == 'department' or planning_logic == 'date_based' else max_descriptions

        group_items = list(grouped_equipment.items())
        total_groups = len(group_items)

        logger.info(f"Processing {total_groups} groups...")

        for group_index, (group_id, equipment_in_group) in enumerate(group_items):
            # Update progress (only when running as a Celery task, not when
            # called directly by the daily auto-scheduler)
            if group_index % 5 == 0 and self.request.id:
                progress = int((group_index / total_groups) * 100)
                self.update_state(
                    state='PROGRESS',
                    meta={
                        'current': group_index,
                        'total': total_groups,
                        'percent': progress,
                        'status': f'Processing group {group_index + 1} of {total_groups} ({len(equipment_in_group)} items)'
                    }
                )

            # Find next available month for this entire group
            scheduled_month = None
            attempts = 0
            max_attempts = 60  # Try up to 5 years ahead

            while attempts < max_attempts:
                test_month = start_date + relativedelta(months=current_month_offset)

                # Check if this month has capacity for another group
                if month_group_count[test_month] < max_groups_per_month:
                    scheduled_month = test_month
                    month_group_count[test_month] += 1
                    break

                current_month_offset += 1
                attempts += 1

            if not scheduled_month:
                logger.warning(f"Could not find suitable month for group {group_id} within {max_attempts} months")
                scheduled_month = start_date + relativedelta(months=current_month_offset)
                current_month_offset += 1

            # Get group name for logging
            if planning_logic == 'description' or planning_logic == 'description_based':
                if group_id == 'no_description':
                    group_name = 'No Description'
                else:
                    desc = EquipmentDescription.objects.filter(id=group_id).first()
                    group_name = desc.name if desc else f'Desc {group_id}'
            else:
                if group_id == 'no_department':
                    group_name = 'No Department'
                else:
                    dept = Department.objects.filter(id=group_id).first()
                    group_name = dept.name if dept else f'Dept {group_id}'

            logger.info(f"Scheduling {len(equipment_in_group)} items from '{group_name}' -> {scheduled_month.strftime('%B %Y')}")

            # SCHEDULE ALL EQUIPMENT IN THIS GROUP TO THE SAME MONTH
            for equip in equipment_in_group:
                try:
                    # Double-check if equipment is already scheduled
                    if PPMSchedule.objects.filter(
                        equipment_id=equip.id, active_status=True, pending_delete=False
                    ).exists():
                        logger.debug(f"Equipment {equip.id} already scheduled, skipping")
                        skipped_already_scheduled += 1
                        continue

                    # Check for duplicate in target month
                    if PPMSchedule.objects.filter(
                        equipment=equip,
                        scheduled_month=scheduled_month
                    ).exists():
                        logger.warning(f"Duplicate schedule found for equipment {equip.id} in {scheduled_month}")
                        skipped_already_scheduled += 1
                        continue

                    PPMSchedule.objects.create(
                        workshop=workshop,
                        equipment=equip,
                        scheduled_month=scheduled_month,
                        status='pending',
                        maintenance_period=maintenance_period,
                        planning_logic=planning_logic if planning_logic in ['description', 'description_based', 'date_based'] else 'department',
                        generation_source='initialization'
                    )
                    created_schedules += 1

                except Exception as e:
                    logger.error(f"Failed to create PPM schedule for equipment {equip.id}: {e}", exc_info=True)
                    continue

            # Move to next month after this group (spread groups across months)
            if (group_index + 1) % max_groups_per_month == 0:
                current_month_offset += 1

        # Final result
        result_message = (
            f"Created {created_schedules} schedules for workshop {workshop.name} "
            f"using {planning_logic} logic. Grouped {total_groups} groups across months."
        )

        if skipped_already_scheduled > 0:
            result_message += f" Skipped {skipped_already_scheduled} already scheduled equipment."

        logger.info(f"PPM initialization completed: {result_message}")

        # Log month distribution
        logger.info("Month Distribution:")
        for month, count in sorted(month_group_count.items()):
            logger.info(f"  {month.strftime('%B %Y')}: {count} groups")

        return {
            'status': 'success',
            'message': result_message,
            'created': created_schedules,
            'skipped': skipped_already_scheduled,
            'total_processed': total_equipment,
            'groups_created': total_groups,
            'month_distribution': {
                month.strftime('%Y-%m'): count
                for month, count in month_group_count.items()
            }
        }

    except Exception as e:
        error_msg = f"Fatal error in PPM initialization: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {
            'status': 'error',
            'message': error_msg
        }
