"""
PPM schedule signals.

1. A completed schedule gets its device's next one from the workshop's
   scheduling plan (see scheduling.planner).
2. When equipment moves to another department, its schedules follow it to
   the new workshop.
"""

import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db import transaction
from datetime import date
from dateutil.relativedelta import relativedelta

from .models import PPMSchedule
from Inventory.models import Equipment

logger = logging.getLogger(__name__)


# ============================================================
#  SIGNAL 1: Next schedule on completion
# ============================================================

@receiver(post_save, sender=PPMSchedule)
def schedule_next_ppm(sender, instance, created, **kwargs):
    """A completed PPM gets its device's next one from the workshop's plan.

    The plan (scheduling.planner) decides the month from its grouping,
    months and interval; nothing waits for the rest of a group. New
    equipment is scheduled by scheduling.signals.
    """
    if instance.status != 'completed':
        return
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        return
    from scheduling.planner import on_completed
    try:
        on_completed(instance)
    except Exception as exc:  # never block recording a completion
        logger.error(f"[PLAN] Could not schedule the next PPM after {instance.id}: {exc}", exc_info=True)


# ============================================================
#  SIGNAL 3: Handle Equipment Transfer (Department/Workshop)
# ============================================================

@receiver(post_save, sender=Equipment)
def update_ppm_on_equipment_transfer(sender, instance, created, **kwargs):
    """
    When an Equipment's department changes, update the workshop on all
    related PPM schedules so they stay in sync.
    """
    if created:
        return

    new_workshop = instance.department.workshop if instance.department else None
    if not new_workshop:
        return

    try:
        updated = PPMSchedule.objects.filter(equipment=instance).exclude(
            workshop=new_workshop
        ).update(workshop=new_workshop)
        if updated:
            logger.info(
                f"[PPM_SIGNAL] Updated workshop to {new_workshop.name} "
                f"for {updated} PPM schedule(s) of equipment {instance.id}."
            )
    except Exception as exc:
        logger.error(
            f"[PPM_SIGNAL] Failed to update PPM workshops for equipment {instance.id}: {exc}",
            exc_info=True,
        )
