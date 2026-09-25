"""Operate scheduling plans from the command line.

    manage.py scheduling_plan list
    manage.py scheduling_plan adopt --workshop "Biomed" --program ppm --logic description
    manage.py scheduling_plan show <plan-id>
    manage.py scheduling_plan preview <plan-id>          # what activating would move
    manage.py scheduling_plan activate <plan-id> --yes
    manage.py scheduling_plan run [--program ppm] [--dry-run]
    manage.py scheduling_plan unschedulable [--program ppm]
    manage.py scheduling_plan explain <serial-or-asset-tag> --program ppm
"""
from collections import Counter

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from Inventory.models import Equipment
from scheduling import engine, planner
from scheduling.models import SchedulingPlan, months_label
from workshop.models import Workshop


class Command(BaseCommand):
    help = "List, adopt, preview, activate and run scheduling plans"

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("list")

        adopt = sub.add_parser("adopt", help="Draft a plan from today's schedule layout")
        adopt.add_argument("--workshop", required=True, help="Workshop name or id")
        adopt.add_argument("--program", required=True, choices=list(planner.PROGRAMS))
        adopt.add_argument("--logic", required=True,
                           choices=[SchedulingPlan.LOGIC_DEPARTMENT, SchedulingPlan.LOGIC_DESCRIPTION])

        for name in ("show", "preview"):
            sub.add_parser(name).add_argument("plan")
        activate = sub.add_parser("activate")
        activate.add_argument("plan")
        activate.add_argument("--yes", action="store_true", help="Apply (otherwise preview only)")

        run = sub.add_parser("run", help="Schedule unscheduled equipment in planned workshops")
        run.add_argument("--program", choices=list(planner.PROGRAMS))
        run.add_argument("--dry-run", action="store_true")

        unsched = sub.add_parser("unschedulable", help="Equipment a plan cannot place, with reasons")
        unsched.add_argument("--program", choices=list(planner.PROGRAMS))

        explain = sub.add_parser("explain")
        explain.add_argument("equipment", help="Serial number or asset tag")
        explain.add_argument("--program", required=True, choices=list(planner.PROGRAMS))

    def handle(self, *args, action, **opts):
        getattr(self, f"do_{action}")(**opts)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _plan(self, ref):
        try:
            plan = SchedulingPlan.objects.filter(pk=ref).first()
        except (ValueError, ValidationError):
            plan = None
        if not plan:
            raise CommandError(f"No plan {ref!r} (use the id from `scheduling_plan list`)")
        return plan

    def _out(self, text=""):
        self.stdout.write(text)

    # ── actions ─────────────────────────────────────────────────────────────

    def do_list(self, **_):
        for plan in SchedulingPlan.objects.select_related("workshop"):
            self._out(f"{plan.id}  {plan.workshop.name:<24} {plan.program:<12} {plan.logic:<12} "
                      f"v{plan.version:<3} {plan.state:<10} {plan.rules.count()} rules")

    def do_adopt(self, workshop, program, logic, **_):
        ws = Workshop.objects.filter(name__iexact=workshop).first()
        if not ws:
            try:
                ws = Workshop.objects.filter(pk=workshop).first()
            except (ValueError, ValidationError):
                ws = None
        if not ws:
            raise CommandError(f"No workshop {workshop!r}")
        if not planner.owns(ws, program):
            raise CommandError(f"{ws.name} does not plan {program} (PPM belongs to maintenance "
                               "workshops, calibration to the calibration center)")
        plan = planner.adopt_current_layout(ws, program, logic)
        self._out(f"Draft {plan} created: {plan.id}")
        self._out("Review and edit it in the admin, then `preview` and `activate` it.")

    def do_show(self, plan, **_):
        plan = self._plan(plan)
        self._out(str(plan))
        self._out(f"Default interval: {plan.default_interval_months or 'none'} months")
        for rule in plan.rules.select_related("department", "description"):
            self._out(f"  {rule.group_name:<32} {months_label(rule.months)}")
        for iv in plan.intervals.select_related("description"):
            self._out(f"  every {iv.interval_months:>2} months: {iv.description}")

    def do_preview(self, plan, **_):
        self._report(planner.preview_activation(self._plan(plan)))

    def do_activate(self, plan, yes=False, **_):
        plan = self._plan(plan)
        if not yes:
            self._report(planner.preview_activation(plan))
            self._out("\nNothing changed. Re-run with --yes to activate.")
            return
        self._report(planner.activate(plan))

    def _report(self, report):
        self._out(report.summary())
        if report.previous:
            self._out(f"Replaces {report.previous}")
        for sched, row, placement in report.to_move[:50]:
            self._out(f"  move  {row['serial_number']:<20} {engine.month_name(sched.scheduled_month)} -> "
                      f"{placement.message}")
        for sched, row, reason in report.stuck[:50]:
            self._out(f"  keep  {row['serial_number']:<20} {engine.month_name(sched.scheduled_month)}: {reason}")
        shown = len(report.to_move[:50]) + len(report.stuck[:50])
        if len(report.to_move) + len(report.stuck) > shown:
            self._out("  ...")

    def do_run(self, program=None, dry_run=False, **_):
        for result in planner.run_all(program, dry_run=dry_run):
            self._out(result.summary())
            months = Counter(p.month for _, p in result.created)
            for month in sorted(months):
                self._out(f"  {engine.month_name(month)}: {months[month]}")

    def do_unschedulable(self, program=None, **_):
        for result in planner.run_all(program, dry_run=True):
            self._out(str(result.plan))
            for row, placement in result.unschedulable:
                self._out(f"  {row['serial_number']:<20} {placement.message}")

    def do_explain(self, equipment, program, **_):
        lookup = Q(serial_number__iexact=equipment)
        if any(f.name == "asset_tag" for f in Equipment._meta.get_fields()):
            lookup |= Q(asset_tag=equipment)
        eq = Equipment.objects.filter(lookup).first()
        if not eq:
            raise CommandError(f"No equipment {equipment!r}")
        info = planner.explain(eq, program)
        self._out(f"{eq.serial_number} ({eq.description}, {eq.department})")
        self._out("Completed:")
        for row in info["completed"]:
            done = f", done {row['completed_date']:%d %b %Y}" if row["completed_date"] else ""
            self._out(f"  {engine.month_name(row['scheduled_month'])}{done}")
        if info["open"] is not None:
            self._out(f"Open: {engine.month_name(info['open'].scheduled_month)} ({info['open'].status})")
            if info["reason"]:
                self._out(f"  Why: {info['reason'].get('text')}")
        else:
            self._out("Open: none")
        if info["plan"] and info["would_be"] is not None:
            placement = info["would_be"]
            self._out(f"Plan {info['plan']}: " + (placement.message if placement.ok else
                                                  f"cannot schedule: {placement.message}"))
