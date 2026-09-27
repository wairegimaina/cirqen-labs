"""Calibration schedule lifecycle: completion and the next schedule.

* A certificate on an approved calibration session completes its schedule.
  The schedule keeps the month it was due in; ``completed_date`` records the
  day the work was done.
* A completed schedule gets its device's next one from the workshop's
  scheduling plan (``scheduling.planner``), which decides the month from the
  plan's grouping, months and interval. Nothing waits for the rest of a group.

Completions that arrive through sync are written with raw SQL and fire no
signal; the regular ``scheduling.tasks.run_plans`` sweep picks those up.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import localdate

from .models import CalibrationSchedule

logger = logging.getLogger(__name__)

OPEN_STATUSES = ("pending", "pushed", "in_progress", "overdue")


@receiver(post_save, sender="CalSoft.CalibrationSession")
def complete_schedule_on_certificate(sender, instance, created, **kwargs):
    if not instance.certificate_number:
        return
    if instance.status not in ("approved", "approved_pending_certificate"):
        return

    schedule = instance.schedule
    equipment = schedule.equipment if schedule else None
    if equipment is None:
        logger.warning(f"[CERT] Session {instance.id} has certificate {instance.certificate_number} but no schedule")
        return
    if schedule.status == "completed":
        return

    schedule.completed_date = localdate(instance.timestamp)
    schedule.status = "completed"
    schedule.save(update_fields=["status", "completed_date", "updated_at", "needs_sync"])
    logger.info(f"[CERT] {instance.certificate_number}: completed schedule {schedule.id} "
                f"(due {schedule.scheduled_month:%B %Y}, done {schedule.completed_date})")


@receiver(post_save, sender=CalibrationSchedule)
def schedule_next_calibration(sender, instance, created, **kwargs):
    if instance.status != "completed":
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "status" not in update_fields:
        return
    from scheduling.planner import on_completed
    try:
        on_completed(instance)
    except Exception as exc:  # never block recording a completion
        logger.error(f"[PLAN] Could not schedule the next calibration after {instance.id}: {exc}", exc_info=True)
