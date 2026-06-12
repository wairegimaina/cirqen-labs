# Updated tasks.py - PPM initialization with normalized grouping logic
# FIX: Removed 'department' parameter from PPMSchedule.objects.create() at line 198

from celery import shared_task
from Inventory.models import Equipment, Department, EquipmentDescription
from .models import PPMSchedule
from workshop.models import Workshop
from django.utils.timezone import now
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


def _get_ppm_group_key(schedule, planning_logic):
    """Generate a unique group key for PPM schedules based on planning logic."""
    if planning_logic == 'description' or planning_logic == 'description_based':
        group_id = schedule.equipment.description_id if schedule.equipment and schedule.equipment.description else 'no_description'
    else:
        group_id = schedule.equipment.department_id if schedule.equipment and schedule.equipment.department else 'no_department'

    month_key = schedule.scheduled_month.strftime('%Y-%m') if schedule.scheduled_month else 'unknown'
    return f"{planning_logic}_{group_id}_{month_key}"


def _get_ppm_group_members(schedule, planning_logic):
    """Get all members of the same PPM group for a given schedule."""
    if planning_logic == 'description' or planning_logic == 'description_based':
        group_id = schedule.equipment.description_id if schedule.equipment else None
        members = PPMSchedule.objects.filter(
            equipment__description_id=group_id,
            scheduled_month=schedule.scheduled_month,
            active_status=True
        ).select_related('equipment')
    else:
        group_id = schedule.equipment.department_id if schedule.equipment else None
        members = PPMSchedule.objects.filter(
            equipment__department_id=group_id,
            scheduled_month=schedule.scheduled_month,
            active_status=True
        ).select_related('equipment')
    return members


@shared_task(bind=True)
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

        # Set base year if not provided
        if base_year is None:
            base_year = date.today().year
            if date.today().month > 6:
                base_year += 1

        start_date = date(base_year, base_month, 1)

        # Track existing schedules
        existing_schedules = PPMSchedule.objects.select_related(
            'equipment__department',
            'equipment__description'
        ).filter(workshop_id=workshop_id)

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
            # Update progress
            if group_index % 5 == 0:
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
                    if PPMSchedule.objects.filter(equipment_id=equip.id).exists():
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


@shared_task
def check_and_push_overdue_ppms():
    """
    Enhanced version: Only pushes schedules for ACTIVE equipment.
    Deletes schedules for inactive equipment that became overdue.
    """
    today = date.today()

    # Get all overdue pending schedules
    overdue = PPMSchedule.objects.select_related('equipment').filter(
        scheduled_month__lt=today,
        status='pending'
    )

    pushed_count = 0
    deleted_inactive_count = 0

    for sched in overdue:
        # Check if equipment is still active
        if sched.equipment and sched.equipment.active_status:
            # Equipment is active - push the schedule forward
            sched.scheduled_month = sched.scheduled_month + timedelta(days=30)
            sched.status = 'pushed'
            sched.save()
            pushed_count += 1
            logger.info(f"Pushed overdue schedule for active equipment {sched.equipment.id}")
        else:
            # Equipment is inactive - delete the schedule
            equipment_id = sched.equipment.id if sched.equipment else 'Unknown'
            sched.delete()
            deleted_inactive_count += 1
            logger.info(f"Deleted overdue schedule for inactive equipment {equipment_id}")

    result = f"Pushed {pushed_count} overdue schedules for active equipment."
    if deleted_inactive_count > 0:
        result += f" Deleted {deleted_inactive_count} schedules for inactive equipment."

    return result


def _get_group_key_ppm(schedule, planning_logic):
    """Generate a unique group key for PPM schedules."""
    if planning_logic == 'description' or planning_logic == 'description_based':
        group_id = schedule.equipment.description_id if schedule.equipment and schedule.equipment.description else 'no_description'
    else:
        group_id = schedule.equipment.department_id if schedule.equipment and schedule.equipment.department else 'no_department'

    month_key = schedule.scheduled_month.strftime('%Y-%m') if schedule.scheduled_month else 'unknown'
    return f"{planning_logic}_{group_id}_{month_key}"


def _get_ppm_group_members(schedule, planning_logic):
    """Get all members of the same PPM group for a given schedule."""
    if planning_logic == 'description' or planning_logic == 'description_based':
        group_id = schedule.equipment.description_id if schedule.equipment else None
        members = PPMSchedule.objects.filter(
            equipment__description_id=group_id,
            scheduled_month=schedule.scheduled_month,
            active_status=True
        ).select_related('equipment')
    else:
        group_id = schedule.equipment.department_id if schedule.equipment else None
        members = PPMSchedule.objects.filter(
            equipment__department_id=group_id,
            scheduled_month=schedule.scheduled_month,
            active_status=True
        ).select_related('equipment')
    return members


@shared_task
def validate_ppm_schedules(workshop_id):
    """
    Validate PPM schedules and fix common issues.
    NEW: Check for groups that are split across different months
    """
    try:
        workshop = Workshop.objects.get(id=workshop_id)
        issues_fixed = 0

        # Remove schedules for inactive equipment
        inactive_equipment_schedules = PPMSchedule.objects.filter(
            workshop_id=workshop_id,
            equipment__active_status=False
        )
        inactive_count = inactive_equipment_schedules.count()
        if inactive_count > 0:
            inactive_equipment_schedules.delete()
            issues_fixed += inactive_count
            logger.info(f"Deleted {inactive_count} schedules for inactive equipment")

        # Find duplicate schedules
        from django.db.models import Count
        duplicates = PPMSchedule.objects.filter(
            workshop_id=workshop_id,
            equipment__active_status=True
        ).values('equipment_id').annotate(
            count=Count('equipment_id')
        ).filter(count__gt=1)

        for duplicate in duplicates:
            equipment_id = duplicate['equipment_id']
            schedules = PPMSchedule.objects.filter(
                workshop_id=workshop_id,
                equipment_id=equipment_id
            ).order_by('scheduled_month')

            if schedules.count() > 1:
                schedules_to_delete = schedules[1:]
                for schedule in schedules_to_delete:
                    logger.info(f"Deleting duplicate schedule {schedule.id}")
                    schedule.delete()
                    issues_fixed += 1

        # Check for split groups (department/description split across months)
        logger.info("Checking for split groups...")

        # Check department-based schedules
        dept_schedules = PPMSchedule.objects.filter(
            workshop_id=workshop_id,
            planning_logic='date_based',
            equipment__active_status=True
        ).select_related('equipment__department')

        dept_month_map = defaultdict(lambda: defaultdict(list))
        for sched in dept_schedules:
            if sched.equipment.department:
                dept_id = sched.equipment.department_id
                month_key = sched.scheduled_month.strftime('%Y-%m')
                dept_month_map[dept_id][month_key].append(sched)

        for dept_id, months in dept_month_map.items():
            if len(months) > 1:
                dept_name = Department.objects.get(id=dept_id).name
                logger.warning(f"Department '{dept_name}' is split across {len(months)} months!")
                # Could add auto-fix logic here if desired

        return f"Fixed {issues_fixed} scheduling issues in workshop {workshop.name}"

    except Workshop.DoesNotExist:
        return f"Error: Workshop {workshop_id} not found"
    except Exception as e:
        logger.error(f"Error in validate_ppm_schedules: {e}")
        return f"Error validating schedules: {str(e)}"


@shared_task
def periodic_cleanup_inactive_schedules():
    """Automatically run cleanup of PPM schedules for inactive equipment"""
    try:
        inactive_schedules = PPMSchedule.objects.select_related('equipment').filter(
            equipment__active_status=False
        )

        count = inactive_schedules.count()

        if count > 0:
            equipment_ids = list(inactive_schedules.values_list('equipment_id', flat=True))
            inactive_schedules.delete()

            logger.info(
                f"Periodic cleanup: Removed {count} PPM schedule(s) for inactive equipment. "
                f"Equipment IDs: {equipment_ids[:10]}{'...' if len(equipment_ids) > 10 else ''}"
            )
            return f"Cleaned up {count} schedules for inactive equipment"
        else:
            logger.debug("Periodic cleanup: No inactive equipment schedules found")
            return "No inactive equipment schedules found"

    except Exception as e:
        logger.error(f"Error in periodic_cleanup_inactive_schedules: {e}")
        return f"Error during cleanup: {str(e)}"


@shared_task
def auto_schedule_unscheduled_equipment(planning_logic='department',
                                       maintenance_period=6,
                                       max_departments=100,
                                       max_descriptions=100):
    """
    Automatically schedule all unscheduled equipment across all workshops.
    Runs daily via Celery Beat to catch any equipment that was added but not scheduled.

    This task:
    1. Finds all active workshops
    2. For each workshop, finds unscheduled active equipment
    3. Schedules them using the initialize_ppm_schedule_with_logic task

    Args:
        planning_logic: 'department' or 'description'
        maintenance_period: Months between maintenance (3, 6, 9, or 12)
        max_departments: Max departments per month
        max_descriptions: Max descriptions per month

    Returns:
        dict: Summary of scheduling results
    """
    try:
        logger.info("="*80)
        logger.info("AUTO-SCHEDULE UNSCHEDULED EQUIPMENT - STARTING")
        logger.info(f"Planning Logic: {planning_logic} | Period: {maintenance_period} months")
        logger.info("="*80)

        from workshop.models import Workshop
        from datetime import date

        # Get all active workshops
        workshops = Workshop.objects.filter(active_status=True)
        total_workshops = workshops.count()

        if total_workshops == 0:
            logger.warning("No active workshops found")
            return {
                'status': 'success',
                'message': 'No active workshops found',
                'workshops_processed': 0,
                'equipment_scheduled': 0
            }

        logger.info(f"Found {total_workshops} active workshops")

        # Track results
        results = {
            'workshops_processed': 0,
            'workshops_with_unscheduled': 0,
            'total_equipment_scheduled': 0,
            'total_equipment_skipped': 0,
            'workshop_details': {},
            'errors': []
        }

        # Process each workshop
        for workshop in workshops:
            try:
                logger.info(f"{'-'*60}")
                logger.info(f"Processing workshop: {workshop.name} (ID: {workshop.id})")

                # Find unscheduled equipment in this workshop
                scheduled_equipment_ids = PPMSchedule.objects.filter(
                    workshop_id=workshop.id,
                    equipment__active_status=True
                ).values_list('equipment_id', flat=True)

                unscheduled_equipment_ids = list(
                    Equipment.objects.filter(
                        workshop_id=workshop.id,
                        active_status=True
                    ).exclude(
                        id__in=scheduled_equipment_ids
                    ).values_list('id', flat=True)
                )

                unscheduled_count = len(unscheduled_equipment_ids)

                logger.info(f"  Scheduled equipment: {len(scheduled_equipment_ids)}")
                logger.info(f"  Unscheduled equipment: {unscheduled_count}")

                if unscheduled_count == 0:
                    logger.info(f"  No unscheduled equipment in {workshop.name}")
                    results['workshops_processed'] += 1
                    results['workshop_details'][workshop.name] = {
                        'unscheduled': 0,
                        'scheduled': 0,
                        'status': 'no_action_needed'
                    }
                    continue

                # Workshop has unscheduled equipment - schedule it
                logger.info(f"  Scheduling {unscheduled_count} equipment items...")
                results['workshops_with_unscheduled'] += 1

                # Determine base month/year (start from next month)
                today = date.today()
                base_month = today.month + 1
                base_year = today.year

                if base_month > 12:
                    base_month = 1
                    base_year += 1

                # Call the main initialization task
                # Use direct call instead of .delay() to run synchronously
                result = initialize_ppm_schedule_with_logic(
                    None,  # self parameter (not needed when called directly)
                    str(workshop.id),
                    planning_logic=planning_logic,
                    maintenance_period=maintenance_period,
                    base_month=base_month,
                    base_year=base_year,
                    max_departments=max_departments,
                    max_descriptions=max_descriptions,
                    selected_descriptions=None,
                    preserve_existing=True,  # Don't overwrite existing schedules
                    specific_equipment_ids=unscheduled_equipment_ids  # Only schedule unscheduled
                )

                if result['status'] == 'success':
                    logger.info(f"  Successfully scheduled {result['created']} equipment")
                    results['total_equipment_scheduled'] += result['created']
                    results['total_equipment_skipped'] += result.get('skipped', 0)
                    results['workshop_details'][workshop.name] = {
                        'unscheduled': unscheduled_count,
                        'scheduled': result['created'],
                        'skipped': result.get('skipped', 0),
                        'status': 'success'
                    }
                else:
                    logger.error(f"  Failed to schedule: {result.get('message', 'Unknown error')}")
                    results['errors'].append({
                        'workshop': workshop.name,
                        'error': result.get('message', 'Unknown error')
                    })
                    results['workshop_details'][workshop.name] = {
                        'unscheduled': unscheduled_count,
                        'scheduled': 0,
                        'status': 'error',
                        'error': result.get('message')
                    }

                results['workshops_processed'] += 1

            except Exception as e:
                error_msg = f"Error processing workshop {workshop.name}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                results['errors'].append({
                    'workshop': workshop.name,
                    'error': str(e)
                })
                continue

        # Generate summary
        logger.info("="*80)
        logger.info("AUTO-SCHEDULE SUMMARY")
        logger.info("="*80)
        logger.info(f"Workshops processed: {results['workshops_processed']}/{total_workshops}")
        logger.info(f"Workshops with unscheduled equipment: {results['workshops_with_unscheduled']}")
        logger.info(f"Total equipment scheduled: {results['total_equipment_scheduled']}")
        logger.info(f"Total equipment skipped: {results['total_equipment_skipped']}")

        if results['errors']:
            logger.warning(f"Errors encountered: {len(results['errors'])}")
            for error in results['errors']:
                logger.warning(f"  - {error['workshop']}: {error['error']}")

        # Detailed workshop breakdown
        logger.info("Workshop Details:")
        for workshop_name, details in results['workshop_details'].items():
            if details['status'] == 'success':
                logger.info(
                    f"  {workshop_name}: "
                    f"Scheduled {details['scheduled']}/{details['unscheduled']} equipment"
                )
            elif details['status'] == 'no_action_needed':
                logger.info(f"  {workshop_name}: No unscheduled equipment")
            else:
                logger.error(f"  {workshop_name}: {details.get('error', 'Failed')}")

        logger.info("="*80)

        # Build result message
        message = (
            f"Auto-scheduling completed: "
            f"Processed {results['workshops_processed']} workshops, "
            f"scheduled {results['total_equipment_scheduled']} equipment items"
        )

        if results['errors']:
            message += f" with {len(results['errors'])} errors"

        return {
            'status': 'success',
            'message': message,
            **results
        }

    except Exception as e:
        error_msg = f"Fatal error in auto_schedule_unscheduled_equipment: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {
            'status': 'error',
            'message': error_msg
        }


@shared_task
def generate_unscheduled_equipment_report():
    """
    Generate a report of unscheduled equipment across all workshops.
    Useful for monitoring and alerting.

    Can be scheduled to run before auto_schedule_unscheduled_equipment
    to send notifications if there's unscheduled equipment.
    """
    try:
        from workshop.models import Workshop

        logger.info("Generating unscheduled equipment report")

        workshops = Workshop.objects.filter(active_status=True)
        report = {
            'timestamp': date.today().isoformat(),
            'total_workshops': workshops.count(),
            'workshops_with_unscheduled': 0,
            'total_unscheduled': 0,
            'workshop_breakdown': {}
        }

        for workshop in workshops:
            scheduled_ids = set(
                PPMSchedule.objects.filter(
                    workshop_id=workshop.id,
                    equipment__active_status=True
                ).values_list('equipment_id', flat=True)
            )

            all_equipment = Equipment.objects.filter(
                workshop_id=workshop.id,
                active_status=True
            )

            total_count = all_equipment.count()
            scheduled_count = len(scheduled_ids)
            unscheduled_count = total_count - scheduled_count

            if unscheduled_count > 0:
                report['workshops_with_unscheduled'] += 1
                report['total_unscheduled'] += unscheduled_count

                # Get unscheduled equipment details
                unscheduled = all_equipment.exclude(id__in=scheduled_ids).select_related(
                    'department', 'description'
                )

                report['workshop_breakdown'][workshop.name] = {
                    'total_equipment': total_count,
                    'scheduled': scheduled_count,
                    'unscheduled': unscheduled_count,
                    'coverage_percent': round((scheduled_count / total_count * 100), 2) if total_count > 0 else 0,
                    'unscheduled_items': [
                        {
                            'id': eq.id,
                            'description': eq.description.name if eq.description else 'No Description',
                            'serial_number': eq.serial_number or 'N/A',
                            'department': eq.department.name if eq.department else 'No Department'
                        }
                        for eq in unscheduled[:10]  # Limit to first 10 for brevity
                    ]
                }

        logger.info(f"Report generated: {report['total_unscheduled']} unscheduled equipment across {report['workshops_with_unscheduled']} workshops")

        return report

    except Exception as e:
        logger.error(f"Error generating report: {e}", exc_info=True)
        return {'error': str(e)}


@shared_task
def cleanup_orphaned_ppm_schedules():
    """
    Clean up PPM schedules that are orphaned or mismatched.
    - Schedules for deleted equipment
    - Schedules where workshop doesn't match equipment's workshop
    """
    try:
        logger.info("Starting PPM schedule cleanup")

        # Find schedules with null equipment
        orphaned_equipment = PPMSchedule.objects.filter(equipment__isnull=True)
        orphaned_count = orphaned_equipment.count()
        if orphaned_count > 0:
            orphaned_equipment.delete()
            logger.info(f"  Deleted {orphaned_count} schedules with null equipment")

        # Find mismatched workshops
        from django.db.models import F
        mismatched = PPMSchedule.objects.exclude(
            workshop_id=F('equipment__workshop_id')
        ).select_related('equipment', 'workshop')

        fixed_count = 0
        for schedule in mismatched:
            if schedule.equipment:
                logger.warning(
                    f"  Fixing mismatched schedule {schedule.id}: "
                    f"PPM workshop={schedule.workshop.name}, "
                    f"Equipment workshop={schedule.equipment.workshop.name}"
                )
                schedule.workshop_id = schedule.equipment.workshop_id
                schedule.save()
                fixed_count += 1

        result = f"Cleanup complete: Deleted {orphaned_count} orphaned schedules, fixed {fixed_count} mismatches"
        logger.info(f"{result}")
        return result

    except Exception as e:
        logger.error(f"Error in cleanup: {e}", exc_info=True)
        return f"Error: {str(e)}"


@shared_task
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

@shared_task
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
