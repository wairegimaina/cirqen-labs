"""
PPM Signals — Auto-reschedule on completion (GROUP-AWARE + SMART ORGANIZER)
============================================================================

This module provides Django signals for PPM schedule lifecycle events:
1. Auto-reschedule when an ENTIRE GROUP completes maintenance.
2. Auto-create PPM schedule when new equipment is added.
3. Update schedules when equipment is transferred.

KEY PRINCIPLES:
- Group-aware: waits for all members of a department/description to complete.
- Never over-scatter: the entire group moves to the same next month.
- Smart Organizer Protection:
    - Completed schedules are auto-locked (historical records).
    - Signal-created schedules are protected from normalization.
    - Locked schedules cannot be modified by bulk operations.
    - Uses planning_logic stored on the schedule itself to determine group boundaries.
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
#  UTILITY: GROUP COMPLETION FOR PPM
# ============================================================

def _get_ppm_group_members(schedule):
    """
    Get all active PPM schedules in the same group as `schedule`.
    Grouping is based on the schedule's stored planning_logic:
      - department / date_based  -> group by equipment.department_id
      - description / description_based -> group by equipment.description_id
    """
    planning_logic = schedule.planning_logic or 'department'
    if planning_logic in ('description', 'description_based'):
        group_filter = {
            'equipment__description_id': schedule.equipment.description_id,
            'scheduled_month': schedule.scheduled_month,
            'active_status': True,
        }
    else:
        group_filter = {
            'equipment__department_id': schedule.equipment.department_id,
            'scheduled_month': schedule.scheduled_month,
            'active_status': True,
        }
    return PPMSchedule.objects.filter(**group_filter).select_related('equipment')


def _check_ppm_group_completion(schedule):
    """
    Check whether every active member in the same group as `schedule`
    has status == 'completed'.

    Returns:
        dict with keys: total, completed, all_completed, members_qs
    """
    members = _get_ppm_group_members(schedule)
    total = members.count()
    completed = members.filter(status='completed').count()
    return {
        'total': total,
        'completed': completed,
        'all_completed': total > 0 and completed == total,
        'members': members,
    }


def _get_ppm_group_key(schedule):
    """
    Generate a deterministic group key so the same group isn't
    re-processed multiple times in the same signal run.
    """
    planning_logic = schedule.planning_logic or 'department'
    if planning_logic in ('description', 'description_based'):
        group_id = schedule.equipment.description_id if schedule.equipment and schedule.equipment.description else 'no_description'
    else:
        group_id = schedule.equipment.department_id if schedule.equipment and schedule.equipment.department else 'no_department'
    month_key = schedule.scheduled_month.strftime('%Y-%m') if schedule.scheduled_month else 'unknown'
    return f"{planning_logic}_{group_id}_{month_key}"


# ============================================================
#  SIGNAL 1: Auto-Reschedule on Group Completion
# ============================================================

@receiver(post_save, sender=PPMSchedule)
def auto_reschedule_ppm_on_group_complete(sender, instance, created, **kwargs):
    """
    Fires after *any* PPMSchedule save.

    If the schedule just moved to 'completed', we check whether the
    ENTIRE group is now completed.  If so, every member is advanced
    to the SAME next month (original scheduled_month + maintenance_period).

    Smart Organizer Protection:
      - Skips if status != 'completed'
      - Skips saves that did not touch the status (e.g. locking a member)
      - Skips members that already have an open schedule, so re-running is safe
      - New schedules are created with generation_source='signal' and parent_schedule link
      - Only operates on active schedules

    There is deliberately no "already processed this group" memo. One used to
    live on this function for the life of the process and was filled in
    before the completion check, so the first completion in a group recorded
    the group as done and every later completion returned early: groups with
    more than one member never advanced. The open-schedule check below is
    what keeps repeated runs from creating duplicates.
    """
    # Only act on existing rows that reached 'completed'
    if created or instance.status != 'completed':
        return

    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        return

    # A workshop with an active scheduling plan: the plan decides the next
    # month, for this device alone (no waiting on the rest of the group).
    from scheduling.planner import on_completed
    if on_completed(instance):
        return

    group_key = _get_ppm_group_key(instance)

    # Check group completion
    group_status = _check_ppm_group_completion(instance)

    if not group_status['all_completed']:
        logger.info(
            f"[PPM_SIGNAL] Group not complete: "
            f"{group_status['completed']}/{group_status['total']} done for key {group_key}. "
            f"Waiting for remaining members."
        )
        return

    # ENTIRE GROUP IS COMPLETE  reschedule every member together
    members = group_status['members']
    period = instance.maintenance_period or 6
    next_month = instance.scheduled_month + relativedelta(months=period)

    logger.info(
        f"[PPM_SIGNAL] Group complete ({group_key}): "
        f"{group_status['completed']}/{group_status['total']} members done. "
        f"Rescheduling entire group to {next_month.strftime('%B %Y')}.")

    with transaction.atomic():
        for member in members:
            # One open schedule per equipment: skip anything already scheduled,
            # whichever month that schedule is in.
            if PPMSchedule.open_schedules().filter(equipment=member.equipment).exists():
                logger.debug(
                    f"[PPM_SIGNAL] Equipment {member.equipment.id} already has an open schedule, skipping."
                )
                continue

            # Mark the completed parent as locked if not already
            if not member.is_locked:
                member.is_locked = True
                member.save(update_fields=['is_locked', 'needs_sync'])

            try:
                # Savepoint: one failed insert must not abort the other members.
                with transaction.atomic():
                    new_schedule = PPMSchedule.objects.create(
                        equipment=member.equipment,
                        scheduled_month=next_month,
                        status='pending',
                        maintenance_period=period,
                        planning_logic=member.planning_logic or 'department',
                        generation_source='signal',
                        parent_schedule=member,
                        expected_maintenance_date=next_month,
                        workshop=member.workshop,
                        active_status=True,
                    )
                logger.info(
                    f"[PPM_SIGNAL] Rescheduled equipment {member.equipment.id} "
                    f"to {new_schedule.scheduled_month.strftime('%B %Y')} (period={period}m, source=signal, parent={member.id})."
                )
            except Exception as exc:
                logger.error(
                    f"[PPM_SIGNAL] Failed to create next PPM schedule for "
                    f"equipment {member.equipment.id}: {exc}", exc_info=True
                )


# ============================================================
#  SIGNAL 2: Auto-Create Schedule for New Equipment
# ============================================================

@receiver(post_save, sender=Equipment)
def auto_create_ppm_for_new_equipment(sender, instance, created, **kwargs):
    """
    When new active Equipment is created, automatically create an initial
    PPM schedule so it appears in the maintenance planning immediately.

    Rules:
      - Only if Equipment.active_status == True
      - Only if no existing PPM schedule exists for this equipment
      - Defaults to 'department' logic and 6-month maintenance period.
      - The schedule's month is placed in the next available month.
    """
    if not created:
        return

    if not instance.active_status:
        logger.debug(f"[PPM_SIGNAL] Equipment {instance.id} is not active, skipping auto-create.")
        return

    # Planned workshops schedule new equipment through scheduling.signals.
    from scheduling.planner import active_plan
    if instance.department_id and active_plan(instance.department.workshop_id, 'ppm'):
        return

    existing = PPMSchedule.objects.filter(equipment=instance, active_status=True).exists()
    if existing:
        logger.debug(f"[PPM_SIGNAL] PPM schedule already exists for equipment {instance.id}, skipping.")
        return

    try:
        today = date.today()
        next_month = date(today.year, today.month, 1) + relativedelta(months=1)

        PPMSchedule.objects.create(
            equipment=instance,
            workshop=instance.department.workshop if instance.department else None,
            scheduled_month=next_month,
            status='pending',
            maintenance_period=6,
            planning_logic='department',
            generation_source='signal',
            active_status=True,
        )
        logger.info(
            f"[PPM_SIGNAL] Auto-created initial PPM schedule for new equipment {instance.id} "
            f"in {next_month.strftime('%B %Y')} (source=signal)."
        )
    except Exception as exc:
        logger.error(
            f"[PPM_SIGNAL] Failed to auto-create PPM schedule for equipment {instance.id}: {exc}",
            exc_info=True,
        )


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
