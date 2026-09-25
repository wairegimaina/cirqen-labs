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
    Report overdue PPM schedules. Nothing is moved or deleted.

    This used to add 30 days to every overdue schedule, which knocked
    ``scheduled_month`` off the 1st (1 Jan + 30 days is still 31 Jan; 1 Feb
    becomes 3 Mar) and broke every query that matches a group by month. It
    also deleted schedules of inactive equipment. A due month is now
    permanent: overdue is worked out from the month (``PPMSchedule.is_overdue``)
    and shown as such until the work is done. Schedules of inactive equipment
    are retired by ``periodic_cleanup_inactive_schedules``.

    The task name is kept so the existing beat entry keeps working.
    """
    month_start = date.today().replace(day=1)
    overdue = PPMSchedule.open_schedules().filter(
        scheduled_month__lt=month_start,
        equipment__active_status=True,
    ).count()
    result = f"{overdue} overdue PPM schedule(s) (left in their due month)."
    logger.info(result)
    return result


def _resume_ppm_chains(equipment_ids):
    """Give equipment whose schedule chain broke its next schedule.

    ``equipment_ids`` have completed history but no open schedule, usually
    because their group waited on a member that was never completed. Each
    resumes from its own latest completed schedule, one maintenance period on
    and stepped forward by whole periods to this month or later, so it stays
    in its group's months (see ``calSchedules.grouping.resume_month``).

    Returns the number of equipment rescheduled.
    """
    from django.db import transaction
    from calSchedules.grouping import resume_month

    this_month = date.today().replace(day=1)
    latest = {}
    completed = PPMSchedule.objects.filter(
        equipment_id__in=equipment_ids, status='completed', pending_delete=False,
    ).select_related('equipment').order_by('equipment_id', '-scheduled_month')
    for sched in completed:
        latest.setdefault(sched.equipment_id, sched)

    resumed = 0
    for equipment_id, last in latest.items():
        period = last.maintenance_period or 6
        target = resume_month(last.scheduled_month, period, this_month)
        try:
            with transaction.atomic():
                # A retired, never-completed row can already sit in that
                # month (unique per equipment and month): bring it back.
                stale = PPMSchedule.objects.filter(
                    equipment_id=equipment_id, scheduled_month=target,
                ).exclude(status='completed').first()
                if stale:
                    stale.status = 'pending'
                    stale.active_status = True
                    stale.pending_delete = False
                    stale.generation_source = 'signal'
                    stale.parent_schedule = last
                    stale.save()
                else:
                    PPMSchedule.objects.create(
                        equipment=last.equipment,
                        workshop=last.workshop,
                        scheduled_month=target,
                        status='pending',
                        maintenance_period=period,
                        planning_logic=last.planning_logic or 'department',
                        generation_source='signal',
                        parent_schedule=last,
                        expected_maintenance_date=target,
                        active_status=True,
                    )
            resumed += 1
            logger.info(
                f"[PPM_RESUME] Equipment {equipment_id}: last completed "
                f"{last.scheduled_month.strftime('%B %Y')} + {period}m -> {target.strftime('%B %Y')}"
            )
        except Exception as exc:
            logger.error(f"[PPM_RESUME] Could not reschedule equipment {equipment_id}: {exc}")
    return resumed


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
            'total_equipment_resumed': 0,
            'total_equipment_skipped': 0,
            'workshop_details': {},
            'errors': []
        }

        from scheduling.planner import active_plan, schedule as schedule_by_plan

        # Process each workshop
        for workshop in workshops:
            try:
                logger.info(f"{'-'*60}")
                logger.info(f"Processing workshop: {workshop.name} (ID: {workshop.id})")

                # Workshops with a scheduling plan: the plan places every
                # unscheduled device, broken chains included.
                plan = active_plan(workshop.id, 'ppm')
                if plan:
                    run = schedule_by_plan(plan)
                    results['workshops_processed'] += 1
                    results['total_equipment_scheduled'] += len(run.created)
                    results['workshop_details'][workshop.name] = {
                        'scheduled': len(run.created),
                        'unschedulable': len(run.unschedulable),
                        'status': 'plan',
                    }
                    logger.info(f"  {run.summary()}")
                    continue

                # Unscheduled = no open schedule. Completed history alone does
                # not count: that is a chain that stopped, not a scheduled device.
                scheduled_equipment_ids = PPMSchedule.open_schedules().filter(
                    equipment__workshop_id=workshop.id,
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

                # Equipment with completed history continues its own cycle;
                # only equipment never maintained goes through initialization.
                with_history = set(
                    PPMSchedule.objects.filter(
                        equipment_id__in=unscheduled_equipment_ids,
                        status='completed',
                        pending_delete=False,
                    ).values_list('equipment_id', flat=True)
                )
                resumed = _resume_ppm_chains(with_history) if with_history else 0
                results['total_equipment_resumed'] += resumed
                unscheduled_equipment_ids = [
                    eid for eid in unscheduled_equipment_ids if eid not in with_history
                ]

                unscheduled_count = len(unscheduled_equipment_ids)

                logger.info(f"  Resumed chains: {resumed} of {len(with_history)}")
                logger.info(f"  Never scheduled: {unscheduled_count}")

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

                # Call the main initialization task synchronously. It is a
                # bound task, so calling it passes `self` already: an extra
                # leading None used to shift every argument and fail with
                # "got multiple values for argument 'planning_logic'".
                result = initialize_ppm_schedule_with_logic(
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
            f"scheduled {results['total_equipment_scheduled']} equipment items, "
            f"resumed {results['total_equipment_resumed']} broken chains"
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
                PPMSchedule.open_schedules().filter(
                    equipment__workshop_id=workshop.id,
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
