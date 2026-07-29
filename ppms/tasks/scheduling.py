"""ppms.tasks.scheduling — schedule unscheduled equipment + overdue push."""
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

# sibling modules in this package
from .initialize import initialize_ppm_schedule_with_logic


@shared_task(name="ppms.tasks.check_and_push_overdue_ppms")
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


@shared_task(name="ppms.tasks.auto_schedule_unscheduled_equipment")
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


@shared_task(name="ppms.tasks.generate_unscheduled_equipment_report")
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
