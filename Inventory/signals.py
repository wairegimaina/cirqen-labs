"""
Equipment transfer signals to automatically update dependencies
"""
import logging
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from .models import Equipment

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Equipment)
def track_equipment_location_change(sender, instance, **kwargs):
    """
    Track if equipment's department/workshop is changing
    Store old values for comparison in post_save
    """
    if instance.pk:  # Only for existing equipment
        try:
            old_instance = Equipment.objects.get(pk=instance.pk)
            instance._old_department_id = old_instance.department_id
            instance._old_workshop_id = old_instance.department.workshop_id if old_instance.department else None
        except Equipment.DoesNotExist:
            instance._old_department_id = None
            instance._old_workshop_id = None
    else:
        instance._old_department_id = None
        instance._old_workshop_id = None


@receiver(post_save, sender=Equipment)
def update_dependencies_on_transfer(sender, instance, created, **kwargs):
    """
    Automatically update all dependencies when equipment is transferred
    """
    # Skip for new equipment
    if created:
        return

    # Check if location actually changed
    old_department_id = getattr(instance, '_old_department_id', None)
    old_workshop_id = getattr(instance, '_old_workshop_id', None)

    if not instance.department:
        return

    new_department_id = instance.department_id
    new_workshop_id = instance.department.workshop_id if instance.department.workshop else None

    # If location hasn't changed, skip
    if old_department_id == new_department_id and old_workshop_id == new_workshop_id:
        return

    logger.info(f"🔄 Equipment location changed - updating dependencies")
    logger.info(f"   Equipment: {instance.serial_number}")
    logger.info(f"   Old: Workshop {old_workshop_id}, Dept {old_department_id}")
    logger.info(f"   New: Workshop {new_workshop_id}, Dept {new_department_id}")

    # Update PPM Schedules
    try:
        from ppms.models import PPMSchedule

        ppms = PPMSchedule.objects.filter(equipment=instance)
        ppm_count = ppms.count()

        if ppm_count > 0:
            logger.info(f"   Updating {ppm_count} PPM schedules...")

            for ppm in ppms:
                ppm.workshop = instance.department.workshop
                ppm.updated_at = timezone.now()
                ppm.needs_sync = True
                ppm.save(update_fields=['workshop', 'updated_at', 'needs_sync'])

                logger.info(f"      ✅ PPM {ppm.id} updated to {instance.department.workshop.name}")

            logger.info(f"   ✅ Updated {ppm_count} PPM schedules")
    except ImportError:
        logger.warning("   ⚠️ PPM module not available")
    except Exception as e:
        logger.error(f"   ❌ Error updating PPM schedules: {e}")

    # Update Calibration Schedules
    try:
        from calSchedules.models import CalibrationSchedule

        cals = CalibrationSchedule.objects.filter(equipment=instance)
        cal_count = cals.count()

        if cal_count > 0:
            logger.info(f"   Updating {cal_count} Calibration schedules...")

            for cal in cals:
                cal.workshop = instance.department.workshop
                cal.updated_at = timezone.now()
                cal.needs_sync = True
                cal.save(update_fields=['workshop', 'updated_at', 'needs_sync'])

                logger.info(f"      ✅ Cal {cal.id} updated to {instance.department.workshop.name}")

            logger.info(f"   ✅ Updated {cal_count} Calibration schedules")
    except ImportError:
        logger.warning("   ⚠️ Calibration module not available")
    except Exception as e:
        logger.error(f"   ❌ Error updating Calibration schedules: {e}")

    logger.info(f"🎉 Equipment dependencies updated via signals")
