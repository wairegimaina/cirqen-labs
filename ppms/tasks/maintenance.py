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


def _retire(queryset):
    """Take schedules out of the plan without deleting them.

    ``update()`` skips ``save()``, so ``needs_sync`` and ``updated_at`` are set
    here for the sync agent to pick the change up.
    """
    from django.utils import timezone
    return queryset.update(active_status=False, needs_sync=True, updated_at=timezone.now())


@shared_task(name="ppms.tasks.validate_ppm_schedules")
def validate_ppm_schedules(workshop_id):
    """
    Validate PPM schedules and fix common issues.
    NEW: Check for groups that are split across different months
    """
    try:
        workshop = Workshop.objects.get(id=workshop_id)
        issues_fixed = 0

        # Retire open schedules of inactive equipment (completed history stays)
        inactive_count = _retire(PPMSchedule.open_schedules().filter(
            workshop_id=workshop_id,
            equipment__active_status=False
        ))
        if inactive_count > 0:
            issues_fixed += inactive_count
            logger.info(f"Retired {inactive_count} open schedules for inactive equipment")

        # More than one OPEN schedule for an equipment: keep the earliest,
        # retire the rest. Completed schedules are history, not duplicates.
        from django.db.models import Count
        open_qs = PPMSchedule.open_schedules().filter(
            workshop_id=workshop_id,
            equipment__active_status=True
        )
        duplicates = open_qs.values('equipment_id').annotate(
            count=Count('id')
        ).filter(count__gt=1)

        for duplicate in duplicates:
            extra_ids = list(
                open_qs.filter(equipment_id=duplicate['equipment_id'])
                .order_by('scheduled_month', 'created_at')
                .values_list('id', flat=True)[1:]
            )
            retired = _retire(PPMSchedule.objects.filter(id__in=extra_ids))
            logger.info(
                f"Retired {retired} duplicate open schedule(s) for equipment {duplicate['equipment_id']}"
            )
            issues_fixed += retired

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
    """Retire open PPM schedules of inactive equipment.

    Only schedules still to be done are retired (``active_status=False``);
    completed schedules are the equipment's maintenance history and are kept.
    This task used to delete every schedule of an inactive device, completed
    ones included, so reactivating a device brought it back with no history.
    """
    try:
        inactive_schedules = PPMSchedule.open_schedules().filter(
            equipment__active_status=False
        )
        equipment_ids = list(inactive_schedules.values_list('equipment_id', flat=True)[:10])
        count = _retire(inactive_schedules)

        if count > 0:
            logger.info(
                f"Periodic cleanup: Retired {count} open PPM schedule(s) for inactive equipment. "
                f"Equipment IDs: {equipment_ids}{'...' if count > 10 else ''}"
            )
            return f"Retired {count} open schedules for inactive equipment"
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
