"""Keep planned workshops' schedules current as equipment changes.

* New equipment gets its first schedules immediately, rather than at the
  next sweep: PPM from its maintenance workshop's plan, calibration from the
  hospital-wide plan of the calibration center. Its group (description or
  department) is taken from whichever logic that plan uses; a group the plan
  has never seen is given months of its own.
* A transfer to another department, or a change of description, can put a
  device in a group with other months; its open schedule is moved to match.

"""
import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from Inventory.models import Equipment

from . import planner

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Equipment)
def remember_group(sender, instance, raw=False, **kwargs):
    if raw or not instance.pk:
        return
    instance._scheduling_old_group = (
        Equipment.objects.filter(pk=instance.pk).values_list("department_id", "description_id").first()
    )


@receiver(post_save, sender=Equipment)
def schedule_planned_equipment(sender, instance, created, raw=False, **kwargs):
    if raw or not instance.active_status or not instance.department_id:
        return
    try:
        if created:
            _schedule_everywhere(instance)
            return

        old = getattr(instance, "_scheduling_old_group", None)
        if old and old != (instance.department_id, instance.description_id):
            moved = planner.reassign(instance)
            if moved:
                logger.info(f"[PLAN] {instance.serial_number} changed group: moved {moved} open schedule(s)")
            # A transfer to another workshop may leave it unscheduled there.
            _schedule_everywhere(instance)
    except Exception as exc:  # scheduling must never block saving equipment
        logger.error(f"[PLAN] Could not schedule equipment {instance.pk}: {exc}", exc_info=True)


def _schedule_everywhere(equipment):
    for program in planner.PROGRAMS:
        plan = planner.plan_for(equipment, program)
        if plan is not None:
            planner.schedule(plan, equipment_ids=[equipment.id])
