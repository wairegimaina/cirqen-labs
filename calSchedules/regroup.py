"""Targeted regrouping — move a chosen set of schedules, not the whole estate.

Until now the only way to regroup was ``smart_reorganize_on_logic_change``,
which flips the planning logic for **every** pending schedule at once. That is
the right tool for "we now plan by description rather than by department", and
the wrong one for the everyday case: this ward is being refurbished in March,
move its twelve devices to May; or these four pumps were scheduled apart and
should be visited together.

This module provides that everyday operation. It reuses the existing move
primitive (``tasks.helpers._apply_schedule_update``) so conflict detection,
duplicate removal and the three-layer protection — completed, locked, and
protected generation sources — behave exactly as they do during a full
reorganisation. Nothing here can move a schedule that a reorganisation would
refuse to move.

Every function is safe to call with ``dry_run=True`` first, and returns the
same shape either way, so a caller can show the operator what would happen
before it happens.
"""

import logging
from datetime import date

from django.db import transaction

from . import grouping
from .models import CalibrationSchedule
from .instant_reconciliation.helpers import suppress_alignment
from .tasks.helpers import _apply_schedule_update, _is_protected

logger = logging.getLogger(__name__)


def _blank_counts():
    """The counter keys ``_apply_schedule_update`` expects to increment.

    It does ``counts['normalized'] += 1`` on success and catches its own
    exceptions, so a missing key turns into a silent skip rather than an error —
    which is precisely what happened the first time this was wired up.
    """
    return {
        "normalized": 0,
        "skipped": 0,
        "duplicates_removed": 0,
        "errors": 0,
    }


def _month_floor(value):
    """Schedules are keyed by month, so a target is always the 1st."""
    if value is None:
        return None
    if isinstance(value, str):
        value = date.fromisoformat(value if len(value) > 7 else f"{value}-01")
    return value.replace(day=1)


def preview_regroup(schedule_ids, target_month, planning_logic=None):
    """What ``regroup`` would do, without doing it.

    Returns the same structure as :func:`regroup` with ``dry_run`` set, so a
    confirmation screen and the real call read identically.
    """
    return regroup(schedule_ids, target_month, planning_logic=planning_logic, dry_run=True)


@transaction.atomic
def regroup(schedule_ids, target_month, planning_logic=None, calibration_period=None,
            dry_run=False, actor=None):
    """Move the given schedules into ``target_month`` as one group.

    Args:
        schedule_ids: the schedules to move.
        target_month: any date in the destination month; normalised to the 1st.
        planning_logic: optionally restamp the logic while moving. Left alone
            when omitted, because moving a group and changing how the estate is
            grouped are different decisions and conflating them is how the
            planning-logic vocabularies drifted apart in the first place.
        calibration_period: optionally restamp the interval. Left alone when
            omitted.
        dry_run: report what would happen and roll back.
        actor: user for the audit line, if any.

    Returns:
        dict with ``moved``, ``skipped``, ``counts`` and ``target_month``.
        ``skipped`` carries a reason per schedule so the operator is told *why*
        something did not move rather than just that it did not.
    """
    target_month = _month_floor(target_month)
    if target_month is None:
        raise ValueError("A target month is required.")

    schedules = list(
        CalibrationSchedule.objects
        .filter(id__in=list(schedule_ids), active_status=True)
        .select_related("equipment", "equipment__department", "equipment__description")
    )

    counts = _blank_counts()
    moved, skipped = [], []

    # The whole selection moves as one. Group alignment is suppressed for the
    # duration: it would otherwise pull each schedule back to the group's old
    # month as it was saved, and the move could never get started.
    with suppress_alignment():
        for schedule in schedules:
            reason = protection_reason(schedule)
            if reason:
                counts["skipped"] += 1
                skipped.append({"id": str(schedule.id), "equipment": _label(schedule), "reason": reason})
                continue

            logic = planning_logic or schedule.planning_logic
            period = calibration_period or schedule.calibration_period
            before = schedule.scheduled_month

            try:
                changed = _apply_schedule_update(schedule, target_month, logic, period, counts)
            except Exception:
                counts["errors"] += 1
                logger.exception(
                    "[REGROUP] Failed to move schedule %s to %s", schedule.id, target_month
                )
                skipped.append({
                    "id": str(schedule.id), "equipment": _label(schedule),
                    "reason": "An error occurred while moving this schedule; it was left where it was.",
                })
                continue

            if changed:
                moved.append({
                    "id": str(schedule.id),
                    "equipment": _label(schedule),
                    "from": before,
                    "to": target_month,
                })
            else:
                skipped.append({
                    "id": str(schedule.id), "equipment": _label(schedule),
                    "reason": "Already in this month with the same logic and interval.",
                })

    logger.info(
        "[REGROUP] %s%s schedules to %s by %s (moved=%d skipped=%d)",
        "DRY RUN: " if dry_run else "",
        len(schedules), target_month, getattr(actor, "username", "system"),
        len(moved), len(skipped),
    )

    if dry_run:
        transaction.set_rollback(True)

    return {
        "target_month": target_month,
        "moved": moved,
        "skipped": skipped,
        "counts": counts,
        "dry_run": dry_run,
    }


def protection_reason(schedule):
    """Why this schedule cannot be moved, or None when it can.

    ``_is_protected`` answers yes/no; an operator needs to know which of the
    three protections applied, because the remedy differs: a completed
    calibration is history, a locked one can be unlocked, and a signal-created
    one will be regenerated.
    """
    if schedule.status == "completed":
        return "Completed — a finished calibration is not rescheduled."
    if getattr(schedule, "is_locked", False):
        return "Locked — unlock it first if it really should move."
    if schedule.generation_source == "plan" or schedule.workshop_id in _planned_workshops():
        return ("Placed by the workshop's scheduling plan — change the plan's months "
                "instead of moving schedules by hand.")
    if schedule.generation_source in ("signal", "locker", "job_card"):
        return (
            f"Created automatically by {schedule.generation_source} — moving it "
            f"by hand would be undone on the next cycle."
        )
    if not schedule.active_status:
        return "Deleted."
    return None


def _planned_workshops():
    from scheduling.planner import planned_workshop_ids
    return planned_workshop_ids("calibration")


def _label(schedule):
    equipment = schedule.equipment
    if not equipment:
        return "Unknown equipment"
    description = getattr(equipment.description, "name", None) or equipment.model or "Equipment"
    return f"{description} · {equipment.serial_number}"


def group_snapshot(month, planning_logic="department", workshop=None):
    """Every planning group scheduled in ``month``, with its completion state.

    This is what the schedules page needs in order to show progress: a group is
    what auto-rescheduling acts on, so the operator should see the estate the
    same way the scheduler does — including how many members remain before the
    group rolls to the next period.
    """
    month = _month_floor(month)
    logic = grouping.canonical_logic(planning_logic)

    schedules = (
        CalibrationSchedule.objects
        .filter(scheduled_month=month, active_status=True, equipment__active_status=True)
        .select_related("equipment", "equipment__department", "equipment__description")
    )
    if workshop is not None:
        schedules = schedules.filter(equipment__department__workshop=workshop)

    groups = {}
    for schedule in schedules:
        key = grouping.group_id_for(schedule.equipment, logic)
        if key is None:
            key = "unassigned"
        bucket = groups.setdefault(key, {
            "key": str(key),
            "name": _group_name(schedule.equipment, logic),
            "logic": logic,
            "month": month,
            "members": [],
        })
        bucket["members"].append(schedule)

    result = []
    for bucket in groups.values():
        members = bucket["members"]
        completed = sum(1 for s in members if s.status == "completed")
        stats = grouping.completion_stats(len(members), completed)

        # Completed schedules are locked automatically on completion, so
        # counting every locked member reports "5 locked" on a finished group
        # and tells the operator nothing. What is worth surfacing is a member
        # that is NOT done and still cannot be moved — that is the one which
        # will hold the group back and cannot simply be rescheduled around.
        blocked = sum(
            1 for s in members
            if s.status != "completed" and protection_reason(s) is not None
        )

        result.append({
            **bucket,
            "total": stats["total"],
            "completed": stats["completed"],
            "remaining": stats["total"] - stats["completed"],
            "percent": round(completed / len(members) * 100) if members else 0,
            "all_completed": stats["all_completed"],
            "blocked": blocked,
            "next_period_month": grouping.next_period_month(
                month, members[0].calibration_period or 12
            ) if members else None,
            "members": [
                {
                    "id": str(s.id),
                    "equipment": _label(s),
                    "status": s.status,
                    "is_locked": getattr(s, "is_locked", False),
                    "movable": protection_reason(s) is None,
                    "reason": protection_reason(s),
                }
                for s in sorted(members, key=lambda s: _label(s))
            ],
        })

    # Groups closest to rolling over are the interesting ones.
    return sorted(result, key=lambda g: (-g["percent"], g["name"]))


def _group_name(equipment, logic):
    if logic == "department":
        return getattr(equipment.department, "name", None) or "No department"
    return getattr(equipment.description, "name", None) or "No description"
