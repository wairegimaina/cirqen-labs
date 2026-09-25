"""Keep planned workshops' schedules current as equipment changes.

* New equipment gets its first schedule immediately rather than at the
  nightly run.
* A transfer to another department can put a device in a group with other
  months (department plans); its open schedule is moved to match.

"""
import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from Inventory.models import Equipment

from . import planner

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Equipment)
def remember_department(sender, instance, raw=False, **kwargs):
    if raw or not instance.pk:
        return
    instance._scheduling_old_department_id = (
        Equipment.objects.filter(pk=instance.pk).values_list("department_id", flat=True).first()
    )


@receiver(post_save, sender=Equipment)
def schedule_planned_equipment(sender, instance, created, raw=False, **kwargs):
    if raw or not instance.active_status or not instance.department_id:
        return
    try:
        workshop = instance.department.workshop
        if created:
            for program in planner.PROGRAMS:
                planner.schedule(planner.ensure_plan(workshop, program), equipment_ids=[instance.id])
            return

        old = getattr(instance, "_scheduling_old_department_id", None)
        if old and old != instance.department_id:
            moved = planner.reassign(instance)
            if moved:
                logger.info(f"[PLAN] {instance.serial_number} transferred: moved {moved} open schedule(s)")
            # A transfer to another workshop may leave it unscheduled there.
            for program in planner.PROGRAMS:
                planner.schedule(planner.ensure_plan(workshop, program), equipment_ids=[instance.id])
    except Exception as exc:  # scheduling must never block saving equipment
        logger.error(f"[PLAN] Could not schedule equipment {instance.pk}: {exc}", exc_info=True)
