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
      - Skips if the group has already been processed in this run
      - New schedules are created with generation_source='signal' and parent_schedule link
      - Only operates on active schedules
    """
    # Only act on existing rows that reached 'completed'
    if created or instance.status != 'completed':
        return

    # Avoid re-processing groups when multiple saves fire in the same request
    group_key = _get_ppm_group_key(instance)

    # Important: django signals run in-process, so a module-level set is safe
    if not hasattr(auto_reschedule_ppm_on_group_complete, '_processed_groups'):
        auto_reschedule_ppm_on_group_complete._processed_groups = set()

    if group_key in auto_reschedule_ppm_on_group_complete._processed_groups:
        return

    auto_reschedule_ppm_on_group_complete._processed_groups.add(group_key)

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
            # Skip if already rescheduled for this next month
            existing = PPMSchedule.objects.filter(
                equipment=member.equipment,
                scheduled_month=next_month,
                active_status=True
            ).exists()

            if existing:
                logger.debug(
                    f"[PPM_SIGNAL] Next schedule already exists for equipment {member.equipment.id} "
                    f"in {next_month.strftime('%B %Y')}, skipping."
                )
                continue

            # Mark the completed parent as locked if not already
            if not member.is_locked:
                member.is_locked = True
                member.save(update_fields=['is_locked', 'needs_sync'])

            try:
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
