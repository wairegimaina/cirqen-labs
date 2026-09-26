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


class RealignTests(SimpleTestCase):
    """A device whose old month is not in the new plan goes to the nearest month."""
    start = d(2026, 10)  # "today" is September 2026

    def test_twelve_month_device_moves_to_the_nearest_slot_not_a_year_later(self):
        # Done August 2026, due August 2027; the group is now May/November.
        p = place(group_name="Defibrillator", months=[5, 11], interval=12,
                  last=(d(2026, 8), d(2026, 8)), start=self.start)
        self.assertEqual(p.month, d(2027, 5))
        self.assertIn("nearest", p.message)

    def test_never_moved_into_the_past(self):
        # Due October 2026; nearest Feb/Aug month is August 2026, already gone.
        p = place(group_name="Patient Monitor", months=[2, 8], interval=6,
                  last=(d(2026, 4), d(2026, 4)), start=self.start)
        self.assertEqual(p.month, d(2027, 2))

    def test_calibration_realign_stays_within_the_interval(self):
        p = place(group_name="Analyser", months=[6], interval=12, last=(d(2026, 9), d(2026, 9)),
                  start=self.start, policy=WITHIN_INTERVAL)
        self.assertEqual(p.month, d(2027, 6))


class SpreadLayoutTests(SimpleTestCase):
    def test_twelve_month_groups_fill_the_year_evenly(self):
        groups = [(f"g{i}", 10, 12) for i in range(12)]
        layout = engine.spread_layout(groups)
        self.assertEqual(sorted(m for months in layout.values() for m in months), list(range(1, 13)))

    def test_a_heavy_group_gets_more_than_one_month(self):
        # 95 devices a year: about 8 a month. 40 of one kind can't all go in one.
        groups = [("big", 40, 12)] + [(f"s{i}", 5, 12) for i in range(11)]
        layout = engine.spread_layout(groups)
        self.assertEqual(len(layout["big"]), 6)
        self.assertTrue(all(len(layout[f"s{i}"]) == 1 for i in range(11)))

    def test_months_always_fit_the_interval(self):
        layout = engine.spread_layout([("q", 8, 3), ("h", 8, 6), ("t", 8, 4), ("y", 8, 12)])
        for key, interval in (("q", 3), ("h", 6), ("t", 4), ("y", 12)):
            self.assertTrue(engine.slots(interval, layout[key]), (key, layout[key]))

    def test_deterministic(self):
        groups = [("a", 7, 6), ("b", 7, 6), ("c", 3, 3), ("d", 11, 12)]
        self.assertEqual(engine.spread_layout(groups), engine.spread_layout(list(reversed(groups))))

    def test_the_busiest_month_stays_close_to_the_average(self):
        groups = [(f"g{i}", n, 12) for i, n in enumerate([19, 16, 14, 14, 12, 12, 10, 10, 9, 9, 9, 8,
                                                          8, 8, 7, 6, 6, 6, 6, 4, 4, 2])]
        layout = engine.spread_layout(groups)
        per_month = {m: 0.0 for m in range(1, 13)}
        for key, n, _ in groups:
            for m in layout[key]:
                per_month[m] += n / len(layout[key])
        self.assertLess(max(per_month.values()), 1.35 * sum(per_month.values()) / 12)
