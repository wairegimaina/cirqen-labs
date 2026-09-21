"""calSchedules.instant_reconciliation.signals — post_save receivers (reschedule, complete, align)."""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from datetime import date, datetime, timezone
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import logging
import uuid
from django.db.models import Count, Min, Q
from ..models import CalibrationSchedule
from ..reconciliation import trigger_sync_for_changes
from .. import grouping
logger = logging.getLogger(__name__)

# sibling modules in this package
from .helpers import (
    alignment_suppressed,
    check_group_completion_status,
    get_group_scheduled_month,
    get_next_group_month,
    lock_schedule_immediately,
)


@receiver(post_save, sender=CalibrationSchedule)
def instant_reschedule_on_completion(sender, instance, created, **kwargs):
    """
    ⚡ Instantly reschedule when ENTIRE GROUP completes calibration
    🔒 AND IMMEDIATELY LOCK THE COMPLETED SCHEDULE

    TRIGGER: Fires when status = 'completed'. The status is only set to
    'completed' through the job card workflow (certificate generation via
    auto_complete_on_certificate or direct job-card status update).

    KEY RULES:
    1. Lock immediately on completion (no waiting)
    2. Entire group moves together to next period
    3. The NEW schedule's scheduled_month = original scheduled_month + period
       (NOT based on actual calibration date — early/late calibrations do not
        shift the scheduled month)
    4. planning_logic of the new schedule matches the completed schedule exactly
    """

    # Skip if this is a new creation
    if created:
        return

    # Only trigger on status change to 'completed'
    if instance.status != 'completed':
        return

    # ✅ NEW: IMMEDIATELY LOCK THIS COMPLETED SCHEDULE
    if hasattr(instance, 'is_locked') and not instance.is_locked:
        lock_schedule_immediately(instance)
        logger.info(f"[SIGNAL] 🔒 Immediately locked completed schedule {instance.id}")

    # Get planning logic
    planning_logic = instance.planning_logic or 'department'

    logger.info(
        f"[SIGNAL] 🔥 Calibration completed for equipment {instance.equipment.id} "
        f"in {instance.scheduled_month.strftime('%B %Y')} - checking group status"
    )

    # Check if entire group is now complete
    group_status = check_group_completion_status(
        instance.equipment,
        instance.scheduled_month,
        planning_logic
    )

    if not group_status['all_completed']:
        logger.info(
            f"[SIGNAL] ⏸️ Group not complete yet: "
            f"{group_status['completed']}/{group_status['total']} done - waiting for others"
        )
        return

    # ✅ ENTIRE GROUP COMPLETE - RESCHEDULE ALL TOGETHER
    logger.info(
        f"[SIGNAL] ✅ ENTIRE GROUP COMPLETE! "
        f"{group_status['group_name']} - {group_status['total']} equipment"
    )

    # Calculate next schedule month for the group
    # RULE: Always add period to the ORIGINAL scheduled_month (not the actual calibration date).
    # This means if equipment was calibrated early (e.g., in month 10 but scheduled for month 12),
    # the next schedule is still scheduled_month + period, not actual_date + period.
    period = instance.calibration_period or 12
    next_month = get_next_group_month(
        instance.equipment,
        instance.scheduled_month,  # ← Always the original scheduled month
        period,
        planning_logic
    )

    # Get all completed members
    completed_members = group_status['members'].filter(status='completed')

    rescheduled_count = 0
    already_exists_count = 0

    with transaction.atomic():
        for member in completed_members:
            # Check if schedule for next month already exists
            existing = CalibrationSchedule.objects.filter(
                equipment=member.equipment,
                scheduled_month=next_month
            ).exists()

            if existing:
                already_exists_count += 1
                logger.debug(
                    f"[SIGNAL] Schedule for {member.equipment.id} "
                    f"in {next_month.strftime('%B %Y')} already exists"
                )
                continue

            # Create new schedule for next period
            # ✅ CRITICAL: scheduled_month = original scheduled_month + period
            # planning_logic is inherited from the completed schedule — keeps logic consistent
            new_schedule = CalibrationSchedule.objects.create(
                equipment=member.equipment,
                scheduled_month=next_month,
                status='pending',
                calibration_period=period,
                planning_logic=member.planning_logic or planning_logic,  # Preserve logic
                generation_source='signal',  # Mark as PPM auto-generated via job card
                parent_schedule=member,
                workshop=member.workshop,
            )

            # ✅ LOCK THE OLD COMPLETED SCHEDULE IMMEDIATELY
            if hasattr(member, 'is_locked') and not member.is_locked:
                lock_schedule_immediately(member)

            rescheduled_count += 1

            logger.info(
                f"[SIGNAL] ✅ PPM Rescheduled {member.equipment.id} to "
                f"{next_month.strftime('%B %Y')} "
                f"(original scheduled_month: {member.scheduled_month.strftime('%B %Y')}, "
                f"period: {period}m, logic: {member.planning_logic or planning_logic})"
            )

        # Sync all new schedules to HQ
        new_schedule_ids = CalibrationSchedule.objects.filter(
            scheduled_month=next_month,
            status='pending',
            generation_source='signal',
            equipment__in=[m.equipment for m in completed_members]
        ).values_list('id', flat=True)

        if new_schedule_ids:
            trigger_sync_for_changes(list(new_schedule_ids), operation='u')

    logger.info(
        f"[SIGNAL] 🎉 Group reschedule complete! "
        f"Created {rescheduled_count} new schedules for {next_month.strftime('%B %Y')}. "
        f"Already existed: {already_exists_count}"
    )


@receiver(post_save, sender='CalSoft.CalibrationSession')
def auto_complete_on_certificate(sender, instance, created, **kwargs):
    """
    ⚡ AUTO-PPM TRIGGER: Complete schedule when certificate is generated via Job Card

    This is the PRIMARY TRIGGER for the PPM auto-reschedule cycle.

    The flow is:
        Job Card submitted → Session approved → Certificate generated
        → This signal fires → Schedule marked as 'completed'
        → instant_reschedule_on_completion fires → Next PPM schedule created

    IMPORTANT RULES:
    - scheduled_month of the COMPLETED schedule is NEVER changed.
      Even if the calibration happened early (cert in month 10, scheduled for month 12)
      or late (cert in month 2 of next year, scheduled for month 12), the
      scheduled_month stays as the month it was planned. The completed_date
      field records the actual date.
    - The NEW schedule's month is calculated as: completed schedule's scheduled_month + period
    - planning_logic of the new schedule matches the completed schedule exactly.

    Triggered when:
    - A calibration session is saved
    - Session has a certificate_number assigned
    - Session status is 'approved' or 'approved_pending_certificate'

    Actions:
    1. Finds matching pending schedule for the equipment
    2. Sets completed_date = actual certificate date (NOT scheduled_month)
    3. Marks schedule as completed (which triggers instant_reschedule_on_completion)
    4. IMMEDIATELY LOCKS IT
    5. Group-aware reschedule fires via the other signal
    """
    # Skip if this is a new session creation without certificate
    if created and not instance.certificate_number:
        return

    # Only proceed if certificate number is assigned
    if not instance.certificate_number:
        return

    # Only proceed if session is approved (job card complete)
    if instance.status not in ['approved', 'approved_pending_certificate']:
        return

    # Get session details
    equipment = instance.schedule.equipment if instance.schedule else None

    if not equipment:
        logger.warning(
            f"[CERT] ⚠️ Session {instance.id} has certificate {instance.certificate_number} "
            f"but no equipment found"
        )
        return

    # actual calibration date from the certificate/session
    cal_date = instance.timestamp.date()

    logger.info(
        f"[CERT] 📜 Certificate {instance.certificate_number} assigned to session "
        f"for equipment {equipment.id} on {cal_date.strftime('%Y-%m-%d')} "
        f"— triggering PPM auto-reschedule"
    )

    # Find matching schedule (the one linked to this session)
    matching_schedule = instance.schedule

    if not matching_schedule:
        # Try to find pending schedule for this equipment in the session's month
        schedule_month = cal_date.replace(day=1)
        matching_schedule = CalibrationSchedule.objects.filter(
            equipment=equipment,
            scheduled_month=schedule_month,
            status__in=['pending', 'pushed', 'in_progress']
        ).first()

        if not matching_schedule:
            # Try to find closest pending schedule regardless of month
            # (handles early/late calibrations — scheduled_month is preserved)
            matching_schedule = CalibrationSchedule.objects.filter(
                equipment=equipment,
                status__in=['pending', 'pushed', 'in_progress']
            ).order_by('scheduled_month').first()

            if matching_schedule:
                logger.info(
                    f"[CERT] Matched closest pending schedule in "
                    f"{matching_schedule.scheduled_month.strftime('%B %Y')} "
                    f"(actual calibration date: {cal_date.strftime('%Y-%m-%d')} — "
                    f"scheduled_month is preserved, NOT overridden)"
                )

    if matching_schedule:
        # Only complete if not already completed
        if matching_schedule.status != 'completed':
            # ✅ Record ACTUAL calibration date — does NOT change scheduled_month
            matching_schedule.completed_date = cal_date
            # ✅ Mark completed — this triggers instant_reschedule_on_completion signal
            matching_schedule.status = 'completed'
            matching_schedule.save(update_fields=[
                'status',
                'completed_date',
                'updated_at',
                'needs_sync'
            ])

            # ✅ IMMEDIATELY LOCK THIS COMPLETED SCHEDULE
            if hasattr(matching_schedule, 'is_locked') and not matching_schedule.is_locked:
                lock_schedule_immediately(matching_schedule)

            logger.info(
                f"[CERT] ✅ Auto-completed and locked schedule {matching_schedule.id} "
                f"for equipment {equipment.id} "
                f"(Certificate: {instance.certificate_number}, "
                f"Scheduled month: {matching_schedule.scheduled_month.strftime('%B %Y')}, "
                f"Actual calibration date: {cal_date.strftime('%Y-%m-%d')})"
            )

            # Sync to HQ
            trigger_sync_for_changes([matching_schedule.id], operation='u')

            # The instant_reschedule_on_completion signal handles group PPM rescheduling
        else:
            logger.debug(
                f"[CERT] Schedule {matching_schedule.id} already completed - skipping"
            )
    else:
        logger.warning(
            f"[CERT] ⚠️ No pending schedule found for equipment {equipment.id} "
            f"(Certificate: {instance.certificate_number}) — certificate recorded but "
            f"no schedule updated. Create a schedule manually if needed."
        )


@receiver(post_save, sender=CalibrationSchedule)
def instant_grouping_alignment(sender, instance, created, **kwargs):
    """
    ⚡ Instantly align new/updated schedules to their group

    STRICT RULE: Equipment NEVER jump between months within a group

    When:
    - New schedule created
    - Schedule updated (but not completed)

    Actions:
    - Check if equipment's group has existing schedules
    - If yes, align to group's month
    - If no, current month becomes the group's month
    """
    # Stand down during a deliberate regroup. The whole selection is being
    # moved together, so pulling each schedule back to the group's old month as
    # it is saved would make the move impossible.
    if alignment_suppressed():
        return

    # Skip if completed (handled by reschedule signal)
    if instance.status == 'completed':
        return

    # Skip if this is a signal-generated schedule (already aligned)
    if hasattr(instance, 'generation_source') and instance.generation_source == 'signal':
        return

    # Skip if locked
    if hasattr(instance, 'is_locked') and instance.is_locked:
        return

    planning_logic = instance.planning_logic or 'department'

    # Find where this equipment's group is scheduled
    group_month = get_group_scheduled_month(instance.equipment, planning_logic)

    # If schedule is not in group's month, align it
    if instance.scheduled_month != group_month:
        logger.info(
            f"[GROUPING] Equipment {instance.equipment.id} scheduled in "
            f"{instance.scheduled_month.strftime('%B %Y')} but group is in "
            f"{group_month.strftime('%B %Y')} - aligning to group"
        )

        # Check for conflicts (same equipment, same new month)
        conflict = CalibrationSchedule.objects.filter(
            equipment=instance.equipment,
            scheduled_month=group_month
        ).exclude(id=instance.id).exists()

        if conflict:
            logger.warning(
                f"[GROUPING] Schedule conflict exists for equipment {instance.equipment.id} "
                f"in {group_month.strftime('%B %Y')} - deleting this duplicate"
            )
            instance.delete()
            return

        # Align to group month
        instance.scheduled_month = group_month
        instance.generation_source = 'group_alignment'
        instance.save(update_fields=['scheduled_month', 'generation_source', 'updated_at'])

        # Sync to HQ
        trigger_sync_for_changes([instance.id], operation='u')

        logger.info(
            f"[GROUPING] ✅ Aligned equipment {instance.equipment.id} to group month "
            f"{group_month.strftime('%B %Y')}"
        )
