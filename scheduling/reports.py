"""Read-only figures for the scheduling screens.

Everything is computed per workshop and program with aggregate queries or
one pass over the workshop's equipment, so a 5,000-device workshop costs a
handful of queries, not one per device.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from django.db.models import Count
from django.utils import timezone

from Inventory.models import Equipment

from . import engine, planner
from .models import months_label

PROBLEM_LABELS = {
    "no_months": "No months for its group",
    "no_interval": "No interval",
    "interval_does_not_fit": "Interval doesn't fit the group's months",
    "month_taken": "Month already taken",
}


def this_month(today=None):
    return (today or timezone.localdate()).replace(day=1)


def workshop_equipment(workshop):
    return Equipment.objects.filter(department__workshop=workshop, active_status=True)


# ── The panel on the PPM and calibration pages ──────────────────────────────

def panels(workshops, program):
    """One summary per workshop: its plan, grouping and what needs attention."""
    model = planner.PROGRAMS[program].model
    rows = []
    for workshop in workshops:
        plan = planner.active_plan(workshop.id, program)
        equipment = workshop_equipment(workshop)
        total = equipment.count()
        if not total and not plan:
            continue
        scheduled = model.open_schedules().filter(equipment__in=equipment).values("equipment_id").distinct().count()
        month = this_month()
        rows.append({
            "workshop": workshop,
            "plan": plan,
            "draft": planner.SchedulingPlan.objects.filter(
                workshop=workshop, program=program, state=planner.SchedulingPlan.STATE_DRAFT).first(),
            "groups": plan.rules.exclude(month_mask=0).count() if plan else 0,
            "total": total,
            "unscheduled": total - scheduled,
            "overdue": model.open_schedules().filter(equipment__in=equipment, scheduled_month__lt=month).count(),
            "query": f"?workshop={workshop.id}&program={program}",
            "other_logic": ("department" if not plan or plan.logic == "description" else "description"),
        })
    return rows


# ── Plan health ──────────────────────────────────────────────────────────────

@dataclass
class Audit:
    """How well a plan covers a workshop: problems per device, off-plan schedules."""
    problems: Counter = field(default_factory=Counter)
    problem_rows: list = field(default_factory=list)   # (equipment row, code, message)
    off_plan: list = field(default_factory=list)       # (schedule, equipment row, message)


def audit(plan):
    view = planner.PlanView(plan)
    result = Audit()
    rows = planner._equipment_rows(plan.workshop_id)
    by_id = {r["id"]: r for r in rows}
    for row in rows:
        group_id, group_name = view.group(row)
        months = view.months.get(group_id)
        interval = view.interval(row["description_id"])
        code, message = None, ""
        if not months:
            code, message = "no_months", f"{group_name or '(no group)'} has no months in the plan"
        elif not interval:
            code, message = "no_interval", "No interval for this description and no plan default"
        elif not engine.slots(interval, months):
            need = 12 // engine.step(interval)
            code = "interval_does_not_fit"
            message = (f"A {interval}-month interval needs {need} evenly spaced months; "
                       f"{group_name} has {months_label(months)}")
        if code:
            result.problems[code] += 1
            result.problem_rows.append((row, code, message))

    model = view.program.model
    for sched in model.open_schedules().filter(equipment_id__in=by_id.keys()).order_by("scheduled_month"):
        row = by_id[sched.equipment_id]
        group_id, group_name = view.group(row)
        interval = view.interval(row["description_id"])
        month = sched.due_month or sched.scheduled_month
        if not view.fits(group_id, interval, month):
            result.off_plan.append((sched, row, f"{engine.month_name(month)} is not one of "
                                                f"{group_name}'s months for a {interval or '?'}-month interval"))
    return result


# ── Overview ─────────────────────────────────────────────────────────────────

def overview(workshop, program, today=None):
    model = planner.PROGRAMS[program].model
    month = this_month(today)
    next_month = engine.add_months(month, 1)
    equipment = workshop_equipment(workshop)
    open_qs = model.open_schedules().filter(equipment__in=equipment)

    total = equipment.count()
    scheduled = open_qs.values("equipment_id").distinct().count()
    figures = {
        "total": total,
        "scheduled": scheduled,
        "unscheduled": total - scheduled,
        "pending": open_qs.count(),
        "completed": model.objects.filter(
            equipment__in=equipment, status="completed", pending_delete=False).count(),
        "overdue": open_qs.filter(scheduled_month__lt=month).count(),
        "due_this_month": open_qs.filter(scheduled_month=month).count(),
        "due_next_month": open_qs.filter(scheduled_month=next_month).count(),
        "conflicts": open_qs.values("equipment_id").annotate(n=Count("id")).filter(n__gt=1).count(),
    }

    plan = planner.active_plan(workshop.id, program)
    health = audit(plan) if plan else None
    if health:
        figures.update(
            no_months=health.problems["no_months"],
            no_interval=health.problems["no_interval"],
            does_not_fit=health.problems["interval_does_not_fit"],
            off_plan=len(health.off_plan),
        )
    return {"figures": figures, "plan": plan, "health": health,
            "distribution": distribution(open_qs, month)}


def distribution(open_qs, month, months=12):
    """Open schedules per month for the next ``months`` months, plus overdue."""
    end = engine.add_months(month, months)
    counts = dict(
        open_qs.filter(scheduled_month__gte=month, scheduled_month__lt=end)
        .values_list("scheduled_month").annotate(n=Count("id"))
    )
    overdue = open_qs.filter(scheduled_month__lt=month).count()
    bars = [{"month": engine.add_months(month, i), "count": counts.get(engine.add_months(month, i), 0)}
            for i in range(months)]
    peak = max([b["count"] for b in bars] + [overdue, 1])
    for b in bars:
        b["pct"] = round(100 * b["count"] / peak)
    return {"bars": bars, "overdue": overdue, "overdue_pct": round(100 * overdue / peak), "peak": peak}


# ── Month view ───────────────────────────────────────────────────────────────

def year_months(workshop, program, year, today=None):
    model = planner.PROGRAMS[program].model
    current = this_month(today)
    rows = (model.objects.filter(
        equipment__in=workshop_equipment(workshop), scheduled_month__year=year,
        active_status=True, pending_delete=False,
    ).values_list("scheduled_month", "status").annotate(n=Count("id")))
    months = {m: {"month": date(year, m, 1),
                  "completed": 0, "open": 0, "overdue": 0} for m in range(1, 13)}
    for sched_month, status, n in rows:
        cell = months[sched_month.month]
        if status == "completed":
            cell["completed"] += n
        else:
            cell["open"] += n
            if sched_month < current:
                cell["overdue"] += n
    for cell in months.values():
        cell["total"] = cell["completed"] + cell["open"]
    return list(months.values())


def month_schedules(workshop, program, month):
    model = planner.PROGRAMS[program].model
    return (model.objects.filter(
        equipment__in=workshop_equipment(workshop), scheduled_month=month,
        active_status=True, pending_delete=False,
    ).select_related("equipment__department", "equipment__description", "plan")
     .order_by("equipment__department__name", "equipment__description__name", "equipment__serial_number"))


# ── Unscheduled ──────────────────────────────────────────────────────────────

def unscheduled(workshop, program, today=None):
    """Equipment without an open schedule, and why.

    With an active plan, a dry run says where each device will be placed on
    the next run, or why it can't be. A workshop with no plan yet gets one on
    the next scheduling run.
    """
    plan = planner.active_plan(workshop.id, program)
    if plan:
        run = planner.schedule(plan, today=today, dry_run=True)
        waiting = [(row, p.month, p.message) for row, p in run.created]
        blocked = [(row, PROBLEM_LABELS.get(p.problem, p.problem), p.message) for row, p in run.unschedulable]
        return {"plan": plan, "waiting": waiting, "blocked": blocked}

    model = planner.PROGRAMS[program].model
    open_ids = model.open_schedules().values("equipment_id")
    rows = list(workshop_equipment(workshop).exclude(id__in=open_ids).order_by("serial_number").values(
        "id", "serial_number", "department__name", "description__name"))
    return {"plan": None, "waiting": [], "blocked": [
        (row, "No plan yet", "The workshop's plan is created on the next scheduling run "
                             "(or set one up now under Plan); this device is placed then") for row in rows]}
