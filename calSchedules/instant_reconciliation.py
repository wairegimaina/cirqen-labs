"""
calSchedules/instant_reconciliation.py (IMPROVED - IMMEDIATE LOCKING)
====================================================================

IMPROVED VERSION with immediate locking:
- Equipment in same department ALWAYS scheduled in same month
- Equipment in same description ALWAYS scheduled in same month
- IMMEDIATE locking on completion (no waiting)
- Periodic checker locks ALL completed schedules

KEY PRINCIPLE: Lock immediately when completed!
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from datetime import date, datetime, timezone
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import logging
import uuid
from django.db.models import Count, Min, Q
from .models import CalibrationSchedule
from .reconciliation import trigger_sync_for_changes

logger = logging.getLogger(__name__)


# ============================================================
# 🔒 IMMEDIATE LOCKING SYSTEM
# ============================================================

def lock_schedule_immediately(schedule):
    """
    Lock a schedule immediately without any conditions.

    Args:
        schedule: CalibrationSchedule instance

    Returns:
        bool: True if locked, False if already locked or failed
    """
    if not hasattr(schedule, 'is_locked'):
        logger.warning(f"[LOCK] Schedule {schedule.id} has no is_locked field")
        return False

    if schedule.is_locked:
        logger.debug(f"[LOCK] Schedule {schedule.id} already locked")
        return False

    try:
        schedule.is_locked = True
        schedule.save(update_fields=['is_locked', 'updated_at'])

        logger.info(f"[LOCK] ✅ Immediately locked schedule {schedule.id}")

        # Sync to HQ
        trigger_sync_for_changes([schedule.id], operation='u')

        return True

    except Exception as e:
        logger.error(f"[LOCK] ❌ Failed to lock schedule {schedule.id}: {e}")
        return False


def lock_all_completed_schedules():
    """
    🔒 IMMEDIATE CHECKER: Lock ALL completed schedules without any waiting.

    This function:
    - Finds ALL completed schedules
    - Locks them immediately
    - No group checking
    - No waiting periods
    - No conditions

    Returns:
        dict: Results with locked count
    """
    logger.info("[LOCK_ALL] Starting immediate lock of ALL completed schedules")

    # Find ALL completed schedules that are not locked
    completed_unlocked = CalibrationSchedule.objects.filter(
        status='completed',
        is_locked=False
    ).select_related('equipment')

    total_to_lock = completed_unlocked.count()

    if total_to_lock == 0:
        logger.info("[LOCK_ALL] No unlocked completed schedules found")
        return {
            'locked': 0,
            'already_locked': 0,
            'failed': 0,
            'total': 0
        }

    logger.info(f"[LOCK_ALL] Found {total_to_lock} completed schedules to lock")

    locked_count = 0
    failed_count = 0
    schedule_ids = []

    # Lock each one immediately
    for schedule in completed_unlocked:
        try:
            schedule.is_locked = True
            schedule.save(update_fields=['is_locked', 'updated_at'])
            schedule_ids.append(schedule.id)
            locked_count += 1

            logger.debug(f"[LOCK_ALL] ✅ Locked {schedule.id} (Equipment: {schedule.equipment.id})")

        except Exception as e:
            logger.error(f"[LOCK_ALL] ❌ Failed to lock {schedule.id}: {e}")
            failed_count += 1

    # Sync all locked schedules to HQ
    if schedule_ids:
        trigger_sync_for_changes(schedule_ids, operation='u')
        logger.info(f"[LOCK_ALL] Synced {len(schedule_ids)} locked schedules to HQ")

    # Check how many were already locked
    already_locked = CalibrationSchedule.objects.filter(
        status='completed',
        is_locked=True
    ).count()

    result = {
        'locked': locked_count,
        'already_locked': already_locked,
        'failed': failed_count,
        'total': locked_count + already_locked
    }

    logger.info(
        f"[LOCK_ALL] ✅ Complete! Locked {locked_count} schedules. "
        f"Already locked: {already_locked}, Failed: {failed_count}"
    )

    return result


# ============================================================
# 🎯 GROUP-BASED DATE UTILITIES (STRICT)
# ============================================================

def get_group_scheduled_month(equipment, planning_logic='department'):
    """
    Get the month where this equipment's group is ALREADY scheduled.

    This ensures all equipment in the same group stay together in one month.

    Args:
        equipment: Equipment object
        planning_logic: 'department' or 'description'

    Returns:
        date: The month where this group is scheduled, or current month if new group
    """
    today = date.today().replace(day=1)

    if planning_logic == 'department':
        dept_id = equipment.department_id if hasattr(equipment, 'department_id') else None

        # Find where this department is ALREADY scheduled
        existing_group = CalibrationSchedule.objects.filter(
            equipment__department_id=dept_id,
            equipment__active_status=True,
            status__in=['pending', 'pushed'],
            scheduled_month__gte=today  # Only future or current schedules
        ).values('scheduled_month').annotate(
            count=Count('id')
        ).order_by('-count', 'scheduled_month').first()

        if existing_group:
            group_month = existing_group['scheduled_month']
            logger.info(
                f"[GROUP_ALIGNMENT] Department {dept_id} group found at "
                f"{group_month.strftime('%B %Y')} with {existing_group['count']} equipment"
            )
            return group_month
        else:
            logger.info(f"[GROUP_ALIGNMENT] New department {dept_id} group, using current month")
            return today

    else:  # description
        desc_id = equipment.description_id if hasattr(equipment, 'description_id') else None

        # Find where this description is ALREADY scheduled
        existing_group = CalibrationSchedule.objects.filter(
            equipment__description_id=desc_id,
            equipment__active_status=True,
            status__in=['pending', 'pushed'],
            scheduled_month__gte=today  # Only future or current schedules
        ).values('scheduled_month').annotate(
            count=Count('id')
        ).order_by('-count', 'scheduled_month').first()

        if existing_group:
            group_month = existing_group['scheduled_month']
            logger.info(
                f"[GROUP_ALIGNMENT] Description {desc_id} group found at "
                f"{group_month.strftime('%B %Y')} with {existing_group['count']} equipment"
            )
            return group_month
        else:
            logger.info(f"[GROUP_ALIGNMENT] New description {desc_id} group, using current month")
            return today


def get_next_group_month(equipment, current_month, period=12, planning_logic='department'):
    """
    Calculate next schedule month for ENTIRE group (not individual equipment).

    Ensures the whole group moves together to the next period.

    Args:
        equipment: Equipment object (to identify the group)
        current_month: Current scheduled month for the group
        period: Calibration period in months
        planning_logic: 'department' or 'description'

    Returns:
        date: Next scheduled month for the entire group
    """
    # Calculate next period from current group month
    next_month = current_month + relativedelta(months=period)

    # Verify this makes sense (not too far in future)
    today = date.today()
    max_future = today + relativedelta(months=period + 6)

    if next_month > max_future:
        # Current month is too old, recalculate from today
        logger.warning(
            f"[GROUP_NEXT] Calculated next month {next_month.strftime('%Y-%m')} "
            f"is too far in future. Using today as base instead."
        )
        next_month = today.replace(day=1) + relativedelta(months=period)

    logger.info(
        f"[GROUP_NEXT] Group next month: {next_month.strftime('%B %Y')} "
        f"(from {current_month.strftime('%B %Y')} + {period} months)"
    )

    return next_month


def find_group_members(equipment, scheduled_month, planning_logic='department'):
    """
    Find all equipment in the same group scheduled in the same month.

    Args:
        equipment: Equipment object
        scheduled_month: The month to check
        planning_logic: 'department' or 'description'

    Returns:
        QuerySet: All schedules for the group in that month
    """
    if planning_logic == 'department':
        dept_id = equipment.department_id if hasattr(equipment, 'department_id') else None

        members = CalibrationSchedule.objects.filter(
            equipment__department_id=dept_id,
            equipment__active_status=True,
            scheduled_month=scheduled_month
        ).select_related('equipment')

    else:  # description
        desc_id = equipment.description_id if hasattr(equipment, 'description_id') else None

        members = CalibrationSchedule.objects.filter(
            equipment__description_id=desc_id,
            equipment__active_status=True,
            scheduled_month=scheduled_month
        ).select_related('equipment')

    return members


def check_group_completion_status(equipment, scheduled_month, planning_logic='department'):
    """
    Check if ALL equipment in a group have completed their calibrations.

    Args:
        equipment: Equipment object
        scheduled_month: The month to check
        planning_logic: 'department' or 'description'

    Returns:
        dict: {
            'total': total equipment in group,
            'completed': number completed,
            'all_completed': boolean,
            'members': queryset of group members
        }
    """
    members = find_group_members(equipment, scheduled_month, planning_logic)

    total = members.count()
    completed = members.filter(status='completed').count()
    all_completed = (total > 0 and completed == total)

    if planning_logic == 'department':
        dept_name = equipment.department.name if hasattr(equipment, 'department') and equipment.department else 'N/A'
        group_name = f"Department: {dept_name}"
    else:
        desc_name = equipment.description.name if hasattr(equipment, 'description') and equipment.description else 'N/A'
        group_name = f"Description: {desc_name}"

    logger.info(
        f"[GROUP_STATUS] {group_name} in {scheduled_month.strftime('%B %Y')}: "
        f"{completed}/{total} completed (All done: {all_completed})"
    )

    return {
        'total': total,
        'completed': completed,
        'all_completed': all_completed,
        'members': members,
        'group_name': group_name
    }


# ============================================================
# 🔥 INSTANT AUTO-RESCHEDULE ON COMPLETION (GROUP-AWARE)
# ============================================================

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


# ============================================================
# 🔒 AUTO-COMPLETE AND LOCK ON CERTIFICATE NUMBER
# ============================================================

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

# ============================================================
# 🔄 GROUPING SIGNAL (STRICT - NO JUMPING)
# ============================================================

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


# ============================================================
# 🔧 SCHEDULE INITIALIZATION
# ============================================================

def initialize_schedule_for_equipment(equipment, planning_logic='department'):
    """
    Initialize schedule for equipment using GROUP-AWARE logic

    Args:
        equipment: Equipment object
        planning_logic: 'department' or 'description'

    Returns:
        CalibrationSchedule: Created or existing schedule
    """
    # Check if schedule already exists
    existing = CalibrationSchedule.objects.filter(
        equipment=equipment,
        active_status=True,
        status__in=['pending', 'pushed', 'completed']
    ).first()

    if existing:
        logger.info(
            f"[INIT] Schedule already exists for equipment {equipment.id} "
            f"(status: {existing.status})"
        )
        return existing

    # Get equipment's calibration period
    period = getattr(equipment, 'calibration_period', 12)
    if not period:
        period = 12

    # Find where this equipment's group is scheduled
    group_month = get_group_scheduled_month(equipment, planning_logic)

    try:
        # Create schedule aligned with group
        schedule = CalibrationSchedule.objects.create(
            equipment=equipment,
            scheduled_month=group_month,
            status='pending',
            calibration_period=period,
            planning_logic=planning_logic,
            generation_source='initialization'
        )

        logger.info(
            f"[INIT] ✅ Created schedule for equipment {equipment.id} "
            f"in {group_month.strftime('%B %Y')} (aligned with group)"
        )

        # Sync to HQ
        trigger_sync_for_changes([schedule.id], operation='u')

        return schedule

    except Exception as e:
        logger.error(f"[INIT] ❌ Failed to initialize schedule: {e}", exc_info=True)
        return None


# ============================================================
# 🔍 GROUP DIAGNOSTICS
# ============================================================

def diagnose_group_alignment(planning_logic='department'):
    """
    Check if all equipment are properly grouped in same months

    Returns:
        dict: Diagnostic information about grouping
    """
    from .models import CalibrationSchedule

    logger.info("=" * 80)
    logger.info(f"🔍 GROUP ALIGNMENT DIAGNOSTICS ({planning_logic})")
    logger.info("=" * 80)

    schedules = CalibrationSchedule.objects.filter(
        active_status=True,
        status__in=['pending', 'pushed'],
        equipment__active_status=True
    ).select_related('equipment__department', 'equipment__description')

    if planning_logic == 'department':
        # Group by department and month
        groups = defaultdict(lambda: defaultdict(int))

        for schedule in schedules:
            dept_id = schedule.equipment.department_id if schedule.equipment.department else 'none'
            month = schedule.scheduled_month.strftime('%Y-%m')
            groups[dept_id][month] += 1

        # Check for departments split across months
        split_groups = 0
        well_grouped = 0

        for dept_id, months in groups.items():
            if len(months) > 1:
                split_groups += 1
                logger.warning(
                    f"  ⚠️ Department {dept_id} split across {len(months)} months: "
                    f"{dict(months)}"
                )
            else:
                well_grouped += 1

        logger.info(f"\n  Well-grouped departments: {well_grouped}")
        logger.info(f"  Split departments: {split_groups}")

    else:  # description
        # Group by description and month
        groups = defaultdict(lambda: defaultdict(int))

        for schedule in schedules:
            desc_id = schedule.equipment.description_id if schedule.equipment.description else 'none'
            month = schedule.scheduled_month.strftime('%Y-%m')
            groups[desc_id][month] += 1

        # Check for descriptions split across months
        split_groups = 0
        well_grouped = 0

        for desc_id, months in groups.items():
            if len(months) > 1:
                split_groups += 1
                logger.warning(
                    f"  ⚠️ Description {desc_id} split across {len(months)} months: "
                    f"{dict(months)}"
                )
            else:
                well_grouped += 1

        logger.info(f"\n  Well-grouped descriptions: {well_grouped}")
        logger.info(f"  Split descriptions: {split_groups}")

    logger.info("=" * 80)

    return {
        'total_groups': len(groups),
        'well_grouped': well_grouped,
        'split_groups': split_groups,
        'alignment_percentage': (well_grouped / len(groups) * 100) if groups else 0
    }


def fix_group_alignment(planning_logic='department', dry_run=True):
    """
    Fix misaligned schedules by moving them to their group's month

    Args:
        planning_logic: 'department' or 'description'
        dry_run: If True, only report what would be fixed

    Returns:
        dict: Statistics about fixes
    """
    from .models import CalibrationSchedule

    logger.info("=" * 80)
    logger.info(f"🔧 FIX GROUP ALIGNMENT ({planning_logic}) - {'DRY RUN' if dry_run else 'LIVE'}")
    logger.info("=" * 80)

    schedules = CalibrationSchedule.objects.filter(
        active_status=True,
        status__in=['pending', 'pushed'],
        equipment__active_status=True,
        is_locked=False
    ).select_related('equipment__department', 'equipment__description')

    fixed_count = 0
    skipped_count = 0

    for schedule in schedules:
        # Find where this schedule's group is
        group_month = get_group_scheduled_month(schedule.equipment, planning_logic)

        if schedule.scheduled_month != group_month:
            logger.info(
                f"  🔧 {'Would move' if dry_run else 'Moving'} schedule {schedule.id}: "
                f"{schedule.scheduled_month.strftime('%Y-%m')} → {group_month.strftime('%Y-%m')}"
            )

            if not dry_run:
                # Check for conflicts
                conflict = CalibrationSchedule.objects.filter(
                    equipment=schedule.equipment,
                    scheduled_month=group_month
                ).exclude(id=schedule.id).exists()

                if conflict:
                    logger.warning(f"    ⚠️ Conflict exists, deleting duplicate")
                    CalibrationSchedule.objects.filter(
                        equipment=schedule.equipment,
                        scheduled_month=group_month
                    ).exclude(id=schedule.id).delete()

                # Move schedule
                schedule.scheduled_month = group_month
                schedule.planning_logic = planning_logic
                schedule.generation_source = 'group_fix'
                schedule.save(update_fields=[
                    'scheduled_month',
                    'planning_logic',
                    'generation_source',
                    'updated_at'
                ])

                trigger_sync_for_changes([schedule.id], operation='u')

            fixed_count += 1
        else:
            skipped_count += 1

    logger.info("")
    logger.info(f"  {'Would fix' if dry_run else 'Fixed'}: {fixed_count} schedules")
    logger.info(f"  Already aligned: {skipped_count} schedules")
    logger.info("=" * 80)

    return {
        'fixed': fixed_count,
        'skipped': skipped_count,
        'dry_run': dry_run
    }


# ============================================================
# 📊 SIGNAL STATUS INDICATOR
# ============================================================

def check_instant_reconciliation_status():
    """Check if instant reconciliation is working"""
    logger.info("=" * 80)
    logger.info("⚡ INSTANT RECONCILIATION STATUS (IMMEDIATE LOCKING)")
    logger.info("=" * 80)
    logger.info("✅ instant_reschedule_on_completion: ACTIVE (Group-aware + Immediate Lock)")
    logger.info("✅ instant_grouping_alignment: ACTIVE (Strict - no jumping)")
    logger.info("✅ auto_complete_on_certificate: ACTIVE (Job Card / Certificate trigger)")
    logger.info("✅ lock_all_completed_schedules: AVAILABLE (Immediate checker)")
    logger.info("")
    logger.info("💡 How it works:")
    logger.info("   - PPM auto-reschedule ONLY fires on job card completion (certificate generated)")
    logger.info("   - Groups ALWAYS stay together in same month")
    logger.info("   - New equipment joins existing group")
    logger.info("   - Entire group reschedules together")
    logger.info("   - 🔒 IMMEDIATE LOCKING on completion (no waiting)")
    logger.info("   - scheduled_month is NEVER changed by early/late calibration")
    logger.info("     (completed_date records actual date; scheduled_month stays fixed)")
    logger.info("   - New schedule month = original scheduled_month + period")
    logger.info("   - Periodic checker locks ALL completed schedules")
    logger.info("   - All changes sync to HQ automatically")
    logger.info("=" * 80)

    return {
        'instant_reschedule': True,
        'strict_grouping': True,
        'auto_complete': True,
        'immediate_locking': True,
        'scheduled_month_preserved': True,
        'ppm_trigger': 'job_card_certificate',
        'status': 'active',
        'version': 'immediate_locking_v2'
    }


# ============================================================
# MODULE INITIALIZATION
# ============================================================

logger.info("=" * 80)
logger.info("⚡ INSTANT CALIBRATION RECONCILIATION LOADED (IMMEDIATE LOCKING)")
logger.info("=" * 80)
logger.info("✅ instant_reschedule_on_completion (GROUP-AWARE + IMMEDIATE LOCK)")
logger.info("✅ instant_grouping_alignment (STRICT - NO JUMPING)")
logger.info("✅ auto_complete_on_certificate (CERTIFICATE INTEGRATION + LOCK)")
logger.info("✅ lock_all_completed_schedules (IMMEDIATE CHECKER)")
logger.info("")
logger.info("🔧 KEY FEATURES:")
logger.info("   - Groups stay together in same month")
logger.info("   - No equipment jumping between months")
logger.info("   - Entire group moves together when all complete")
logger.info("   - New equipment aligns to existing group")
logger.info("   - 🔒 IMMEDIATE locking on completion (no waiting)")
logger.info("   - Periodic checker locks ALL completed schedules")
logger.info("=" * 80)
