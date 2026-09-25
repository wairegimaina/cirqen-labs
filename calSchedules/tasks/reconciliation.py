"""calSchedules.tasks.reconciliation — beat wrappers around reconciliation.py."""
from celery import shared_task
from datetime import date, datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from django.db import transaction
from django.db.models import Q, Count
import logging
from Inventory.models import Equipment, Department, EquipmentDescription
from ..models import CalibrationSchedule
from workshop.models import Workshop
from .. import grouping
logger = logging.getLogger(__name__)
from ..reconciliation import full_reconciliation, auto_reschedule_completed_calibrations, ensure_grouping_consistency, diagnose_schedules
from ..locker import lock_completed_schedules, auto_lock_and_reschedule, get_lock_status
PROTECTED_SOURCES = ['signal', 'locker', 'job_card', 'plan']


@shared_task(name="calSchedules.tasks.run_calibration_reconciliation")
def run_calibration_reconciliation(planning_logic=None, fix_issues=True, dry_run=False, auto_reschedule=True, check_hours=1):
    """Celery wrapper for full_reconciliation(). Performs a complete reconciliation pass."""
    logger.info(f"[TASK:run_calibration_reconciliation] Starting (logic={planning_logic}, fix={fix_issues}, dry_run={dry_run}, check_hours={check_hours})")
    result = full_reconciliation(
        planning_logic=planning_logic or 'department',
        fix_issues=fix_issues, dry_run=dry_run, auto_reschedule=auto_reschedule,
    )
    logger.info(f"[TASK:run_calibration_reconciliation] Complete: {result}")
    return result


@shared_task(name="calSchedules.tasks.auto_reschedule_completed_task")
def auto_reschedule_completed_task(check_hours=0.5):
    """Celery wrapper for auto_reschedule_completed_calibrations()."""
    logger.info(f"[TASK:auto_reschedule_completed_task] Starting (check_hours={check_hours})")
    result = auto_reschedule_completed_calibrations(check_hours=check_hours)

    # Planned workshops: completions that arrived through sync (raw SQL, so
    # no signal fired) get their next schedule here, within 15 minutes.
    from scheduling.planner import run_all
    runs = run_all('calibration')
    result['planned_created'] = sum(len(r.created) for r in runs)

    logger.info(
        f"[TASK:auto_reschedule_completed_task] Complete: "
        f"rescheduled={result.get('rescheduled', 0)}, "
        f"waiting={result.get('waiting_for_group', 0)}, "
        f"skipped={result.get('skipped', 0)}, "
        f"planned={result['planned_created']}"
    )
    return result


@shared_task(name="calSchedules.tasks.ensure_grouping_consistency_task")
def ensure_grouping_consistency_task(planning_logic=None, fix_misalignments=True, dry_run=False, calibration_period=12):
    """Celery wrapper for ensure_grouping_consistency(). Detects and fixes split groups."""
    logger.info(f"[TASK:ensure_grouping_consistency_task] Starting (logic={planning_logic}, fix={fix_misalignments}, dry_run={dry_run})")
    result = ensure_grouping_consistency(
        planning_logic=planning_logic or 'department',
        fix_misalignments=fix_misalignments, dry_run=dry_run, calibration_period=calibration_period,
    )
    logger.info(
        f"[TASK:ensure_grouping_consistency_task] Complete: "
        f"status={result.get('status')}, "
        f"alignment={result.get('alignment_percentage', 'n/a')}%, "
        f"split_groups={result.get('split_groups', 'n/a')}"
    )
    return result


@shared_task(name="calSchedules.tasks.diagnose_calibration_schedules")
def diagnose_calibration_schedules():
    """Celery wrapper for diagnose_schedules(). Daily read-only diagnostic pass."""
    logger.info("[TASK:diagnose_calibration_schedules] Starting daily diagnostics")
    result = diagnose_schedules()
    logger.info(f"[TASK:diagnose_calibration_schedules] Complete: {result}")
    return result
