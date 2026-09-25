"""The scheduling engine: pure month arithmetic, no database.

Everything here is deterministic: the same equipment, plan and history give
the same answer every time.

Cycle slots
-----------
A device on an ``interval``-month cycle visits the months that are equal
modulo ``g = gcd(interval, 12)``. On 6 months that is {Feb, Aug} or {Jan, Jul}
and so on; on 3 months {Jan, Apr, Jul, Oct}; on 12 or 24 months a single
month. Such a set is a *slot*. A group's allowed months admit a slot only if
they contain the whole slot, so a device can never be due outside its group's
months:

    ICU: Jan, Jul    6-month device  -> slot (Jan, Jul)
                     12-month device -> slot (Jan,) or (Jul,), balanced
                     3-month device  -> no slot: unschedulable, with a reason

The next due month
------------------
A completed schedule's *due* month plus the interval gives the next one, so
an early or late visit does not drag the cycle (the rule calibration already
followed). Two refinements, both only for late or out-of-slot completions:

* The next due month is always after the month the work was done in.
* ``within_interval`` (calibration): the next due month is never more than
  one interval after the work was done, so a certificate does not lapse
  before the device is due; it takes the latest slot month that fits.
  ``next_slot`` (PPM): the earliest slot month on or after due + interval.
"""
from dataclasses import dataclass, field
from datetime import date
from math import gcd

from dateutil.relativedelta import relativedelta

from .models import MONTH_NAMES, months_label

NEXT_SLOT = "next_slot"
WITHIN_INTERVAL = "within_interval"

SEARCH_LIMIT = 60  # months; a slot month always occurs within 12


def month_floor(value):
    return value.replace(day=1)


def add_months(month, n):
    return month + relativedelta(months=n)


def month_name(month):
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def step(interval):
    """Spacing between a slot's months."""
    return gcd(int(interval), 12)


def slot_of(month_number, interval):
    """The slot (tuple of months 1-12) that ``month_number`` belongs to."""
    g = step(interval)
    first = (month_number - 1) % g + 1
    return tuple(range(first, 13, g))


def slots(interval, allowed_months):
    """Slots wholly inside ``allowed_months``, earliest first."""
    allowed = set(allowed_months)
    g = step(interval)
    found = []
    for first in range(1, g + 1):
        slot = tuple(range(first, 13, g))
        if set(slot) <= allowed:
            found.append(slot)
    return found


def first_in_slot(slot, start):
    """Earliest month on or after ``start`` whose month is in ``slot``."""
    month = month_floor(start)
    for _ in range(SEARCH_LIMIT):
        if month.month in slot:
            return month
        month = add_months(month, 1)
    raise ValueError(f"No month of {slot} after {start}")


def next_due(prev_due, interval, slot, completed_month=None, policy=NEXT_SLOT):
    """The month due after ``prev_due`` was done in ``completed_month``."""
    prev_due = month_floor(prev_due)
    done = month_floor(completed_month) if completed_month else prev_due
    base = add_months(prev_due, interval)
    after_done = add_months(done, 1)

    if policy == WITHIN_INTERVAL:
        latest = add_months(done, interval)
        month = latest
        while month > prev_due and month >= after_done:
            if month.month in slot:
                return month
            month = add_months(month, -1)

    return first_in_slot(slot, max(base, after_done))


def months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def realign(prev_due, interval, slot, completed_month=None, policy=NEXT_SLOT, floor=None):
    """Next due month in a *different* slot than the device was on.

    Happens when the plan's months change under a device. Taking the next
    slot month after due + interval could add most of a year (a 12-month
    device moved from August to a May/November group would wait 21 months),
    so this takes the slot month nearest to when it was due instead:
    earlier is allowed, but never before ``floor`` (this month, so the move
    itself doesn't make the device overdue) or before the work was done.
    Calibration keeps its rule of never running past one interval.
    """
    if policy == WITHIN_INTERVAL:
        return next_due(prev_due, interval, slot, completed_month, policy)
    prev_due = month_floor(prev_due)
    done = month_floor(completed_month) if completed_month else prev_due
    base = add_months(prev_due, interval)
    lowest = max(add_months(done, 1), month_floor(floor) if floor else add_months(done, 1))
    after = first_in_slot(slot, max(base, lowest))
    before = base
    while before >= lowest and before.month not in slot:
        before = add_months(before, -1)
    if before >= lowest and months_between(before, base) <= months_between(base, after):
        return before
    return after


@dataclass
class Placement:
    """Where one device goes, and why."""
    month: date = None
    slot: tuple = ()
    problem: str = ""        # machine code when the device cannot be scheduled
    message: str = ""        # what a person reads
    facts: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.month is not None


def place(*, group_name, months, interval, last=None, start, load=None, policy=NEXT_SLOT):
    """Decide the next due month for one device.

    ``months``: its group's allowed months (empty/None when the group has no
    rule). ``last``: ``(due_month, completed_month)`` of its latest completed
    schedule, or None if it has never been done. ``start``: the earliest
    month a never-done device may be placed in. ``load``: open schedules per
    slot in this group, used to balance new devices; it is updated in place.
    """
    facts = {"group": group_name, "months": list(months or []), "interval": interval}
    if not months:
        return Placement(problem="no_months", facts=facts,
                         message=f"{group_name} has no months in the plan")
    if not interval:
        return Placement(problem="no_interval", facts=facts,
                         message="No interval for this description and no plan default")

    candidates = slots(interval, months)
    if not candidates:
        need = 12 // step(interval)
        return Placement(
            problem="interval_does_not_fit", facts=facts,
            message=(f"A {interval}-month interval needs {need} evenly spaced month"
                     f"{'s' if need != 1 else ''}; {group_name} has {months_label(months)}"),
        )

    load = load if load is not None else {}

    if last:
        prev_due, done = last
        facts.update(previous_due=month_name(prev_due),
                     completed=month_name(done) if done else None)
        own = slot_of(prev_due.month, interval)
        if own in candidates:
            slot = own
            month = next_due(prev_due, interval, slot, done, policy)
            reason = f"{interval} months after {month_name(prev_due)}"
        else:
            # Nearest month of any allowed cycle to when it was due; ties go
            # to the less-loaded cycle, then the earlier one.
            base = add_months(month_floor(prev_due), interval)
            options = [(realign(prev_due, interval, s, done, policy, floor=add_months(start, -1)), s)
                       for s in candidates]
            month, slot = min(options, key=lambda o: (abs(months_between(base, o[0])),
                                                     load.get(o[1], 0), o[1]))
            reason = (f"{month_name(prev_due)} is no longer one of {group_name}'s months; "
                      f"due {month_name(base)}, nearest {months_label(slot)} month")
        if done and done > prev_due and month != add_months(prev_due, interval):
            reason += f" (done late, in {month_name(done)})"
    else:
        slot = min(candidates, key=lambda s: (load.get(s, 0), s))
        month = first_in_slot(slot, start)
        reason = f"first visit: next {months_label(slot)} month from {month_name(start)}"
        if len(candidates) > 1:
            reason += ", on the least-loaded of its cycles"

    load[slot] = load.get(slot, 0) + 1
    facts.update(slot=list(slot), due=month_name(month))
    message = f"{reason} → {month_name(month)} ({group_name}: {months_label(months)}, every {interval} months)"
    return Placement(month=month, slot=slot, message=message, facts=facts)
