"""Turn a scheduling plan into schedules (the database side of the engine).

Entry points
------------
* :func:`schedule`: give every active device in the plan's workshop that has
  no open schedule its next one. Idempotent: a second run finds nothing to do.
* :func:`on_completed`: the same for one device, right after a completion.
* :func:`preview_activation` / :func:`activate`: switch a workshop to a new
  plan version and move the open schedules that no longer fit it.
* :func:`reassign`: after a transfer, move a device's open schedule if its
  new group works in different months.
* :func:`explain`: why a device is due when it is.

Workshops without an active plan are untouched: the legacy schedulers keep
running for them (see :func:`planned_workshop_ids`).
"""
import logging
import uuid
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from Inventory.models import Equipment

from . import engine
from .models import SchedulingPlan, mask_to_months

logger = logging.getLogger(__name__)

# Deterministic ids: two desktops scheduling the same device for the same
# month produce the same row, so sync merges them instead of duplicating.
ID_NAMESPACE = uuid.UUID("5d0f4c9e-6a0b-4c55-9d64-1f3b1b8e2c71")


@dataclass(frozen=True)
class Program:
    key: str
    period_field: str
    policy: str
    movable_statuses: tuple

    @property
    def model(self):
        if self.key == SchedulingPlan.PROGRAM_PPM:
            from ppms.models import PPMSchedule
            return PPMSchedule
        from calSchedules.models import CalibrationSchedule
        return CalibrationSchedule


PROGRAMS = {
    SchedulingPlan.PROGRAM_PPM: Program(
        SchedulingPlan.PROGRAM_PPM, "maintenance_period", engine.NEXT_SLOT, ("pending", "pushed"),
    ),
    SchedulingPlan.PROGRAM_CALIBRATION: Program(
        SchedulingPlan.PROGRAM_CALIBRATION, "calibration_period", engine.WITHIN_INTERVAL,
        ("pending", "pushed", "overdue"),
    ),
}


def schedule_id(program, equipment_id, month):
    return uuid.uuid5(ID_NAMESPACE, f"{program}:{equipment_id}:{month:%Y-%m}")


def active_plan(workshop_id, program):
    if not workshop_id:
        return None
    return SchedulingPlan.objects.filter(
        workshop_id=workshop_id, program=program, state=SchedulingPlan.STATE_ACTIVE,
        active_status=True,
    ).first()


def planned_workshop_ids(program):
    """Workshops whose schedules this engine owns. Legacy code skips them."""
    return set(
        SchedulingPlan.objects.filter(
            program=program, state=SchedulingPlan.STATE_ACTIVE, active_status=True,
        ).values_list("workshop_id", flat=True)
    )


def next_month(today=None):
    today = today or timezone.localdate()
    return engine.add_months(today.replace(day=1), 1)


# ── Plan lookups ─────────────────────────────────────────────────────────────

class PlanView:
    """A plan's rules and intervals, loaded once."""

    def __init__(self, plan):
        self.plan = plan
        self.program = PROGRAMS[plan.program]
        self.months = {}
        for rule in plan.rules.filter(active_status=True):
            self.months[rule.department_id or rule.description_id] = mask_to_months(rule.month_mask)
        self.intervals = dict(
            plan.intervals.filter(active_status=True).values_list("description_id", "interval_months")
        )

    def group(self, row):
        """(group id, group name) of an equipment row from :func:`_equipment_rows`."""
        if self.plan.is_department:
            return row["department_id"], row["department__name"]
        return row["description_id"], row["description__name"]

    def interval(self, description_id):
        return self.intervals.get(description_id) or self.plan.default_interval_months

    def fits(self, group_id, interval, month):
        """Is ``month`` a valid due month for a device in this group?"""
        months = self.months.get(group_id)
        if not months or not interval:
            return False
        return engine.slot_of(month.month, interval) in engine.slots(interval, months)


def _equipment_rows(workshop_id, equipment_ids=None):
    qs = Equipment.objects.filter(department__workshop_id=workshop_id, active_status=True)
    if equipment_ids is not None:
        qs = qs.filter(id__in=equipment_ids)
    # Stable order (serial numbers are unique) so balancing is reproducible.
    return list(qs.order_by("serial_number", "id").values(
        "id", "department_id", "description_id", "department__name", "description__name",
        "serial_number",
    ))


def _load(view, rows_by_id, open_rows):
    """Open schedules per (group, interval, slot): what balancing counts."""
    load = {}
    for sched in open_rows:
        row = rows_by_id.get(sched["equipment_id"])
        if not row:
            continue
        group_id, _ = view.group(row)
        interval = view.interval(row["description_id"])
        if not interval:
            continue
        month = sched["due_month"] or sched["scheduled_month"]
        slots = load.setdefault((group_id, interval), {})
        slot = engine.slot_of(month.month, interval)
        slots[slot] = slots.get(slot, 0) + 1
    return load


def _last_completed(model, equipment_ids):
    latest = {}
    rows = model.objects.filter(
        equipment_id__in=equipment_ids, status="completed", pending_delete=False,
    ).order_by("equipment_id", "-scheduled_month").values(
        "id", "equipment_id", "scheduled_month", "due_month", "completed_date",
    )
    for r in rows:
        latest.setdefault(r["equipment_id"], r)
    return latest


def _place(view, row, last, start, load):
    group_id, group_name = view.group(row)
    interval = view.interval(row["description_id"])
    history = None
    if last:
        done = last["completed_date"]
        history = (last["due_month"] or last["scheduled_month"], done.replace(day=1) if done else None)
    return engine.place(
        group_name=group_name or "(no group)",
        months=view.months.get(group_id),
        interval=interval,
        last=history,
        start=start,
        load=load.setdefault((group_id, interval), {}),
        policy=view.program.policy,
    )


def _reason(view, placement):
    plan = view.plan
    return {
        **placement.facts,
        "plan_id": str(plan.id),
        "plan_version": plan.version,
        "logic": plan.logic,
        "text": placement.message,
        "at": timezone.now().isoformat(timespec="seconds"),
    }


# ── Scheduling ───────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    plan: SchedulingPlan
    created: list = field(default_factory=list)       # (equipment row, placement)
    unschedulable: list = field(default_factory=list)  # (equipment row, placement)
    already_scheduled: int = 0
    dry_run: bool = False

    def summary(self):
        verb = "would create" if self.dry_run else "created"
        return (f"{self.plan}: {verb} {len(self.created)}, "
                f"{self.already_scheduled} already scheduled, "
                f"{len(self.unschedulable)} cannot be scheduled")


def schedule(plan, equipment_ids=None, today=None, dry_run=False):
    """Give every unscheduled device in the plan's workshop its next schedule."""
    view = PlanView(plan)
    model = view.program.model
    start = next_month(today)

    rows = _equipment_rows(plan.workshop_id, equipment_ids)
    rows_by_id = {r["id"]: r for r in rows}
    result = RunResult(plan=plan, dry_run=dry_run)

    with transaction.atomic():
        # Serialise runs for one workshop and program (Postgres row lock).
        SchedulingPlan.objects.select_for_update().filter(pk=plan.pk).first()

        open_rows = list(model.open_schedules().filter(
            equipment__department__workshop_id=plan.workshop_id,
        ).values("equipment_id", "scheduled_month", "due_month"))
        open_ids = {r["equipment_id"] for r in open_rows}
        todo = [r for r in rows if r["id"] not in open_ids]
        result.already_scheduled = len(rows) - len(todo)
        if not todo:
            return result

        all_rows = rows_by_id if equipment_ids is None else {
            r["id"]: r for r in _equipment_rows(plan.workshop_id)
        }
        load = _load(view, all_rows, open_rows)
        last = _last_completed(model, [r["id"] for r in todo])

        placements = []
        for row in todo:
            placement = _place(view, row, last.get(row["id"]), start, load)
            (placements if placement.ok else result.unschedulable).append((row, placement))

        if dry_run:
            result.created = placements
            return result

        _write(view, placements, last, result)

    for row, placement in result.unschedulable:
        logger.info(f"[PLAN] {row['serial_number']}: not scheduled: {placement.message}")
    logger.info(f"[PLAN] {result.summary()}")
    return result


def _write(view, placements, last, result):
    model = view.program.model
    plan = view.plan
    program = view.program

    # A retired, never-completed row may already hold (equipment, month),
    # which is unique: bring that row back instead of inserting.
    wanted = {(row["id"], p.month) for row, p in placements}
    existing = {
        (r["equipment_id"], r["scheduled_month"]): r
        for r in model.objects.filter(
            equipment_id__in={e for e, _ in wanted},
            scheduled_month__in={m for _, m in wanted},
        ).values("id", "equipment_id", "scheduled_month", "status")
    }

    now = timezone.now()
    new_objects = []
    for row, placement in placements:
        parent = last.get(row["id"])
        fields = {
            "scheduled_month": placement.month,
            "due_month": placement.month,
            "status": "pending",
            program.period_field: placement.facts["interval"],
            "planning_logic": plan.logic,
            "generation_source": "plan",
            "plan": plan,
            "schedule_reason": _reason(view, placement),
            "parent_schedule_id": parent["id"] if parent else None,
            "active_status": True,
            "pending_delete": False,
            "needs_sync": True,
        }
        clash = existing.get((row["id"], placement.month))
        if clash:
            if clash["status"] == "completed":
                placement.problem = "month_taken"
                placement.message = (f"{engine.month_name(placement.month)} already holds a "
                                     "completed schedule for this device")
                result.unschedulable.append((row, placement))
                continue
            model.objects.filter(pk=clash["id"]).update(updated_at=now, **fields)
        else:
            new_objects.append(model(
                id=schedule_id(plan.program, row["id"], placement.month),
                equipment_id=row["id"],
                workshop_id=plan.workshop_id,
                **fields,
            ))
        result.created.append((row, placement))

    model.objects.bulk_create(new_objects)


def on_completed(schedule_row):
    """A schedule was just completed: give its device the next one, if planned.

    Returns True when a plan handled it (the caller must then skip any legacy
    rescheduling), False when the workshop has no plan.
    """
    program = (SchedulingPlan.PROGRAM_PPM if schedule_row._meta.app_label == "ppms"
               else SchedulingPlan.PROGRAM_CALIBRATION)
    plan = active_plan(schedule_row.workshop_id, program)
    if not plan:
        return False
    schedule(plan, equipment_ids=[schedule_row.equipment_id])
    return True


def schedule_by_hand(equipment_ids, program):
    """The manual "schedule" buttons: place devices through their plan.

    Returns ``(done, problems, unplanned_ids)``: ``done`` is a list of
    (equipment row, Placement) created, ``problems`` a list of (equipment row,
    message) the plan could not place, and ``unplanned_ids`` the devices in
    workshops without a plan, which the caller schedules the legacy way.
    """
    rows = Equipment.objects.filter(id__in=equipment_ids).values_list("id", "department__workshop_id")
    by_workshop = {}
    for equipment_id, workshop_id in rows:
        by_workshop.setdefault(workshop_id, []).append(equipment_id)
    done, problems, unplanned = [], [], []
    for workshop_id, ids in by_workshop.items():
        plan = active_plan(workshop_id, program)
        if not plan:
            unplanned += ids
            continue
        run = schedule(plan, equipment_ids=ids)
        done += run.created
        problems += [(row, p.message) for row, p in run.unschedulable]
    return done, problems, unplanned


def run_all(program=None, today=None, dry_run=False):
    """Schedule every workshop that has an active plan (the nightly sweep).

    Also catches completions that arrived through sync, which writes with raw
    SQL and so never fires the completion signals.
    """
    plans = SchedulingPlan.objects.filter(state=SchedulingPlan.STATE_ACTIVE, active_status=True)
    if program:
        plans = plans.filter(program=program)
    return [schedule(plan, today=today, dry_run=dry_run) for plan in plans.select_related("workshop")]


# ── Changing the plan ────────────────────────────────────────────────────────

@dataclass
class ActivationReport:
    plan: SchedulingPlan
    previous: SchedulingPlan = None
    to_move: list = field(default_factory=list)   # (schedule, equipment row, placement)
    stuck: list = field(default_factory=list)     # (schedule, equipment row, reason)
    unchanged: int = 0
    applied: bool = False

    def summary(self):
        verb = "moved" if self.applied else "would move"
        return (f"{self.plan}: {verb} {len(self.to_move)} open schedules, "
                f"{self.unchanged} already fit, {len(self.stuck)} need review")


def _misfits(view, today=None):
    """Open schedules in the workshop that the plan would not have produced."""
    model = view.program.model
    report = ActivationReport(plan=view.plan)
    rows = {r["id"]: r for r in _equipment_rows(view.plan.workshop_id)}
    opens = list(model.open_schedules().filter(
        equipment_id__in=rows.keys(),
    ).order_by("equipment_id", "scheduled_month"))

    misfit, fitting = [], []
    for sched in opens:
        row = rows[sched.equipment_id]
        group_id, _ = view.group(row)
        interval = view.interval(row["description_id"])
        if view.fits(group_id, interval, sched.due_month or sched.scheduled_month):
            report.unchanged += 1
            fitting.append({"equipment_id": sched.equipment_id, "scheduled_month": sched.scheduled_month,
                            "due_month": sched.due_month})
        else:
            misfit.append((sched, row))

    # Balance moved devices against the schedules that stay put.
    load = _load(view, rows, fitting)
    last = _last_completed(model, [s.equipment_id for s, _ in misfit])
    start = next_month(today)

    for sched, row in misfit:
        if sched.status not in view.program.movable_statuses:
            report.stuck.append((sched, row, f"work is under way (status: {sched.status})"))
            continue
        placement = _place(view, row, last.get(row["id"]), start, load)
        if not placement.ok:
            report.stuck.append((sched, row, placement.message))
        else:
            report.to_move.append((sched, row, placement))
    return report


def preview_activation(draft, today=None):
    """What activating ``draft`` would move. Changes nothing."""
    report = _misfits(PlanView(draft), today)
    report.previous = active_plan(draft.workshop_id, draft.program)
    return report


@transaction.atomic
def activate(draft, user=None, today=None):
    """Make ``draft`` the workshop's plan and move open schedules that don't fit.

    Completed schedules are never touched. Schedules with work under way, or
    for devices the new plan cannot place, stay where they are and are
    reported for review.
    """
    if draft.state != SchedulingPlan.STATE_DRAFT:
        raise ValueError(f"Only a draft can be activated ({draft} is {draft.state})")

    previous = active_plan(draft.workshop_id, draft.program)
    if previous:
        previous.state = SchedulingPlan.STATE_SUPERSEDED
        previous.save(update_fields=["state"])
    draft.state = SchedulingPlan.STATE_ACTIVE
    draft.activated_at = timezone.now()
    draft.activated_by = user
    draft.save(update_fields=["state", "activated_at", "activated_by"])

    view = PlanView(draft)
    report = _misfits(view, today)
    report.previous = previous
    moved = []
    for sched, row, placement in report.to_move:
        if _move(view, sched, placement):
            moved.append((sched, row, placement))
        else:
            report.stuck.append((sched, row, f"{engine.month_name(placement.month)} is already "
                                             "taken by another schedule for this device"))
    report.to_move = moved
    report.applied = True
    logger.info(f"[PLAN] Activated: {report.summary()}")
    return report


def _move(view, sched, placement):
    model = view.program.model
    taken = model.objects.filter(
        equipment_id=sched.equipment_id, scheduled_month=placement.month,
    ).exclude(pk=sched.pk).exists()
    if taken:
        return False
    reason = _reason(view, placement)
    reason["moved_from"] = engine.month_name(sched.scheduled_month)
    model.objects.filter(pk=sched.pk).update(
        scheduled_month=placement.month,
        due_month=placement.month,
        plan=view.plan,
        schedule_reason=reason,
        generation_source="plan",
        needs_sync=True,
        updated_at=timezone.now(),
    )
    return True


def reassign(equipment, today=None):
    """After a transfer: move open schedules whose new group has other months."""
    moved = 0
    workshop_id = equipment.department.workshop_id if equipment.department_id else None
    for key in PROGRAMS:
        plan = active_plan(workshop_id, key)
        if not plan:
            continue
        view = PlanView(plan)
        rows = _equipment_rows(workshop_id, [equipment.id])
        if not rows:
            continue
        row = rows[0]
        group_id, _ = view.group(row)
        interval = view.interval(row["description_id"])
        model = view.program.model
        for sched in model.open_schedules().filter(equipment_id=equipment.id):
            if view.fits(group_id, interval, sched.due_month or sched.scheduled_month):
                continue
            if sched.status not in view.program.movable_statuses:
                continue
            last = _last_completed(model, [equipment.id]).get(equipment.id)
            placement = _place(view, row, last, next_month(today), {})
            if placement.ok and _move(view, sched, placement):
                moved += 1
    return moved


# ── Explaining ───────────────────────────────────────────────────────────────

def explain(equipment, program):
    """Why this device is due when it is: history, the open schedule, the plan."""
    model = PROGRAMS[program].model
    history = list(model.objects.filter(
        equipment=equipment, status="completed", pending_delete=False,
    ).order_by("scheduled_month").values("scheduled_month", "completed_date"))
    current = model.open_schedules().filter(equipment=equipment).order_by("scheduled_month").first()
    plan = active_plan(equipment.department.workshop_id, program)
    out = {"completed": history, "open": current, "plan": plan, "reason": None, "would_be": None}
    if current is not None:
        out["reason"] = current.schedule_reason or None
    if plan:
        view = PlanView(plan)
        rows = _equipment_rows(plan.workshop_id, [equipment.id])
        if rows:
            last = _last_completed(model, [equipment.id]).get(equipment.id)
            out["would_be"] = _place(view, rows[0], last, next_month(), {})
    return out


# ── Drafts ───────────────────────────────────────────────────────────────────

def typical_intervals(workshop, program):
    """{description_id: months}: the interval most of that type's devices use.

    Each device counts once, with the interval of its latest schedule; a tie
    goes to the shorter interval, the safer of the two.
    """
    from collections import Counter

    prog = PROGRAMS[program]
    latest = {}
    for eq_id, desc_id, period in prog.model.objects.filter(
        equipment__department__workshop=workshop, equipment__active_status=True,
    ).order_by("equipment_id", "-scheduled_month").values_list(
        "equipment_id", "equipment__description_id", prog.period_field,
    ):
        if period and eq_id not in latest:
            latest[eq_id] = (desc_id, period)
    votes = {}
    for desc_id, period in latest.values():
        votes.setdefault(desc_id, Counter())[period] += 1
    return {d: min(c, key=lambda p: (-c[p], p)) for d, c in votes.items()}


def spread_evenly(workshop, program, logic, user=None, default_interval=None):
    """A draft whose months share the year's work out evenly (engine.spread_layout).

    For a workshop whose schedules have piled into a month or two, today's
    layout is the problem, not a starting point.
    """
    from math import gcd

    default = default_interval or (6 if program == SchedulingPlan.PROGRAM_PPM else 12)
    plan = SchedulingPlan.objects.create(
        workshop=workshop, program=program, logic=logic, version=next_version(workshop, program),
        default_interval_months=default, created_by=user,
        notes="Months spread evenly across the year",
    )
    intervals = typical_intervals(workshop, program)
    plan.intervals.model.objects.bulk_create([
        plan.intervals.model(plan=plan, description_id=d, interval_months=p) for d, p in intervals.items()
    ])

    by_department = logic == SchedulingPlan.LOGIC_DEPARTMENT
    groups = {}
    for dept, desc in Equipment.objects.filter(
        department__workshop=workshop, active_status=True,
    ).values_list("department_id", "description_id"):
        key = dept if by_department else desc
        devices, step_ = groups.get(key, (0, 12))
        groups[key] = (devices + 1, gcd(step_, intervals.get(desc) or default))
    # A group's cycle must fit every interval in it: spread on the shared step.
    layout = engine.spread_layout([(k, n, g) for k, (n, g) in groups.items() if k])

    rules = []
    for key, months in layout.items():
        rule = plan.rules.model(plan=plan)
        rule.months = months
        if by_department:
            rule.department_id = key
        else:
            rule.description_id = key
        rules.append(rule)
    plan.rules.model.objects.bulk_create(rules)
    logger.info(f"[PLAN] Spread {plan}: {len(rules)} rules")
    return plan

def next_version(workshop, program):
    last = SchedulingPlan.objects.filter(workshop=workshop, program=program).order_by("-version").first()
    return (last.version + 1) if last else 1


def new_draft(workshop, program, logic, user=None, source="copy", default_interval=None):
    """Start a draft: a copy of the active plan, today's layout, or blank.

    A copy keeps the active plan's intervals, and its months when the logic
    is unchanged (months per department mean nothing per description).
    """
    if source == "spread":
        return spread_evenly(workshop, program, logic, user, default_interval)
    if source == "adopt":
        plan = adopt_current_layout(workshop, program, logic, user)
        if default_interval:
            plan.default_interval_months = default_interval
            plan.save(update_fields=["default_interval_months"])
        return plan

    active = active_plan(workshop.id, program)
    plan = SchedulingPlan.objects.create(
        workshop=workshop, program=program, logic=logic,
        version=next_version(workshop, program),
        default_interval_months=default_interval or (active.default_interval_months if active else None),
        created_by=user,
        notes=f"Copied from v{active.version}" if source == "copy" and active else "",
    )
    if source == "copy" and active:
        if active.logic == logic:
            plan.rules.model.objects.bulk_create([
                plan.rules.model(plan=plan, department_id=r.department_id,
                                 description_id=r.description_id, month_mask=r.month_mask)
                for r in active.rules.filter(active_status=True)
            ])
        plan.intervals.model.objects.bulk_create([
            plan.intervals.model(plan=plan, description_id=i.description_id,
                                 interval_months=i.interval_months)
            for i in active.intervals.filter(active_status=True)
        ])
    return plan


# ── Bootstrapping ────────────────────────────────────────────────────────────

def adopt_current_layout(workshop, program, logic, user=None):
    """A draft plan that follows the cycle each group mostly keeps today.

    Intervals: each description gets the interval its latest schedule used.
    Months: for each group, the months its devices' intervals share (the
    gcd of those intervals with 12, so the result fits every one of them),
    choosing the cycle that most of the group's open schedules already fall
    in; groups with nothing open fall back to their completed history.
    Today's months are usually scattered across the year, so this is a
    starting point to review and edit, not something to activate unread.
    """
    from math import gcd

    prog = PROGRAMS[program]
    model = prog.model
    plan = SchedulingPlan.objects.create(
        workshop=workshop, program=program, logic=logic,
        version=next_version(workshop, program),
        default_interval_months=6 if program == SchedulingPlan.PROGRAM_PPM else 12,
        created_by=user, notes="Adopted from the current schedule layout",
    )

    intervals = typical_intervals(workshop, program)
    plan.intervals.model.objects.bulk_create([
        plan.intervals.model(plan=plan, description_id=d, interval_months=p)
        for d, p in intervals.items()
    ])

    by_department = logic == SchedulingPlan.LOGIC_DEPARTMENT
    group_of = (lambda dept, desc: dept) if by_department else (lambda dept, desc: desc)
    step_of = {}
    for dept, desc in Equipment.objects.filter(
        department__workshop=workshop, active_status=True,
    ).values_list("department_id", "description_id"):
        group = group_of(dept, desc)
        step_of[group] = gcd(step_of.get(group, 12), intervals.get(desc) or plan.default_interval_months)

    def month_counts(qs):
        counts = {}
        for dept, desc, month in qs.filter(
            equipment__department__workshop=workshop, equipment__active_status=True,
        ).values_list("equipment__department_id", "equipment__description_id", "scheduled_month"):
            counts.setdefault(group_of(dept, desc), []).append(month.month)
        return counts

    current = month_counts(model.open_schedules())
    history = month_counts(model.objects.filter(status="completed", pending_delete=False))

    rules = []
    for group, g in step_of.items():
        seen = current.get(group) or history.get(group)
        if not group or not seen:
            continue  # nothing to go on: left for the administrator
        tally = {}
        for m in seen:
            tally[(m - 1) % g] = tally.get((m - 1) % g, 0) + 1
        residue = min(tally, key=lambda r: (-tally[r], r))
        rule = plan.rules.model(plan=plan)
        rule.months = list(range(residue + 1, 13, g))
        if by_department:
            rule.department_id = group
        else:
            rule.description_id = group
        rules.append(rule)
    plan.rules.model.objects.bulk_create(rules)

    logger.info(f"[PLAN] Adopted {plan}: {len(rules)} rules, {len(intervals)} intervals")
    return plan
