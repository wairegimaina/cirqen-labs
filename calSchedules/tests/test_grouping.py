"""Pure-logic unit tests for calSchedules.grouping (no database)."""
from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase

from calSchedules import grouping


class CanonicalLogicTests(SimpleTestCase):
    def test_aliases_map_to_runtime_pair(self):
        self.assertEqual(grouping.canonical_logic("date_based"), "department")
        self.assertEqual(grouping.canonical_logic("description_based"), "description")

    def test_runtime_values_pass_through(self):
        self.assertEqual(grouping.canonical_logic("department"), "department")
        self.assertEqual(grouping.canonical_logic("description"), "description")

    def test_unknown_passes_through(self):
        self.assertEqual(grouping.canonical_logic("whatever"), "whatever")
        self.assertIsNone(grouping.canonical_logic(None))


class GroupIdentityTests(SimpleTestCase):
    def test_group_field_lookup(self):
        self.assertEqual(grouping.group_field_lookup("department"), "equipment__department_id")
        self.assertEqual(grouping.group_field_lookup("description"), "equipment__description_id")
        # both vocabularies agree: date_based === department, description_based === description
        self.assertEqual(grouping.group_field_lookup("date_based"), "equipment__department_id")
        self.assertEqual(grouping.group_field_lookup("description_based"), "equipment__description_id")

    def test_group_id_for_honors_aliases(self):
        eq = SimpleNamespace(department_id="d1", description_id="x9")
        self.assertEqual(grouping.group_id_for(eq, "date_based"), "d1")          # department
        self.assertEqual(grouping.group_id_for(eq, "description_based"), "x9")   # description

    def test_group_key_same_for_both_vocabularies(self):
        eq = SimpleNamespace(department_id="d1", department=object(),
                             description_id="x9", description=object())
        sched = SimpleNamespace(equipment=eq, scheduled_month=date(2026, 3, 1))
        self.assertEqual(
            grouping.group_key(sched, "date_based"),
            grouping.group_key(sched, "department"),
        )

    def test_group_id_for(self):
        eq = SimpleNamespace(department_id="d1", description_id="x9")
        self.assertEqual(grouping.group_id_for(eq, "department"), "d1")
        self.assertEqual(grouping.group_id_for(eq, "description"), "x9")
        self.assertIsNone(grouping.group_id_for(None, "department"))

    def test_group_key_with_related_objects(self):
        eq = SimpleNamespace(department_id="d1", department=object(),
                             description_id="x9", description=object())
        sched = SimpleNamespace(equipment=eq, scheduled_month=date(2026, 3, 1))
        self.assertEqual(grouping.group_key(sched, "department"), "department_d1_2026-03")
        self.assertEqual(grouping.group_key(sched, "description"), "description_x9_2026-03")

    def test_group_key_sentinels_when_missing(self):
        eq = SimpleNamespace(department_id=None, department=None,
                             description_id=None, description=None)
        sched = SimpleNamespace(equipment=eq, scheduled_month=None)
        self.assertEqual(grouping.group_key(sched, "department"), "department_no_department_unknown")
        self.assertEqual(grouping.group_key(sched, "description"), "description_no_description_unknown")


class DateMathTests(SimpleTestCase):
    def test_planning_year_rolls_after_june(self):
        self.assertEqual(grouping.planning_year(date(2026, 6, 30)), 2026)
        self.assertEqual(grouping.planning_year(date(2026, 7, 1)), 2027)

    def test_next_period_month(self):
        self.assertEqual(grouping.next_period_month(date(2026, 3, 1), 12), date(2027, 3, 1))
        self.assertEqual(grouping.next_period_month(date(2026, 3, 1), 6), date(2026, 9, 1))

    def test_clamp_far_future_leaves_reasonable_dates(self):
        today = date(2026, 3, 15)
        nxt = grouping.next_period_month(date(2026, 1, 1), 12)  # 2027-01, within period+6
        self.assertEqual(grouping.clamp_far_future_month(nxt, today, 12), nxt)

    def test_clamp_far_future_rebases_stale_base(self):
        today = date(2026, 3, 15)
        # base month long in the past → next would be far future beyond period+slack
        nxt = grouping.next_period_month(date(2020, 1, 1), 12)  # 2021-01 (already past, not future)
        # construct a genuinely-too-far case: base far in the future
        far = grouping.next_period_month(date(2030, 1, 1), 12)  # 2031-01
        rebased = grouping.clamp_far_future_month(far, today, 12)
        self.assertEqual(rebased, today.replace(day=1) + grouping.relativedelta(months=12))
        # the near case is unchanged
        self.assertEqual(grouping.clamp_far_future_month(nxt, today, 12), nxt)


class ProtectionAndCompletionTests(SimpleTestCase):
    def test_is_protected(self):
        prot = ["signal", "locker", "job_card"]
        self.assertTrue(grouping.is_protected("completed", False, "manual", prot))
        self.assertTrue(grouping.is_protected("pending", True, "manual", prot))
        self.assertTrue(grouping.is_protected("pending", False, "signal", prot))
        self.assertFalse(grouping.is_protected("pending", False, "manual", prot))

    def test_completion_stats(self):
        self.assertEqual(
            grouping.completion_stats(3, 3),
            {"total": 3, "completed": 3, "all_completed": True},
        )
        self.assertEqual(
            grouping.completion_stats(3, 2),
            {"total": 3, "completed": 2, "all_completed": False},
        )
        # empty group is never "all completed"
        self.assertFalse(grouping.completion_stats(0, 0)["all_completed"])


class FindOptimalMonthTests(SimpleTestCase):
    def test_memoises_per_group(self):
        month_map = {}
        start = date(2026, 1, 1)
        first = grouping.find_optimal_month(start, 1, "g1", month_map, max_per_month=2, is_special=False)
        again = grouping.find_optimal_month(start, 1, "g1", month_map, max_per_month=2, is_special=False)
        self.assertEqual(first, again)  # same group → same month
        self.assertEqual(first, start)

    def test_load_balances_across_months(self):
        month_map = {}
        start = date(2026, 1, 1)
        # max 1 per month → each new group pushed to the next free month
        m1 = grouping.find_optimal_month(start, 1, "g1", month_map, max_per_month=1, is_special=False)
        m2 = grouping.find_optimal_month(start, 1, "g2", month_map, max_per_month=1, is_special=False)
        self.assertEqual(m1, date(2026, 1, 1))
        self.assertEqual(m2, date(2026, 2, 1))

    def test_special_ignores_capacity(self):
        month_map = {}
        start = date(2026, 1, 1)
        m1 = grouping.find_optimal_month(start, 1, "g1", month_map, max_per_month=1, is_special=True)
        m2 = grouping.find_optimal_month(start, 1, "g2", month_map, max_per_month=1, is_special=True)
        self.assertEqual(m1, m2)  # special groups all land on start month


class CertificateNumberTests(SimpleTestCase):
    def test_increments_last(self):
        self.assertEqual(grouping.next_certificate_number("BNH-0007"), "BNH-0008")

    def test_empty_starts_at_one(self):
        self.assertEqual(grouping.next_certificate_number(None), "BNH-0001")
        self.assertEqual(grouping.next_certificate_number(""), "BNH-0001")

    def test_unparseable_suffix_resets(self):
        self.assertEqual(grouping.next_certificate_number("BNH-abc"), "BNH-0001")

    def test_custom_prefix(self):
        self.assertEqual(grouping.next_certificate_number("STD-0041", prefix="STD-"), "STD-0042")
