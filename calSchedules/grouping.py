"""
grouping.py — calibration date and certificate-number helpers.

The group scheduling logic that used to live here (group membership, group
months, first-fit packing, protection rules) was replaced by the scheduling
plans in ``scheduling`` (engine.py and planner.py). What remains are the
pure helpers the rest of the app shares: when a calibration is due, whether
it is overdue, and the certificate-number format.
"""
from calendar import monthrange
from datetime import date, datetime

from dateutil.relativedelta import relativedelta


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
