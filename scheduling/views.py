"""Scheduling screens: overview, month view, unscheduled report, plan editor,
and each device's schedule history.

Who sees what:
    HOD                        every workshop; may edit and activate plans
    Tech, Engineer Incharge    their workshop; may edit and activate plans
    other Tech                 their workshop; read only
Activating a plan moves open schedules, so it is always previewed first and
confirmed on a separate POST.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from Inventory.models import Department, Equipment, EquipmentDescription
from users.control import get_user_role
from workshop.models import Workshop

from . import engine, planner, reports
from .models import MONTH_NAMES, SchedulingPlan, SchedulingRule, mask_to_months, months_to_mask

PROGRAM_LABELS = dict(SchedulingPlan.PROGRAM_CHOICES)


# ── Access ───────────────────────────────────────────────────────────────────

@dataclass
class Scope:
    workshops: list
    workshop: Workshop
    program: str
    can_manage: bool

    @property
    def program_label(self):
        return PROGRAM_LABELS[self.program]

    def query(self):
        return f"?workshop={self.workshop.id}&program={self.program}"


def can_manage(user):
    """May change scheduling plans: an HOD, or a workshop's Engineer in charge."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or get_user_role(user) == "HOD":
        return True
    profile = getattr(user, "userprofile", None)
    return get_user_role(user) == "Tech" and bool(profile) and profile.level == "Engineer Incharge"


def _scope(request):
    user = request.user
    profile = getattr(user, "userprofile", None)
    role = get_user_role(user)
    if role == "HOD" or user.is_superuser:
        workshops = list(Workshop.objects.filter(active_status=True).order_by("name"))
    elif role == "Tech" and profile:
        ws = profile.workshop or (profile.department.workshop if profile.department_id else None)
        workshops = [ws] if ws else []
    else:
        raise PermissionDenied("Scheduling is available to HODs and technicians.")
    if not workshops:
        raise PermissionDenied("Your profile has no workshop.")

    by_id = {str(w.id): w for w in workshops}
    wanted = request.GET.get("workshop") or request.session.get("scheduling_workshop")
    workshop = by_id.get(str(wanted)) or workshops[0]
    program = request.GET.get("program") or request.session.get("scheduling_program")
    if program not in planner.PROGRAMS:
        program = (SchedulingPlan.PROGRAM_CALIBRATION if workshop.category == "calibration_center"
                   else SchedulingPlan.PROGRAM_PPM)
    request.session["scheduling_workshop"] = str(workshop.id)
    request.session["scheduling_program"] = program
    return Scope(workshops, workshop, program, can_manage(user))


def _plan_in_scope(request, plan_id):
    scope = _scope(request)
    plan = get_object_or_404(SchedulingPlan, pk=plan_id)
    if plan.workshop_id not in {w.id for w in scope.workshops}:
        raise Http404
    scope.workshop, scope.program = plan.workshop, plan.program
    return scope, plan


def _require_manage(scope):
    if not scope.can_manage:
        raise PermissionDenied("Only an HOD or the Engineer in charge can change scheduling plans.")


def _render(request, template, scope, **context):
    return render(request, template, {"scope": scope, "tab": context.pop("tab", ""),
                                      "show_sidebar": True, **context})


# ── Overview ─────────────────────────────────────────────────────────────────

@login_required
def overview(request):
    scope = _scope(request)
    data = reports.overview(scope.workshop, scope.program)
    draft = SchedulingPlan.objects.filter(
        workshop=scope.workshop, program=scope.program, state=SchedulingPlan.STATE_DRAFT,
    ).order_by("-version").first()
    return _render(request, "scheduling/overview.html", scope, tab="overview", draft=draft, **data)


# ── Month view ───────────────────────────────────────────────────────────────

@login_required
def months(request):
    scope = _scope(request)
    today = reports.this_month()
    try:
        year = int(request.GET.get("year") or today.year)
    except ValueError:
        year = today.year
    return _render(request, "scheduling/months.html", scope, tab="months", year=year,
                   months=reports.year_months(scope.workshop, scope.program, year),
                   current=today)


@login_required
def month_detail(request, year, month):
    scope = _scope(request)
    if not 1 <= month <= 12:
        raise Http404
    first = date(year, month, 1)
    schedules = list(reports.month_schedules(scope.workshop, scope.program, first))
    status = request.GET.get("status")
    if status == "completed":
        schedules = [s for s in schedules if s.status == "completed"]
    elif status == "open":
        schedules = [s for s in schedules if s.status != "completed"]
    by_department = defaultdict(list)
    for s in schedules:
        by_department[s.equipment.department.name].append(s)
    return _render(request, "scheduling/month_detail.html", scope, tab="months", month=first,
                   groups=sorted(by_department.items()), count=len(schedules), status=status,
                   overdue=first < reports.this_month(),
                   previous=engine.add_months(first, -1), next=engine.add_months(first, 1))


# ── Unscheduled ──────────────────────────────────────────────────────────────

@login_required
def unscheduled(request):
    scope = _scope(request)
    data = reports.unscheduled(scope.workshop, scope.program)
    health = reports.audit(data["plan"]) if data["plan"] else None
    reasons = Counter(label for _, label, _ in data["blocked"])
    return _render(request, "scheduling/unscheduled.html", scope, tab="unscheduled",
                   reasons=sorted(reasons.items(), key=lambda kv: -kv[1]),
                   off_plan=health.off_plan if health else [], **data)


# ── Plans ────────────────────────────────────────────────────────────────────

@login_required
def plans(request):
    scope = _scope(request)
    versions = SchedulingPlan.objects.filter(
        workshop=scope.workshop, program=scope.program,
    ).annotate(n_rules=Count("rules")).order_by("-version")
    return _render(request, "scheduling/plans.html", scope, tab="plans", versions=versions,
                   active=planner.active_plan(scope.workshop.id, scope.program),
                   logics=SchedulingPlan.LOGIC_CHOICES)


@login_required
@require_POST
def plan_new(request):
    scope = _scope(request)
    _require_manage(scope)
    logic = request.POST.get("logic")
    source = request.POST.get("source", "copy")
    if logic not in dict(SchedulingPlan.LOGIC_CHOICES) or source not in ("copy", "adopt", "spread", "blank"):
        messages.error(request, "Choose a grouping and a starting point.")
        return redirect(reverse("scheduling:plans") + scope.query())
    existing = SchedulingPlan.objects.filter(
        workshop=scope.workshop, program=scope.program, state=SchedulingPlan.STATE_DRAFT).first()
    if existing:
        messages.info(request, f"Version {existing.version} is already a draft: carry on editing it.")
        return redirect("scheduling:plan", existing.id)
    plan = planner.new_draft(scope.workshop, scope.program, logic, request.user, source,
                             _int(request.POST.get("default_interval")))
    messages.success(request, f"Draft version {plan.version} created.")
    return redirect("scheduling:plan", plan.id)


def _int(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 60 else None


def _editor_rows(plan):
    """Groups and descriptions the editor lists, with device counts and warnings."""
    ws = plan.workshop
    equipment = Equipment.objects.filter(department__workshop=ws, active_status=True)
    rules = {r.group_id: r for r in plan.rules.select_related("department", "description")}
    intervals = dict(plan.intervals.values_list("description_id", "interval_months"))

    desc_counts = dict(equipment.values_list("description_id").annotate(n=Count("id")))
    descriptions = list(EquipmentDescription.objects.filter(
        Q(id__in=desc_counts.keys()) | Q(id__in=intervals.keys())).order_by("name"))

    if plan.is_department:
        dept_counts = dict(equipment.values_list("department_id").annotate(n=Count("id")))
        groups = list(Department.objects.filter(
            Q(workshop=ws, active_status=True) | Q(id__in=rules.keys())).order_by("name"))
        counts = dept_counts
    else:
        groups = descriptions
        counts = desc_counts

    warnings = defaultdict(Counter)
    for row, code, message in reports.audit(plan).problem_rows:
        group_id = row["department_id"] if plan.is_department else row["description_id"]
        if code != "no_months":
            warnings[group_id][message] += 1

    group_rows = []
    for g in groups:
        rule = rules.get(g.id)
        group_rows.append({
            "id": g.id, "name": g.name, "devices": counts.get(g.id, 0),
            "months": set(mask_to_months(rule.month_mask)) if rule else set(),
            "interval": intervals.get(g.id, "") if not plan.is_department else None,
            "warnings": [f"{n} device{'s' if n != 1 else ''}: {m}" for m, n in warnings[g.id].items()],
        })
    interval_rows = [{"id": d.id, "name": d.name, "devices": desc_counts.get(d.id, 0),
                      "interval": intervals.get(d.id, "")} for d in descriptions]
    return group_rows, interval_rows


@login_required
def plan_detail(request, plan_id):
    scope, plan = _plan_in_scope(request, plan_id)
    editable = plan.state == SchedulingPlan.STATE_DRAFT and scope.can_manage
    if request.method == "POST":
        if not editable:
            raise PermissionDenied("Only a draft can be edited.")
        _save_draft(request, plan)
        messages.success(request, f"Draft version {plan.version} saved.")
        if request.POST.get("then") == "preview":
            return redirect("scheduling:plan_preview", plan.id)
        return redirect("scheduling:plan", plan.id)

    group_rows, interval_rows = _editor_rows(plan)
    return _render(request, "scheduling/plan.html", scope, tab="plans", plan=plan, editable=editable,
                   group_rows=group_rows, interval_rows=interval_rows,
                   month_names=list(enumerate(MONTH_NAMES, start=1)))


@transaction.atomic
def _save_draft(request, plan):
    post = request.POST
    plan.default_interval_months = _int(post.get("default_interval"))
    plan.notes = post.get("notes", "")[:2000]
    plan.save(update_fields=["default_interval_months", "notes"])

    group_ids = post.getlist("group")
    rules = {str(r.group_id): r for r in plan.rules.all()}
    for gid in group_ids:
        mask = months_to_mask(int(m) for m in post.getlist(f"months_{gid}") if m.isdigit())
        rule = rules.get(gid)
        if mask and rule:
            if rule.month_mask != mask:
                rule.month_mask = mask
                rule.save(update_fields=["month_mask"])
        elif mask:
            field = "department_id" if plan.is_department else "description_id"
            SchedulingRule.objects.create(plan=plan, month_mask=mask, **{field: gid})
        elif rule:
            rule.delete()

    intervals = {str(i.description_id): i for i in plan.intervals.all()}
    for did in post.getlist("interval_description"):
        value = _int(post.get(f"interval_{did}"))
        current = intervals.get(did)
        if value and current:
            if current.interval_months != value:
                current.interval_months = value
                current.save(update_fields=["interval_months"])
        elif value:
            plan.intervals.create(description_id=did, interval_months=value)
        elif current:
            current.delete()


@login_required
def plan_preview(request, plan_id):
    scope, plan = _plan_in_scope(request, plan_id)
    if plan.state != SchedulingPlan.STATE_DRAFT:
        return redirect("scheduling:plan", plan.id)
    report = planner.preview_activation(plan)
    placement = planner.schedule(plan, dry_run=True)
    unschedulable = Counter(reports.PROBLEM_LABELS.get(p.problem, p.problem)
                            for _, p in placement.unschedulable)
    return _render(request, "scheduling/preview.html", scope, tab="plans", plan=plan, report=report,
                   new_placements=placement.created, unschedulable=sorted(unschedulable.items()),
                   unschedulable_total=len(placement.unschedulable))


@login_required
@require_POST
def plan_activate(request, plan_id):
    scope, plan = _plan_in_scope(request, plan_id)
    _require_manage(scope)
    if plan.state != SchedulingPlan.STATE_DRAFT:
        messages.error(request, "Only a draft can be activated.")
        return redirect("scheduling:plan", plan.id)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Tick the confirmation box to activate.")
        return redirect("scheduling:plan_preview", plan.id)

    report = planner.activate(plan, request.user)
    run = planner.schedule(plan)
    messages.success(
        request,
        f"Version {plan.version} is active. Moved {len(report.to_move)} open schedules, "
        f"scheduled {len(run.created)} devices. {len(report.stuck)} schedules need review and "
        f"{len(run.unschedulable)} devices could not be placed.",
    )
    return redirect(reverse("scheduling:unscheduled") + scope.query()
                    if report.stuck or run.unschedulable else reverse("scheduling:overview") + scope.query())


@login_required
@require_POST
def change_logic(request):
    """One click: a new version grouped the other way, months spread evenly.

    Opens its preview; nothing moves until it is activated there.
    """
    scope = _scope(request)
    _require_manage(scope)
    logic = request.POST.get("logic")
    if logic not in dict(SchedulingPlan.LOGIC_CHOICES):
        messages.error(request, "Choose department or description.")
        return redirect(reverse("scheduling:plans") + scope.query())
    draft = planner.change_logic(scope.workshop, scope.program, logic, request.user)
    messages.info(request, f"Draft v{draft.version} groups {draft.get_logic_display().lower()} with "
                           "months spread evenly. Check what moves, then activate.")
    return redirect("scheduling:plan_preview", draft.id)


@login_required
@require_POST
def plan_delete(request, plan_id):
    scope, plan = _plan_in_scope(request, plan_id)
    _require_manage(scope)
    if plan.state != SchedulingPlan.STATE_DRAFT:
        raise PermissionDenied("Only a draft can be discarded.")
    plan.delete()
    messages.success(request, f"Draft version {plan.version} discarded.")
    return redirect(reverse("scheduling:plans") + scope.query())


# ── Equipment history ────────────────────────────────────────────────────────

@login_required
def equipment_history(request, equipment_id):
    scope = _scope(request)
    equipment = get_object_or_404(
        Equipment.objects.select_related("department__workshop", "description"), pk=equipment_id)
    if equipment.department.workshop_id not in {w.id for w in scope.workshops}:
        raise Http404
    programs = []
    for key, label in SchedulingPlan.PROGRAM_CHOICES:
        info = planner.explain(equipment, key)
        model = planner.PROGRAMS[key].model
        info["completed"] = list(model.objects.filter(
            equipment=equipment, status="completed", pending_delete=False,
        ).order_by("-scheduled_month").select_related("plan"))
        info["opens"] = list(model.open_schedules().filter(equipment=equipment).order_by("scheduled_month"))
        programs.append({"key": key, "label": label, **info})
    return _render(request, "scheduling/equipment.html", scope, equipment=equipment, programs=programs)
