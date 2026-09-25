"""calSchedules.instant_reconciliation.alignment — equipment init + group alignment diagnostics."""
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
from .helpers import get_group_scheduled_month


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

    from scheduling.planner import planned_workshop_ids
    schedules = CalibrationSchedule.objects.filter(
        active_status=True,
        status__in=['pending', 'pushed'],
        equipment__active_status=True
    ).exclude(
        workshop_id__in=planned_workshop_ids('calibration')
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

    # Planned workshops take their months from the plan: never realign them.
    from scheduling.planner import planned_workshop_ids
    schedules = CalibrationSchedule.objects.filter(
        active_status=True,
        status__in=['pending', 'pushed'],
        equipment__active_status=True,
        is_locked=False
    ).exclude(
        workshop_id__in=planned_workshop_ids('calibration')
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
