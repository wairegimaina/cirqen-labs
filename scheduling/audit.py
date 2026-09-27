"""Health of the schedule data itself (phase 1 of the redesign).

``audit`` only reads. The two repairs change open schedules only, never
completed ones, and never delete: a surplus schedule is retired
(``active_status=False``) so it stays on record and syncs as a change.

* ``retire_duplicates``: an equipment should have one open schedule. Where it
  has more, keep the one with work under way, else the earliest, and retire
  the rest.
* ``floor_dates``: open schedules must fall on the 1st. The old PPM overdue
  job added 30 days (1 Jan became 31 Jan, 1 Feb became 3 Mar), and every
  query that matches a group by month missed those rows.
"""
from collections import Counter
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from Inventory.models import Equipment

from sync.open_schedule_rule import keeper_key

from . import engine, planner



@dataclass
class ProgramAudit:
    program: str
    equipment: int = 0
    scheduled: int = 0
    never_scheduled: int = 0
    history_only: int = 0          # completed schedules but nothing open: a broken chain
    duplicates: list = field(default_factory=list)     # (equipment_id, serial, [months])
    off_first: int = 0
    inactive_equipment_open: int = 0
    open_without_interval: int = 0
    completed_without_date: int = 0
    overdue: int = 0
    by_month: Counter = field(default_factory=Counter)
    planned_workshops: int = 0


def audit(program, workshop=None, today=None):
    prog = planner.PROGRAMS[program]
    model = prog.model
    month = (today or timezone.localdate()).replace(day=1)
    equipment = Equipment.objects.filter(active_status=True)
    schedules = model.objects.all()
    if workshop is not None:
        equipment = equipment.filter(department__workshop=workshop)
        schedules = schedules.filter(equipment__department__workshop=workshop)
    open_qs = model.open_schedules().filter(pk__in=schedules.values("pk"))

    result = ProgramAudit(program=program)
    result.equipment = equipment.count()
    open_ids = set(open_qs.filter(equipment__active_status=True).values_list("equipment_id", flat=True))
    completed_ids = set(schedules.filter(status="completed", pending_delete=False)
                        .values_list("equipment_id", flat=True))
    active_ids = set(equipment.values_list("id", flat=True))
    result.scheduled = len(active_ids & open_ids)
    result.history_only = len((active_ids - open_ids) & completed_ids)
    result.never_scheduled = len(active_ids - open_ids - completed_ids)

    dup = (open_qs.filter(equipment__active_status=True).values("equipment_id")
           .annotate(n=Count("id")).filter(n__gt=1).values_list("equipment_id", flat=True))
    months = {}
    for eq_id, serial, m in open_qs.filter(equipment_id__in=list(dup)).order_by(
            "equipment_id", "scheduled_month").values_list("equipment_id", "equipment__serial_number",
                                                           "scheduled_month"):
        months.setdefault((eq_id, serial), []).append(m)
    result.duplicates = [(eq_id, serial, ms) for (eq_id, serial), ms in months.items()]

    result.off_first = open_qs.exclude(scheduled_month__day=1).count()
    result.inactive_equipment_open = open_qs.filter(equipment__active_status=False).count()
    result.open_without_interval = open_qs.filter(**{f"{prog.period_field}__isnull": True}).count()
    result.completed_without_date = schedules.filter(status="completed", completed_date__isnull=True).count()
    live = open_qs.filter(equipment__active_status=True)
    result.overdue = live.filter(scheduled_month__lt=month).count()
    for m, n in live.filter(scheduled_month__gte=month).values_list("scheduled_month").annotate(n=Count("id")):
        result.by_month[m.replace(day=1)] += n
    result.planned_workshops = len(planner.planned_workshop_ids(program))
    return result


def _keep_first(rows):
    """Which open schedule to keep: the rule HQ and the sync agent also apply."""
    return sorted(rows, key=lambda r: keeper_key(r["status"], r["scheduled_month"], r["id"]))


@transaction.atomic
def retire_duplicates(program, apply=False, workshop=None):
    model = planner.PROGRAMS[program].model
    open_qs = model.open_schedules()
    if workshop is not None:
        open_qs = open_qs.filter(equipment__department__workshop=workshop)
    dup = (open_qs.values("equipment_id").annotate(n=Count("id")).filter(n__gt=1)
           .values_list("equipment_id", flat=True))
    rows = {}
    for r in open_qs.filter(equipment_id__in=list(dup)).values(
            "id", "equipment_id", "equipment__serial_number", "scheduled_month", "status"):
        rows.setdefault(r["equipment_id"], []).append(r)

    decisions = []   # (serial, kept month, [retired months])
    retire_ids = []
    for group in rows.values():
        keep, *extra = _keep_first(group)
        decisions.append((keep["equipment__serial_number"], keep["scheduled_month"],
                          [r["scheduled_month"] for r in extra]))
        retire_ids += [r["id"] for r in extra]
    if apply and retire_ids:
        model.objects.filter(id__in=retire_ids).update(
            active_status=False, needs_sync=True, updated_at=timezone.now())
    return decisions


@transaction.atomic
def floor_dates(program, apply=False, workshop=None):
    """Move open schedules dated after the 1st to the 1st of their month.

    Skips a row when the equipment already has a schedule on that 1st
    (unique per equipment and month); those are reported, not merged.
    """
    model = planner.PROGRAMS[program].model
    qs = model.open_schedules().exclude(scheduled_month__day=1)
    if workshop is not None:
        qs = qs.filter(equipment__department__workshop=workshop)
    moved, blocked, claimed = [], [], set()
    for sched in qs.select_related("equipment").order_by("equipment_id", "scheduled_month"):
        target = engine.month_floor(sched.scheduled_month)
        key = (sched.equipment_id, target)
        if key in claimed or model.objects.filter(equipment_id=sched.equipment_id,
                                                  scheduled_month=target).exists():
            blocked.append((sched.equipment.serial_number, sched.scheduled_month))
            continue
        claimed.add(key)
        moved.append((sched.equipment.serial_number, sched.scheduled_month, target))
        if apply:
            model.objects.filter(pk=sched.pk).update(
                scheduled_month=target, needs_sync=True, updated_at=timezone.now())
    return moved, blocked
