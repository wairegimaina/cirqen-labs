"""
calSchedules/reconciliation.py - UPDATED WITH GROUP AWARENESS
=============================================================

This reconciliation system now works WITH instant_reconciliation.py to provide:
1. Batch reconciliation that respects group-based scheduling
2. Auto-reschedules using group-aware logic
3. Ensures grouping consistency with instant_reconciliation
4. Handles bulk changes while maintaining groups
5. Integrates with sync engine

KEY: This now uses instant_reconciliation functions to maintain strict grouping!
"""

from datetime import date, datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from django.db import transaction
from django.db.models import Q, Max, Min, Count
import logging
import uuid
from .models import CalibrationSchedule
from Inventory.models import Equipment
from . import grouping

logger = logging.getLogger(__name__)


# ============================================================
# 🔥 SYNC INTEGRATION
# ============================================================

def trigger_sync_for_changes(schedule_ids, operation='u'):
    """
    Trigger sync for changed schedules

    Args:
        schedule_ids: List of schedule IDs that changed
        operation: 'u' for update/create, 'd' for delete
    """
    if not schedule_ids:
        return 0

    try:
        from sync_engine.upload_manager import UploadManager
        upload_manager = UploadManager.get_instance()

        if not upload_manager:
            logger.warning(
                f"[SYNC] ⚠️ UploadManager not available - "
                f"{len(schedule_ids)} schedules will sync on next cycle"
            )
            return 0

        # Get full schedule data
        schedules = CalibrationSchedule.objects.filter(id__in=schedule_ids)
        synced_count = 0

        for schedule in schedules:
            schedule_data = {
                'id': str(schedule.id),
                'equipment_id': str(schedule.equipment_id) if schedule.equipment_id else None,
                'scheduled_month': schedule.scheduled_month.isoformat() if schedule.scheduled_month else None,
                'status': schedule.status,
                'calibration_period': schedule.calibration_period,
                'planning_logic': schedule.planning_logic,
                'completed_date': schedule.completed_date.isoformat() if schedule.completed_date else None,
                'generation_source': getattr(schedule, 'generation_source', 'reconciliation'),
                'is_locked': getattr(schedule, 'is_locked', False),
                'created_at': schedule.created_at.isoformat() if hasattr(schedule, 'created_at') and schedule.created_at else datetime.now(timezone.utc).isoformat(),
                'updated_at': schedule.updated_at.isoformat() if hasattr(schedule, 'updated_at') and schedule.updated_at else datetime.now(timezone.utc).isoformat(),
            }

            event = {
                'event_id': f"reconcile-{uuid.uuid4()}",
                'table': 'public.calSchedules_calibrationschedule',
                'row_id': str(schedule.id),
                'operation': operation,
                'data': schedule_data,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'source': 'reconciliation',
            }

            upload_manager.add_event(event)
            synced_count += 1

        logger.info(f"[SYNC] ✅ Queued {synced_count}/{len(schedule_ids)} schedules for sync")
        return synced_count

    except ImportError:
        logger.warning(f"[SYNC] ⚠️ sync_engine not available - {len(schedule_ids)} schedules skipped")
        return 0
    except Exception as e:
        logger.error(f"[SYNC] ❌ Sync trigger failed: {e}", exc_info=True)
        return 0


# ============================================================
# 🔄 GROUP-AWARE AUTO-RESCHEDULE
# ============================================================

def auto_reschedule_completed_calibrations(check_hours=24):
    """
    ✅ UPDATED: Auto-reschedule completed calibrations using GROUP-AWARE logic

    Now uses instant_reconciliation functions to maintain strict grouping:
    - Waits for entire group to complete
    - Reschedules all group members together
    - Maintains month alignment

    Args:
        check_hours: How many hours back to check for completed schedules

    Returns:
        dict: Results with rescheduled count
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=check_hours)

    logger.info(
        f"[AUTO_RESCHEDULE] Starting group-aware rescheduling "
        f"(checking last {check_hours} hours)"
    )

    # Get recently completed schedules
    completed_schedules = CalibrationSchedule.objects.filter(
        status='completed',
        equipment__active_status=True,
        updated_at__gte=cutoff_time
    ).select_related('equipment__department', 'equipment__description')

    if not completed_schedules.exists():
        logger.info("[AUTO_RESCHEDULE] No recently completed schedules")
        return {
            'rescheduled': 0,
            'waiting_for_group': 0,
            'skipped': 0
        }

    # ✅ NEW: Use instant_reconciliation for group-aware rescheduling
    try:
        from .instant_reconciliation import check_group_completion_status, get_next_group_month

        rescheduled_count = 0
        waiting_count = 0
        skipped_count = 0
        processed_groups = set()

        for schedule in completed_schedules:
            planning_logic = schedule.planning_logic or 'department'

            # Create unique group key
            if planning_logic == 'department':
                group_key = f"dept_{schedule.equipment.department_id}_{schedule.scheduled_month}"
            else:
                group_key = f"desc_{schedule.equipment.description_id}_{schedule.scheduled_month}"

            # Skip if already processed this group
            if group_key in processed_groups:
                continue

            processed_groups.add(group_key)

            # ✅ Check if entire group is complete
            group_status = check_group_completion_status(
                schedule.equipment,
                schedule.scheduled_month,
                planning_logic
            )

            if group_status['all_completed']:
                # ✅ Entire group complete - reschedule all together
                period = schedule.calibration_period or 12
                next_month = get_next_group_month(
                    schedule.equipment,
                    schedule.scheduled_month,
                    period,
                    planning_logic
                )

                # Get all members
                completed_members = group_status['members'].filter(status='completed')

                with transaction.atomic():
                    for member in completed_members:
                        # One open schedule per equipment, in any month
                        existing = CalibrationSchedule.open_schedules().filter(
                            equipment=member.equipment
                        ).exists()

                        if not existing:
                            # Create new schedule
                            new_schedule = CalibrationSchedule.objects.create(
                                equipment=member.equipment,
                                scheduled_month=next_month,
                                status='pending',
                                calibration_period=period,
                                planning_logic=planning_logic,
                                generation_source='reconciliation'
                            )

                            # Lock the old completed schedule
                            if hasattr(member, 'is_locked'):
                                member.is_locked = True
                                member.save(update_fields=['is_locked'])

                            rescheduled_count += 1

                            logger.info(
                                f"[AUTO_RESCHEDULE] ✅ Rescheduled {member.equipment.id} "
                                f"to {next_month.strftime('%B %Y')} (group complete)"
                            )
                        else:
                            skipped_count += 1

                logger.info(
                    f"[AUTO_RESCHEDULE] Group rescheduled: "
                    f"{group_status['completed']}/{group_status['total']} equipment "
                    f"to {next_month.strftime('%B %Y')}"
                )
            else:
                # Group not complete yet - wait
                waiting_count += group_status['completed']
                logger.info(
                    f"[AUTO_RESCHEDULE] ⏸️ Waiting for group: "
                    f"{group_status['completed']}/{group_status['total']} complete"
                )

        result = {
            'rescheduled': rescheduled_count,
            'waiting_for_group': waiting_count,
            'skipped': skipped_count,
            'groups_processed': len(processed_groups)
        }

        logger.info(
            f"[AUTO_RESCHEDULE] Complete: "
            f"{rescheduled_count} rescheduled, "
            f"{waiting_count} waiting for group, "
            f"{skipped_count} skipped"
        )

        return result

    except ImportError:
        logger.warning(
            "[AUTO_RESCHEDULE] instant_reconciliation not available - "
            "falling back to basic rescheduling"
        )
        # Fallback to basic rescheduling without group awareness
        return _basic_auto_reschedule(completed_schedules)


def _basic_auto_reschedule(completed_schedules):
    """
    Fallback: Basic rescheduling without group awareness
    Used only if instant_reconciliation is not available
    """
    rescheduled_count = 0
    skipped_count = 0

    for schedule in completed_schedules:
        period = schedule.calibration_period or 12
        next_month = grouping.next_period_month(schedule.scheduled_month, period)

        existing = CalibrationSchedule.open_schedules().filter(
            equipment=schedule.equipment
        ).exists()

        if not existing:
            CalibrationSchedule.objects.create(
                equipment=schedule.equipment,
                scheduled_month=next_month,
                status='pending',
                calibration_period=period,
                planning_logic=schedule.planning_logic,
                generation_source='reconciliation'
            )
            rescheduled_count += 1
        else:
            skipped_count += 1

    return {
        'rescheduled': rescheduled_count,
        'waiting_for_group': 0,
        'skipped': skipped_count
    }


# ============================================================
# 🎯 GROUP-AWARE CONSISTENCY CHECKER
# ============================================================

def ensure_grouping_consistency(
    planning_logic='department',
    fix_misalignments=False,
    dry_run=True,
    calibration_period=None,  # accepted for compatibility, not used directly
    **kwargs
):
    """
    ✅ UPDATED: Ensure all equipment in same group are scheduled together

    Now uses instant_reconciliation's alignment checking:
    - Detects split groups
    - Fixes misalignments
    - Maintains strict grouping

    Args:
        planning_logic: 'department' or 'description'
        fix_misalignments: Whether to fix issues found
        dry_run: If True, only report what would be fixed

    Returns:
        dict: Results with alignment info
    """
    logger.info(
        f"[GROUPING_CHECK] Starting consistency check "
        f"(logic={planning_logic}, fix={fix_misalignments}, dry_run={dry_run})"
    )

    try:
        from .instant_reconciliation import diagnose_group_alignment, fix_group_alignment

        # ✅ Use instant_reconciliation's diagnostic tools
        diagnosis = diagnose_group_alignment(planning_logic)

        logger.info(
            f"[GROUPING_CHECK] Diagnosis complete: "
            f"{diagnosis['well_grouped']} well-grouped, "
            f"{diagnosis['split_groups']} split groups, "
            f"{diagnosis['alignment_percentage']:.1f}% aligned"
        )

        if fix_misalignments and diagnosis['split_groups'] > 0:
            # ✅ Use instant_reconciliation's fix function
            fix_result = fix_group_alignment(planning_logic, dry_run)

            logger.info(
                f"[GROUPING_CHECK] Fix {'would fix' if dry_run else 'fixed'}: "
                f"{fix_result['fixed']} schedules, "
                f"skipped {fix_result['skipped']}"
            )

            return {
                'status': 'fixed' if not dry_run else 'would_fix',
                'total_groups': diagnosis['total_groups'],
                'well_grouped': diagnosis['well_grouped'],
                'split_groups': diagnosis['split_groups'],
                'alignment_percentage': diagnosis['alignment_percentage'],
                'fixed': fix_result['fixed'],
                'skipped': fix_result['skipped']
            }
        else:
            return {
                'status': 'checked',
                'total_groups': diagnosis['total_groups'],
                'well_grouped': diagnosis['well_grouped'],
                'split_groups': diagnosis['split_groups'],
                'alignment_percentage': diagnosis['alignment_percentage'],
                'action_needed': diagnosis['split_groups'] > 0
            }

    except ImportError:
        logger.warning(
            "[GROUPING_CHECK] instant_reconciliation not available - "
            "using basic consistency check"
        )
        return _basic_grouping_check(planning_logic)


def _basic_grouping_check(planning_logic='department'):
    """
    Fallback: Basic grouping check without instant_reconciliation
    """
    schedules = CalibrationSchedule.objects.filter(
        equipment__active_status=True,
        status__in=['pending', 'pushed']
    )

    groups = schedules.values(
        grouping.group_field_lookup(planning_logic), 'scheduled_month'
    ).annotate(count=Count('id'))

    return {
        'status': 'checked',
        'total_groups': groups.count(),
        'message': 'Basic check - install instant_reconciliation for detailed analysis'
    }


# ============================================================
# 📊 FULL RECONCILIATION
# ============================================================

def full_reconciliation(
    planning_logic='department',
    fix_issues=True,
    dry_run=False,
    auto_reschedule=True,  # accepted for compatibility, behaviour handled internally
    **kwargs
):
    """
    ✅ UPDATED: Full reconciliation with group awareness

    Performs complete reconciliation while respecting strict grouping:
    1. Auto-reschedule completed calibrations (group-aware)
    2. Check and fix grouping consistency
    3. Lock completed schedules
    4. Sync changes

    Args:
        planning_logic: 'department' or 'description'
        fix_issues: Whether to fix problems found
        dry_run: If True, only report what would be done

    Returns:
        dict: Complete reconciliation results
    """
    start_time = datetime.now(timezone.utc)

    logger.info(
        f"[FULL_RECONCILE] Starting full reconciliation "
        f"(logic={planning_logic}, fix={fix_issues}, dry_run={dry_run})"
    )

    results = {
        'started_at': start_time.isoformat(),
        'planning_logic': planning_logic,
        'dry_run': dry_run
    }

    # Step 1: Auto-reschedule completed (group-aware)
    logger.info("[FULL_RECONCILE] Step 1: Auto-rescheduling completed calibrations")
    reschedule_result = auto_reschedule_completed_calibrations(check_hours=168)  # Last 7 days
    results['auto_reschedule'] = reschedule_result

    # Step 2: Check grouping consistency
    logger.info("[FULL_RECONCILE] Step 2: Checking grouping consistency")
    grouping_result = ensure_grouping_consistency(
        planning_logic=planning_logic,
        fix_misalignments=fix_issues,
        dry_run=dry_run
    )
    results['grouping_check'] = grouping_result

    # Step 3: Lock completed schedules
    logger.info("[FULL_RECONCILE] Step 3: Locking completed schedules")
    lock_result = _lock_completed_schedules()
    results['lock_completed'] = lock_result

    # Step 4: Sync changes
    if not dry_run:
        logger.info("[FULL_RECONCILE] Step 4: Syncing changes")

        # Get all schedules modified in last hour
        recent_changes = CalibrationSchedule.objects.filter(
            updated_at__gte=start_time
        ).values_list('id', flat=True)

        sync_count = trigger_sync_for_changes(list(recent_changes))
        results['sync'] = {'synced': sync_count}
    else:
        results['sync'] = {'synced': 0, 'dry_run': True}

    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    results['completed_at'] = end_time.isoformat()
    results['duration_seconds'] = duration

    logger.info(
        f"[FULL_RECONCILE] Complete in {duration:.1f}s: "
        f"Rescheduled {reschedule_result['rescheduled']}, "
        f"Alignment {grouping_result.get('alignment_percentage', 0):.1f}%, "
        f"Locked {lock_result['locked']}"
    )

    return results


def _lock_completed_schedules():
    """
    Lock completed schedules to prevent modification
    """
    completed = CalibrationSchedule.objects.filter(
        status='completed'
    )

    # Check if is_locked field exists
    if hasattr(CalibrationSchedule, 'is_locked'):
        to_lock = completed.filter(is_locked=False)
        locked_count = to_lock.update(is_locked=True)

        return {
            'locked': locked_count,
            'already_locked': completed.count() - locked_count
        }
    else:
        return {
            'locked': 0,
            'message': 'is_locked field not available'
        }


# ============================================================
# 🔍 DIAGNOSTICS
# ============================================================

def diagnose_schedules():
    """
    ✅ UPDATED: Comprehensive diagnostics with group awareness

    Returns:
        dict: Diagnostic information
    """
    logger.info("[DIAGNOSTICS] Running schedule diagnostics")

    total_schedules = CalibrationSchedule.objects.filter(
        equipment__active_status=True
    ).count()

    status_counts = CalibrationSchedule.objects.filter(
        equipment__active_status=True
    ).values('status').annotate(count=Count('id'))

    # Check for locked schedules
    if hasattr(CalibrationSchedule, 'is_locked'):
        locked_count = CalibrationSchedule.objects.filter(
            is_locked=True
        ).count()
    else:
        locked_count = 0

    # Try to get group alignment info
    try:
        from .instant_reconciliation import diagnose_group_alignment

        dept_alignment = diagnose_group_alignment('department')
        desc_alignment = diagnose_group_alignment('description')

        alignment_info = {
            'department': {
                'alignment_percentage': dept_alignment['alignment_percentage'],
                'split_groups': dept_alignment['split_groups']
            },
            'description': {
                'alignment_percentage': desc_alignment['alignment_percentage'],
                'split_groups': desc_alignment['split_groups']
            }
        }
    except ImportError:
        alignment_info = {
            'message': 'Install instant_reconciliation for alignment details'
        }

    diagnostics = {
        'total_schedules': total_schedules,
        'status_breakdown': {item['status']: item['count'] for item in status_counts},
        'locked_schedules': locked_count,
        'alignment': alignment_info,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }

    logger.info(f"[DIAGNOSTICS] Total schedules: {total_schedules}")
    logger.info(f"[DIAGNOSTICS] Locked: {locked_count}")

    return diagnostics


# ============================================================
# 🚀 CONVENIENCE FUNCTIONS
# ============================================================

def quick_reconcile():
    """Quick reconciliation - fixes most common issues"""
    return full_reconciliation(
        planning_logic='department',
        fix_issues=True,
        dry_run=False
    )


def check_status():
    """Check system status without making changes"""
    return full_reconciliation(
        planning_logic='department',
        fix_issues=False,
        dry_run=True
    )
