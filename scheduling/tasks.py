"""Background jobs for scheduling (see Equiper/celery.py beat_schedule)."""
import logging

from celery import shared_task
from django.utils import timezone

from . import planner

logger = logging.getLogger(__name__)


@shared_task(name="scheduling.tasks.run_plans")
def run_plans():
    """Give every workshop a plan and every device its next schedule.

    Also catches completions that arrived through sync (raw SQL, no signal).
    """
    results = planner.run_all()
    created = sum(len(r.created) for r in results)
    blocked = sum(len(r.unschedulable) for r in results)
    summary = f"{len(results)} plan(s): {created} scheduled, {blocked} cannot be scheduled"
    logger.info(f"[PLAN] {summary}")
    return summary


@shared_task(name="scheduling.tasks.retire_inactive_equipment_schedules")
def retire_inactive_equipment_schedules():
    """Take open schedules of inactive equipment out of the plan.

    Retire, never delete: completed schedules are the device's history and
    are left alone; if the device is reactivated it is scheduled again.
    """
    retired = 0
    for program in planner.PROGRAMS.values():
        retired += program.model.open_schedules().filter(equipment__active_status=False).update(
            active_status=False, needs_sync=True, updated_at=timezone.now())
    logger.info(f"[PLAN] Retired {retired} open schedule(s) of inactive equipment")
    return retired
