"""Scheduling plans end to end: the ten scenarios from the redesign brief,
plus the rollout guarantees (unplanned workshops untouched, transfers, new
equipment, completions that arrive without signals)."""
from datetime import date
from io import StringIO
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from calSchedules.models import CalibrationSchedule
from Inventory.models import Department, Equipment, EquipmentDescription
from ppms.models import PPMSchedule
from scheduling import planner
from scheduling.models import SchedulingPlan, SchedulingRule
from workshop.models import Workshop

TODAY = date(2026, 9, 15)          # plans place first visits from October 2026
REAL_MONTH = date.today().replace(day=1)
SYNC_TARGETS = (
    "calSchedules.reconciliation.trigger_sync_for_changes",
    "calSchedules.instant_reconciliation.helpers.trigger_sync_for_changes",
    "calSchedules.instant_reconciliation.alignment.trigger_sync_for_changes",
    "calSchedules.instant_reconciliation.signals.trigger_sync_for_changes",
)


def d(year, month):
    return date(year, month, 1)


class PlanTestBase(TestCase):
    def setUp(self):
        for target in SYNC_TARGETS:
            patcher = patch(target, return_value=0)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ws = Workshop.objects.create(name="Biomed")
        self.icu = Department.objects.create(name="ICU", workshop=self.ws)
        self.lab = Department.objects.create(name="Lab", workshop=self.ws)
        self.monitor = EquipmentDescription.objects.create(name="Patient Monitor")
        self.pump = EquipmentDescription.objects.create(name="Infusion Pump")
        self.analyser = EquipmentDescription.objects.create(name="Analyser")
        self.n = 0

    def plan(self, program="ppm", logic="description", rules=None, intervals=None,
             default=None, state="active", version=1):
        plan = SchedulingPlan.objects.create(
            workshop=self.ws, program=program, logic=logic, version=version,
            state=state, default_interval_months=default,
        )
        for group, months in (rules or {}).items():
            rule = SchedulingRule(plan=plan)
            rule.months = months
            if logic == "department":
                rule.department = group
            else:
                rule.description = group
            rule.save()
        for desc, months in (intervals or {}).items():
            plan.intervals.create(description=desc, interval_months=months)
        return plan

    def equipment(self, desc=None, dept=None, clean=True):
        self.n += 1
        eq = Equipment.objects.create(
            description=desc or self.monitor, department=dept or self.icu, workshop=self.ws,
            model="M", serial_number=f"EQ-{self.n:05d}", status="Working",
        )
        if clean:
            PPMSchedule.objects.filter(equipment=eq).delete()
            CalibrationSchedule.objects.filter(equipment=eq).delete()
        return eq

    def model(self, program):
        return PPMSchedule if program == "ppm" else CalibrationSchedule

    def open_months(self, eq, program="ppm"):
        return list(self.model(program).open_schedules().filter(equipment=eq)
                    .order_by("scheduled_month").values_list("scheduled_month", flat=True))

    def complete(self, eq, program="ppm", done=None):
        sched = self.model(program).open_schedules().get(equipment=eq)
        sched.status = "completed"
        sched.completed_date = done or sched.scheduled_month
        sched.save()
        return sched


class ScenarioTests(PlanTestBase):
    """The ten scenarios from the brief, in order."""

    def test_1_equipment_with_no_schedules_gets_one_pending(self):
        plan = self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        eq = self.equipment()
        result = planner.schedule(plan, today=TODAY)
        self.assertEqual(self.open_months(eq), [d(2027, 2)])
        sched = PPMSchedule.objects.get(equipment=eq)
        self.assertEqual(sched.status, "pending")
        self.assertEqual(sched.plan, plan)
        self.assertEqual(sched.due_month, d(2027, 2))
        self.assertIn("Feb", sched.schedule_reason["text"])
        self.assertEqual(len(result.created), 1)

    def test_2_equipment_with_a_pending_schedule_is_left_alone(self):
        plan = self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        eq = self.equipment()
        PPMSchedule.objects.create(equipment=eq, workshop=self.ws, scheduled_month=d(2026, 11))
        result = planner.schedule(plan, today=TODAY)
        self.assertEqual(self.open_months(eq), [d(2026, 11)])
        self.assertEqual(result.already_scheduled, 1)

    def test_3_history_is_untouched_and_one_new_pending(self):
        plan = self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        eq = self.equipment()
        history = [d(2025, 8), d(2026, 2), d(2026, 8)]
        for m in history:
            PPMSchedule.objects.create(equipment=eq, workshop=self.ws, scheduled_month=m,
                                       status="completed", completed_date=m)
        before = list(PPMSchedule.objects.filter(status="completed").values_list("id", "scheduled_month", "updated_at"))

        planner.schedule(plan, today=TODAY)

        after = list(PPMSchedule.objects.filter(status="completed").values_list("id", "scheduled_month", "updated_at"))
        self.assertEqual(before, after)
        self.assertEqual(self.open_months(eq), [d(2027, 2)])

    def test_4_six_months_february_august(self):
        self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        eq = self.equipment(clean=False)  # scheduled by the new-equipment signal
        first = self.open_months(eq)[0]
        self.assertIn(first.month, (2, 8))
        seen = [first]
        for _ in range(3):
            self.complete(eq)
            seen += self.open_months(eq)
        self.assertEqual(seen, [first + relativedelta(months=6 * i) for i in range(4)])
        self.assertEqual({m.month for m in seen}, {2, 8})
        self.assertEqual(PPMSchedule.objects.filter(equipment=eq, status="completed").count(), 3)

    def test_5_twelve_months_march(self):
        self.plan(program="calibration", rules={self.analyser: [3]}, intervals={self.analyser: 12})
        eq = self.equipment(desc=self.analyser, clean=False)
        seen = self.open_months(eq, "calibration")
        for _ in range(3):
            self.complete(eq, "calibration")
            seen += self.open_months(eq, "calibration")
        self.assertEqual([m.month for m in seen], [3, 3, 3, 3])
        self.assertEqual([m.year for m in seen], list(range(seen[0].year, seen[0].year + 4)))

    def test_6_three_months_quarters(self):
        self.plan(rules={self.pump: [1, 4, 7, 10]}, intervals={self.pump: 3})
        eq = self.equipment(desc=self.pump, clean=False)
        seen = self.open_months(eq)
        for _ in range(4):
            self.complete(eq)
            seen += self.open_months(eq)
        months = [m.month for m in seen]
        start = [1, 4, 7, 10].index(months[0])
        self.assertEqual(months, [[1, 4, 7, 10][(start + i) % 4] for i in range(5)])
        # Never more than one pending at a time.
        self.assertEqual(len(self.open_months(eq)), 1)

    def test_7_five_thousand_devices(self):
        plan = self.plan(
            rules={self.monitor: [2, 8], self.pump: [1, 4, 7, 10], self.analyser: [3, 9]},
            intervals={self.monitor: 6, self.pump: 3, self.analyser: 12},
        )
        no_rule = EquipmentDescription.objects.create(name="Unplanned Device")
        descs = [self.monitor, self.pump, self.analyser, no_rule]
        Equipment.objects.bulk_create([
            Equipment(description=descs[i % 4], department=self.icu, workshop=self.ws, model="M",
                      serial_number=f"BULK-{i:05d}", status="Working")
            for i in range(5000)
        ])

        dry1 = planner.schedule(plan, today=TODAY, dry_run=True)
        dry2 = planner.schedule(plan, today=TODAY, dry_run=True)
        as_map = lambda r: {row["id"]: p.month for row, p in r.created}  # noqa: E731
        self.assertEqual(as_map(dry1), as_map(dry2))
        self.assertEqual(PPMSchedule.objects.count(), 0)

        with CaptureQueriesContext(connection) as queries:
            result = planner.schedule(plan, today=TODAY)
        # Inserts come in batches (SQLite caps variables per statement); every
        # other query is per run, not per device.
        reads = [q for q in queries if not q["sql"].lstrip().upper().startswith("INSERT")]
        self.assertLess(len(reads), 20, "scheduling must not query per device")

        self.assertEqual(as_map(result), as_map(dry1))
        self.assertEqual(len(result.created), 3750)
        self.assertEqual(len(result.unschedulable), 1250)
        self.assertTrue(all(p.problem == "no_months" and "Unplanned Device" in p.message
                            for _, p in result.unschedulable))
        # One open schedule each; months only ever from the group's rule.
        self.assertEqual(PPMSchedule.open_schedules().count(), 3750)
        allowed = {self.monitor.id: {2, 8}, self.pump.id: {1, 4, 7, 10}, self.analyser.id: {3, 9}}
        for desc_id, month in PPMSchedule.objects.values_list("equipment__description_id", "scheduled_month"):
            self.assertIn(month.month, allowed[desc_id])
        # 12-month analysers are split evenly between March and September.
        split = PPMSchedule.objects.filter(equipment__description=self.analyser).values_list(
            "scheduled_month__month", flat=True)
        self.assertEqual(sorted(set(split)), [3, 9])
        self.assertLessEqual(abs(list(split).count(3) - list(split).count(9)), 1)

    def test_8_changing_the_logic(self):
        v1 = self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        done_eq, open_eq = self.equipment(), self.equipment()
        PPMSchedule.objects.create(equipment=done_eq, workshop=self.ws, scheduled_month=d(2026, 8),
                                   status="completed", completed_date=d(2026, 8))
        planner.schedule(v1, today=TODAY)
        completed = PPMSchedule.objects.get(status="completed")
        self.assertEqual(self.open_months(open_eq), [d(2027, 2)])

        v2 = self.plan(rules={self.monitor: [3, 9]}, intervals={self.monitor: 6},
                       state="draft", version=2)
        preview = planner.preview_activation(v2, today=TODAY)
        self.assertEqual(len(preview.to_move), 2)       # both open schedules are Feb
        self.assertEqual(self.open_months(open_eq), [d(2027, 2)])  # preview changed nothing

        report = planner.activate(v2, today=TODAY)
        self.assertTrue(report.applied)
        v1.refresh_from_db()
        self.assertEqual(v1.state, "superseded")
        self.assertEqual(self.open_months(open_eq), [d(2027, 3)])
        self.assertEqual(self.open_months(done_eq), [d(2027, 3)])
        completed.refresh_from_db()
        self.assertEqual(completed.scheduled_month, d(2026, 8))
        moved = PPMSchedule.open_schedules().get(equipment=open_eq)
        self.assertEqual(moved.plan, v2)
        self.assertEqual(moved.schedule_reason["moved_from"], "Feb 2027")

        # Future scheduling follows v2.
        new_eq = self.equipment()
        planner.schedule(planner.active_plan(self.ws.id, "ppm"), today=TODAY)
        self.assertEqual(self.open_months(new_eq), [d(2027, 3)])

    def test_8b_work_under_way_is_not_moved_by_a_logic_change(self):
        v1 = self.plan(program="calibration", rules={self.analyser: [3]},
                       intervals={self.analyser: 12})
        busy, idle = self.equipment(desc=self.analyser), self.equipment(desc=self.analyser)
        planner.schedule(v1, today=TODAY)
        CalibrationSchedule.objects.filter(equipment=busy).update(status="in_progress")
        v2 = self.plan(program="calibration", rules={self.analyser: [6]},
                       intervals={self.analyser: 12}, state="draft", version=2)

        report = planner.activate(v2, today=TODAY)

        self.assertEqual(self.open_months(busy, "calibration"), [d(2027, 3)])
        self.assertEqual(self.open_months(idle, "calibration"), [d(2027, 6)])
        self.assertEqual([sched.equipment_id for sched, _, _ in report.stuck], [busy.id])
        self.assertIn("under way", report.stuck[0][2])

    def test_9_running_again_and_again_changes_nothing(self):
        plan = self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        for _ in range(5):
            self.equipment()
        planner.schedule(plan, today=TODAY)
        snapshot = list(PPMSchedule.objects.order_by("id").values_list("id", "scheduled_month", "updated_at"))
        for _ in range(3):
            result = planner.schedule(plan, today=TODAY)
            self.assertEqual(result.created, [])
        self.assertEqual(
            list(PPMSchedule.objects.order_by("id").values_list("id", "scheduled_month", "updated_at")),
            snapshot)

    def test_10_missing_configuration_is_reported_with_a_reason(self):
        plan = self.plan(rules={self.monitor: [2, 8], self.pump: [1, 7]},
                         intervals={self.pump: 3})
        no_rule = self.equipment(desc=self.analyser)
        no_interval = self.equipment(desc=self.monitor)
        wont_fit = self.equipment(desc=self.pump)
        result = planner.schedule(plan, today=TODAY)
        problems = {row["id"]: p for row, p in result.unschedulable}
        self.assertEqual(problems[no_rule.id].problem, "no_months")
        self.assertEqual(problems[no_interval.id].problem, "no_interval")
        self.assertEqual(problems[wont_fit.id].problem, "interval_does_not_fit")
        self.assertIn("needs 4", problems[wont_fit.id].message)
        self.assertEqual(PPMSchedule.objects.count(), 0)

        out = StringIO()
        call_command("scheduling_plan", "unschedulable", stdout=out)
        self.assertIn("Analyser has no months", out.getvalue())


class RolloutTests(PlanTestBase):
    def test_unplanned_workshop_keeps_legacy_behaviour(self):
        other = Workshop.objects.create(name="Other")
        dept = Department.objects.create(name="Ward 1", workshop=other)
        self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        eq = Equipment.objects.create(description=self.monitor, department=dept, workshop=other,
                                      model="M", serial_number="LEGACY-1", status="Working")
        sched = PPMSchedule.objects.get(equipment=eq)
        self.assertEqual(sched.scheduled_month, REAL_MONTH + relativedelta(months=1))
        self.assertIsNone(sched.plan)

    def test_group_members_do_not_wait_for_each_other(self):
        self.plan(logic="department", rules={self.icu: [2, 8]}, default=6)
        a, b = self.equipment(clean=False), self.equipment(clean=False)
        self.assertEqual(self.open_months(a), self.open_months(b))
        first = self.open_months(a)[0]
        self.complete(a)
        self.assertEqual(self.open_months(a), [first + relativedelta(months=6)])
        self.assertEqual(self.open_months(b), [first])

    def test_transfer_moves_the_open_schedule_to_the_new_departments_months(self):
        self.plan(logic="department", rules={self.icu: [2, 8], self.lab: [3, 9]}, default=6)
        eq = self.equipment(clean=False)
        self.assertIn(self.open_months(eq)[0].month, (2, 8))
        eq.department = self.lab
        eq.save()
        months = self.open_months(eq)
        self.assertEqual(len(months), 1)
        self.assertIn(months[0].month, (3, 9))

    def test_new_equipment_gets_a_plan_schedule_not_next_month(self):
        self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        eq = self.equipment(clean=False)
        scheds = list(PPMSchedule.objects.filter(equipment=eq))
        self.assertEqual(len(scheds), 1)
        self.assertEqual(scheds[0].generation_source, "plan")
        self.assertIn(scheds[0].scheduled_month.month, (2, 8))

    def test_completion_that_arrived_by_sync_is_picked_up(self):
        plan = self.plan(program="calibration", rules={self.analyser: [3]},
                         intervals={self.analyser: 12})
        eq = self.equipment(desc=self.analyser)
        planner.schedule(plan, today=TODAY)
        # Sync writes completions with raw SQL: no signal fires.
        CalibrationSchedule.objects.filter(equipment=eq).update(status="completed",
                                                                completed_date=d(2027, 3))
        self.assertEqual(self.open_months(eq, "calibration"), [])
        from calSchedules.tasks import auto_reschedule_completed_task
        auto_reschedule_completed_task()
        self.assertEqual(self.open_months(eq, "calibration"), [d(2028, 3)])

    def test_calibration_aligner_leaves_plan_schedules_alone(self):
        self.plan(program="calibration", rules={self.analyser: [3]}, intervals={self.analyser: 12})
        eq = self.equipment(desc=self.analyser, clean=False)
        month = self.open_months(eq, "calibration")[0]
        # A legacy schedule for another device of the same department, in
        # another month, used to pull every new save to the group's month.
        other = self.equipment(desc=self.analyser)
        CalibrationSchedule.objects.create(equipment=other, workshop=self.ws,
                                           scheduled_month=REAL_MONTH, status="pending")
        sched = CalibrationSchedule.open_schedules().get(equipment=eq)
        sched.save()
        sched.refresh_from_db()
        self.assertEqual(sched.scheduled_month, month)

    def test_legacy_normalization_skips_planned_workshops(self):
        from ppms.tasks import normalize_ppm_schedules
        self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        eq = self.equipment()
        legacy = PPMSchedule.objects.create(equipment=eq, workshop=self.ws,
                                            scheduled_month=d(REAL_MONTH.year + 1, 5),
                                            generation_source="initialization")
        normalize_ppm_schedules(planning_logic="department")
        legacy.refresh_from_db()
        self.assertEqual(legacy.scheduled_month, d(REAL_MONTH.year + 1, 5))

    def test_ids_are_deterministic(self):
        plan = self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        eq = self.equipment()
        planner.schedule(plan, today=TODAY)
        sched = PPMSchedule.objects.get(equipment=eq)
        self.assertEqual(sched.id, planner.schedule_id("ppm", eq.id, d(2027, 2)))


class AdoptAndCommandTests(PlanTestBase):
    def test_adopt_current_layout(self):
        eq1, eq2 = self.equipment(), self.equipment(desc=self.pump, dept=self.lab)
        PPMSchedule.objects.create(equipment=eq1, workshop=self.ws, scheduled_month=d(2027, 2),
                                   maintenance_period=6)
        PPMSchedule.objects.create(equipment=eq2, workshop=self.ws, scheduled_month=d(2027, 5),
                                   maintenance_period=3)
        plan = planner.adopt_current_layout(self.ws, "ppm", "department")
        self.assertEqual(plan.state, "draft")
        rules = {r.department_id: r.months for r in plan.rules.all()}
        self.assertEqual(rules, {self.icu.id: [2], self.lab.id: [5]})
        intervals = dict(plan.intervals.values_list("description_id", "interval_months"))
        self.assertEqual(intervals, {self.monitor.id: 6, self.pump.id: 3})

    def test_command_explains_a_device(self):
        plan = self.plan(rules={self.monitor: [2, 8]}, intervals={self.monitor: 6})
        eq = self.equipment()
        planner.schedule(plan, today=TODAY)
        out = StringIO()
        call_command("scheduling_plan", "explain", eq.serial_number, "--program", "ppm", stdout=out)
        self.assertIn("Why: first visit", out.getvalue())
