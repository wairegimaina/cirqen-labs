"""
grouping.py — single source of truth for calibration scheduling "group" logic.

Historically the same concepts were re-implemented across ``tasks.py``,
``instant_reconciliation.py``, ``locker.py`` and ``reconciliation.py``:

    * what group an equipment belongs to (department- or description-based),
    * who the group members are for a given month,
    * whether a group is complete,
    * what month comes next for a completed schedule,
    * whether a schedule is protected from reorganization,
    * the next sequential certificate number.

Those copies had **diverged** (e.g. some filtered on the *schedule's*
``active_status`` while others filtered on the *equipment's*), so they are NOT
freely interchangeable. This module centralises the logic while *preserving*
every caller's behaviour: the pure helpers are shared verbatim, and the one ORM
query builder (:func:`group_members_qs`) takes **explicit flags** so each caller
reproduces its exact historical filters. See ``SCHEDULING_NOTES.md`` for the
documented divergences that are intentionally left for a later decision.

Design rule: everything above :func:`group_members_qs` is pure (no ORM, no
side effects) and unit-tested with ``SimpleTestCase``.
"""
from calendar import monthrange
from datetime import date, datetime

from dateutil.relativedelta import relativedelta


# ── Planning-logic vocabulary ─────────────────────────────────────────────────
#
# Two vocabularies for the same two concepts coexist in the codebase:
#   * runtime cycle (instant_reconciliation, locker): 'department' / 'description'
#   * reorganizer + UI + model defaults:              'date_based' / 'description_based'
# They historically did NOT reconcile, so a schedule reorganized "by department"
# (stored as 'date_based') was regrouped by *description* during the PPM
# auto-reschedule cycle. canonical_logic() collapses both vocabularies to the
# runtime pair so every group decision agrees, without a data migration.

LOGIC_ALIASES = {
    "date_based": "department",
    "description_based": "description",
}


def canonical_logic(planning_logic):
    """Normalise a planning_logic value to the runtime pair.

    ``date_based`` -> ``department``, ``description_based`` -> ``description``;
    ``department`` / ``description`` (and anything else) pass through unchanged.
    """
    return LOGIC_ALIASES.get(planning_logic, planning_logic)


# ── Group identity ────────────────────────────────────────────────────────────

def group_field_lookup(planning_logic):
    """ORM lookup prefix for the group id field given the planning logic.

    Convention shared by every scheduler module: department-based logic groups by
    the equipment's department; anything else groups by its description. Both
    logic vocabularies are accepted (see :func:`canonical_logic`).
    """
    return "equipment__department_id" if canonical_logic(planning_logic) == "department" else "equipment__description_id"


def group_id_for(equipment, planning_logic):
    """Return the group id (department_id or description_id) for ``equipment``.

    Mirrors the ``_get_group_members`` / ``find_group_members`` convention:
    returns ``None`` when equipment is falsy or the id attribute is absent.
    """
    if not equipment:
        return None
    if canonical_logic(planning_logic) == "department":
        return getattr(equipment, "department_id", None)
    return getattr(equipment, "description_id", None)


def group_key(schedule, planning_logic):
    """Stable, human-readable group key ``"<logic>_<gid>_<YYYY-MM>"``.

    Note the sentinels (``'no_department'`` / ``'no_description'`` /
    ``'unknown'``) and that it inspects the *related object*
    (``equipment.department``). The logic is canonicalised first so the two
    vocabularies produce the *same* key for the same real group.
    """
    planning_logic = canonical_logic(planning_logic)
    if planning_logic == "department":
        group_id = (
            schedule.equipment.department_id
            if schedule.equipment and schedule.equipment.department
            else "no_department"
        )
    else:
        group_id = (
            schedule.equipment.description_id
            if schedule.equipment and schedule.equipment.description
            else "no_description"
        )
    month_key = schedule.scheduled_month.strftime("%Y-%m") if schedule.scheduled_month else "unknown"
    return f"{planning_logic}_{group_id}_{month_key}"


# ── Date / period math ────────────────────────────────────────────────────────

def planning_year(today=None):
    """Current planning year (rolls to next year once past June)."""
    today = today or date.today()
    return today.year + 1 if today.month > 6 else today.year


def next_period_month(current_month, period):
    """The month one calibration ``period`` (in months) after ``current_month``."""
    return current_month + relativedelta(months=period)


def clamp_far_future_month(next_month, today, period, slack_months=6):
    """Guard against a stale base month producing an absurd future date.

    Reproduces the rule in ``instant.get_next_group_month``: if ``next_month`` is
    more than ``period + slack_months`` ahead of today, rebase on today instead.
    """
    max_future = today + relativedelta(months=period + slack_months)
    if next_month > max_future:
        return today.replace(day=1) + relativedelta(months=period)
    return next_month


def find_optimal_month(start_date, month_count, group_id, month_map, max_per_month,
                       is_special, max_attempts=36):
    """Find the first available month for a group (load-balancing across months).

    Moved verbatim from ``tasks._find_optimal_month``. ``month_map`` is mutated
    in place (memoises the chosen month per group id).
    """
    if group_id in month_map:
        return month_map[group_id]

    for offset in range(max_attempts):
        candidate = start_date + relativedelta(months=offset)
        count = sum(1 for m in month_map.values() if m == candidate)
        if is_special or count < max_per_month:
            month_map[group_id] = candidate
            return candidate

    fallback = start_date + relativedelta(months=max_attempts)
    month_map[group_id] = fallback
    return fallback


# ── Protection & completion ───────────────────────────────────────────────────

def is_protected(status, is_locked, generation_source, protected_sources):
    """Whether a schedule must never be moved by normalization/reorganization.

    Pure form of ``tasks._is_protected`` — caller passes the schedule's fields
    and its module's ``PROTECTED_SOURCES`` list.
    """
    return (
        status == "completed"
        or is_locked
        or generation_source in protected_sources
    )


def completion_stats(total, completed):
    """Group completion summary. ``all_completed`` is False for an empty group."""
    return {
        "total": total,
        "completed": completed,
        "all_completed": total > 0 and total == completed,
    }


# ── Certificate numbering ─────────────────────────────────────────────────────

def next_certificate_number(last_cert, prefix="BNH-"):
    """Next sequential certificate number given the current highest one.

    The numbering **format**, not an allocator. Certificate numbers are
    allocated by the HQ server only; the local allocation path that used to
    call this (``CalibrationSession.generate_certificate_number``) has been
    removed, because two field sites allocating from their own local maxima
    produced duplicate numbers that collided on sync.

    This remains the one pure, tested definition of the format, which HQ's
    allocator mirrors. ``last_cert`` may be ``None``/empty for the first
    certificate. Unparseable suffixes reset the sequence to 0 → ``0001``.
    """
    if last_cert:
        try:
            last_seq = int(last_cert.split("-")[-1])
        except ValueError:
            last_seq = 0
    else:
        last_seq = 0
    return f"{prefix}{last_seq + 1:04d}"


# ── Due dates ────────────────────────────────────────────────────────────────
#
# A calibration is due within a MONTH, not on a particular day of it.
#
# Anchoring the due date to the same day of the month as the last calibration
# made the schedule brittle: a device calibrated on the 3rd was "overdue" from
# the 4th, even though the workshop had the rest of the month to reach it, and
# moving a visit a few days later pushed every subsequent due date with it.
#
# Taking the last day of the month instead gives the workshop the whole month
# to schedule the visit without the device counting as overdue, and keeps the
# anniversary stable: 31 March + 12 months is 31 March, not 30 March, because
# the month rather than the day is what carries forward.
#
# Four ad-hoc versions of this calculation existed across the codebase — two in
# the CalSoft dashboard, one in the certificate sections, one in the main
# dashboard — each written slightly differently. This is the shared one.

def month_end(value):
    """The last day of the month ``value`` falls in.

    Accepts a ``date`` or ``datetime``; returns a ``date``. Handles month
    lengths and leap years via the calendar, so February 2028 gives the 29th.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    last = monthrange(value.year, value.month)[1]
    return value.replace(day=last)


def next_due_date(last_calibrated, interval_months=12):
    """When a calibration performed on ``last_calibrated`` next falls due.

    The result is the **last day** of the month ``interval_months`` after the
    calibration, so the whole of that month is available to schedule the visit.

    ``relativedelta`` is used rather than a day count: 12 months is not 360
    days, and adding ``30 * months`` — as one caller did — drifts the due date
    about five days earlier every year, so a yearly calibration slides into the
    previous month after six cycles.
    """
    if last_calibrated is None:
        return None
    if isinstance(last_calibrated, datetime):
        last_calibrated = last_calibrated.date()
    months = int(interval_months or 12)
    return month_end(last_calibrated + relativedelta(months=months))


def is_overdue(due_date, today=None):
    """True when ``due_date`` has passed.

    A device is overdue only after the last day of its due month, which is the
    point of anchoring due dates there.
    """
    if due_date is None:
        return False
    today = today or date.today()
    if isinstance(due_date, datetime):
        due_date = due_date.date()
    return due_date < today


def days_until_due(due_date, today=None):
    """Days remaining until ``due_date``; negative once overdue."""
    if due_date is None:
        return None
    today = today or date.today()
    if isinstance(due_date, datetime):
        due_date = due_date.date()
    return (due_date - today).days


# ── ORM query builder (the ONLY DB-touching function here) ────────────────────

def group_members_qs(equipment, scheduled_month, planning_logic, *,
                     require_equipment_active=True, require_schedule_active=True,
                     statuses=None, select_related=("equipment",)):
    """Return the ``CalibrationSchedule`` queryset for a group in one month.

    **One definition of group membership: both the equipment and the schedule
    must be active.**

    The two callers used to disagree, and the disagreement was the reason
    auto-rescheduling stopped firing:

      * ``instant.find_group_members`` required the equipment active and
        ignored the schedule flag, so a **soft-deleted schedule** still counted
        as a member and the group could never reach "all completed";
      * ``tasks._get_group_members`` required the schedule active and ignored
        the equipment flag, so a **retired device** held the group open forever.

    Either way the group never completed, so the next period was never created
    and the schedule silently stopped advancing. ``SCHEDULING_NOTES.md``
    recorded this as the most likely source of "scheduling doesn't make sense"
    symptoms and left the canonical choice for a decision; the decision is the
    strictest reading, which is what the locker already used.

    The flags remain for the rare caller that genuinely needs a wider view
    (reporting on retired equipment, for instance), but they now default to the
    canonical rule so a new caller cannot reintroduce the divergence by
    omission.

    ``statuses`` optionally restricts ``status__in``.
    """
    from .models import CalibrationSchedule  # local import: avoids import cycles

    filters = {
        group_field_lookup(planning_logic): group_id_for(equipment, planning_logic),
        "scheduled_month": scheduled_month,
    }
    if require_equipment_active:
        filters["equipment__active_status"] = True
    if require_schedule_active:
        filters["active_status"] = True
    if statuses is not None:
        filters["status__in"] = statuses

    qs = CalibrationSchedule.objects.filter(**filters)
    if select_related:
        qs = qs.select_related(*select_related)
    return qs
