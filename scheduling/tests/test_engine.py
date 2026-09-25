"""The pure scheduling engine: slots, next due months, placement."""
from datetime import date

from django.test import SimpleTestCase

from scheduling import engine
from scheduling.engine import NEXT_SLOT, WITHIN_INTERVAL, place


def d(year, month):
    return date(year, month, 1)


class SlotTests(SimpleTestCase):
    def test_slot_of(self):
        self.assertEqual(engine.slot_of(2, 6), (2, 8))
        self.assertEqual(engine.slot_of(1, 3), (1, 4, 7, 10))
        self.assertEqual(engine.slot_of(3, 12), (3,))
        self.assertEqual(engine.slot_of(3, 24), (3,))
        self.assertEqual(engine.slot_of(10, 9), (1, 4, 7, 10))  # 9 months cycles a quarter class

    def test_slots_must_fit_wholly_inside_the_months(self):
        self.assertEqual(engine.slots(6, [2, 8]), [(2, 8)])
        self.assertEqual(engine.slots(12, [2, 8]), [(2,), (8,)])
        self.assertEqual(engine.slots(3, [1, 7]), [])
        self.assertEqual(engine.slots(6, [2, 3, 8]), [(2, 8)])


class NextDueTests(SimpleTestCase):
    def test_six_months_alternates(self):
        month = d(2027, 2)
        seen = []
        for _ in range(4):
            month = engine.next_due(month, 6, (2, 8))
            seen.append(month)
        self.assertEqual(seen, [d(2027, 8), d(2028, 2), d(2028, 8), d(2029, 2)])

    def test_twelve_months_keeps_the_month(self):
        month, seen = d(2027, 3), []
        for _ in range(3):
            month = engine.next_due(month, 12, (3,))
            seen.append(month)
        self.assertEqual(seen, [d(2028, 3), d(2029, 3), d(2030, 3)])

    def test_three_months_cycles_quarters(self):
        month, seen = d(2027, 1), []
        for _ in range(5):
            month = engine.next_due(month, 3, (1, 4, 7, 10))
            seen.append(month.month)
        self.assertEqual(seen, [4, 7, 10, 1, 4])

    def test_on_time_is_the_same_under_both_policies(self):
        for policy in (NEXT_SLOT, WITHIN_INTERVAL):
            self.assertEqual(engine.next_due(d(2027, 2), 6, (2, 8), d(2027, 2), policy), d(2027, 8))

    def test_early_completion_does_not_move_the_cycle(self):
        self.assertEqual(engine.next_due(d(2027, 3), 12, (3,), d(2027, 1), NEXT_SLOT), d(2028, 3))

    def test_late_ppm_takes_the_next_slot_after_the_work(self):
        # Due Feb, done in September: August has passed, so February.
        self.assertEqual(engine.next_due(d(2027, 2), 6, (2, 8), d(2027, 9), NEXT_SLOT), d(2028, 2))

    def test_late_calibration_never_exceeds_the_interval(self):
        # Due Feb, done April: a certificate from April runs to October, so
        # August (not February next year).
        self.assertEqual(engine.next_due(d(2027, 2), 6, (2, 8), d(2027, 4), WITHIN_INTERVAL), d(2027, 8))
        # 12-month March device done in May: next March, within 12 months.
        self.assertEqual(engine.next_due(d(2027, 3), 12, (3,), d(2027, 5), WITHIN_INTERVAL), d(2028, 3))

    def test_next_due_is_always_after_the_work(self):
        self.assertEqual(engine.next_due(d(2026, 1), 3, (1, 4, 7, 10), d(2026, 6), NEXT_SLOT), d(2026, 7))


class PlaceTests(SimpleTestCase):
    common = {"group_name": "Patient Monitor", "start": d(2026, 10)}

    def test_first_visit_uses_the_next_group_month(self):
        # September 2026 now, start = October: next of Feb/Aug is February 2027.
        p = place(months=[2, 8], interval=6, **self.common)
        self.assertEqual(p.month, d(2027, 2))
        self.assertIn("first visit", p.message)

    def test_continues_from_history(self):
        p = place(months=[2, 8], interval=6, last=(d(2027, 2), d(2027, 2)), **self.common)
        self.assertEqual(p.month, d(2027, 8))
        self.assertEqual(p.facts["previous_due"], "Feb 2027")

    def test_history_in_a_month_the_group_no_longer_uses(self):
        # Was on Feb/Aug; the group is now Mar/Sep.
        p = place(months=[3, 9], interval=6, last=(d(2027, 2), d(2027, 2)), **self.common)
        self.assertEqual(p.month, d(2027, 9))
        self.assertIn("no longer", p.message)

    def test_balances_new_devices_across_slots_deterministically(self):
        load = {}
        months = [place(months=[2, 8], interval=12, load=load, **self.common).month for _ in range(5)]
        self.assertEqual(months, [d(2027, 2), d(2027, 8), d(2027, 2), d(2027, 8), d(2027, 2)])
        self.assertEqual(load, {(2,): 3, (8,): 2})

    def test_never_outside_the_group_months(self):
        load = {(2,): 500}
        p = place(months=[2, 8], interval=6, load=load, **self.common)
        self.assertIn(p.month.month, (2, 8))

    def test_problems_are_named(self):
        self.assertEqual(place(months=[], interval=6, **self.common).problem, "no_months")
        self.assertEqual(place(months=[2], interval=None, **self.common).problem, "no_interval")
        p = place(months=[1, 7], interval=3, **self.common)
        self.assertEqual(p.problem, "interval_does_not_fit")
        self.assertIn("needs 4", p.message)
