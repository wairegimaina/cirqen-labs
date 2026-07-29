"""ppms.tasks.maintenance — validation / cleanup / reporting tasks."""
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


@shared_task(name="ppms.tasks.validate_ppm_schedules")
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


@shared_task(name="ppms.tasks.periodic_cleanup_inactive_schedules")
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


@shared_task(name="ppms.tasks.cleanup_orphaned_ppm_schedules")
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
