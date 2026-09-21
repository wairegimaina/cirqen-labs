"""Due dates fall at the end of the month — no database.

A calibration is due within a month, not on a particular day of it. Anchoring
the due date to the same day as the last calibration made the schedule brittle:
a device calibrated on the 3rd counted as overdue from the 4th, even though the
workshop had the rest of the month to reach it.

    ./venv/bin/python manage.py test calSchedules.tests.test_due_dates \
        --settings=Equiper.test_settings
"""

from datetime import date, datetime

from django.test import SimpleTestCase

from calSchedules.grouping import (
    days_until_due,
    is_overdue,
    month_end,
    next_due_date,
)


class MonthEndTests(SimpleTestCase):
    def test_a_31_day_month(self):
        self.assertEqual(month_end(date(2026, 1, 9)), date(2026, 1, 31))

    def test_a_30_day_month(self):
        self.assertEqual(month_end(date(2026, 4, 1)), date(2026, 4, 30))

    def test_february_in_a_common_year(self):
        self.assertEqual(month_end(date(2026, 2, 5)), date(2026, 2, 28))

    def test_february_in_a_leap_year(self):
        self.assertEqual(month_end(date(2028, 2, 5)), date(2028, 2, 29))

    def test_a_date_already_at_the_month_end_is_unchanged(self):
        self.assertEqual(month_end(date(2026, 1, 31)), date(2026, 1, 31))

    def test_a_datetime_is_accepted_and_returns_a_date(self):
        result = month_end(datetime(2026, 3, 14, 9, 30))
        self.assertEqual(result, date(2026, 3, 31))
        self.assertNotIsInstance(result, datetime)

    def test_none_passes_through(self):
        self.assertIsNone(month_end(None))


class NextDueDateTests(SimpleTestCase):
    def test_twelve_months_lands_on_the_month_end(self):
        """Calibrated 3 March 2026 -> due any time in March 2027."""
        self.assertEqual(next_due_date(date(2026, 3, 3), 12), date(2027, 3, 31))

    def test_the_day_of_the_month_does_not_matter(self):
        """Every calibration in a month gets the same due date."""
        first = next_due_date(date(2026, 3, 1), 12)
        last = next_due_date(date(2026, 3, 31), 12)
        self.assertEqual(first, last)
        self.assertEqual(first, date(2027, 3, 31))

    def test_a_six_month_interval(self):
        self.assertEqual(next_due_date(date(2026, 11, 20), 6), date(2027, 5, 31))

    def test_a_three_month_interval(self):
        self.assertEqual(next_due_date(date(2026, 1, 15), 3), date(2026, 4, 30))

    def test_the_anniversary_is_stable_across_a_leap_year(self):
        """`timedelta(days=365)` would land a day early here."""
        self.assertEqual(next_due_date(date(2027, 2, 15), 12), date(2028, 2, 29))

    def test_a_month_end_anniversary_does_not_slip(self):
        """31 January + 12 months is January, not 30 December."""
        self.assertEqual(next_due_date(date(2026, 1, 31), 12), date(2027, 1, 31))

    def test_calendar_months_do_not_drift(self):
        """The regression this replaces.

        `today + timedelta(days=30 * months)` treats a year as 360 days, so a
        yearly calibration slides five days earlier each cycle and drops into
        the previous month after about six years. Calendar months hold.
        """
        current = date(2026, 6, 10)
        for _ in range(6):
            current = next_due_date(current, 12)
        self.assertEqual(current.month, 6, "six yearly cycles must stay in June")
        self.assertEqual(current, date(2032, 6, 30))

    def test_the_interval_defaults_to_twelve_months(self):
        self.assertEqual(next_due_date(date(2026, 3, 3)), date(2027, 3, 31))

    def test_a_missing_interval_is_treated_as_twelve(self):
        self.assertEqual(next_due_date(date(2026, 3, 3), None), date(2027, 3, 31))

    def test_a_datetime_is_accepted(self):
        self.assertEqual(next_due_date(datetime(2026, 3, 3, 14, 0), 12), date(2027, 3, 31))

    def test_none_passes_through(self):
        self.assertIsNone(next_due_date(None, 12))


class OverdueTests(SimpleTestCase):
    """The point of the change: a device is not overdue mid-month."""

    def test_not_overdue_earlier_in_the_due_month(self):
        due = next_due_date(date(2026, 3, 3), 12)      # 31 March 2027
        self.assertFalse(is_overdue(due, date(2027, 3, 1)))
        self.assertFalse(is_overdue(due, date(2027, 3, 15)))

    def test_not_overdue_on_the_last_day(self):
        due = next_due_date(date(2026, 3, 3), 12)
        self.assertFalse(is_overdue(due, date(2027, 3, 31)))

    def test_overdue_the_day_after(self):
        due = next_due_date(date(2026, 3, 3), 12)
        self.assertTrue(is_overdue(due, date(2027, 4, 1)))

    def test_the_old_behaviour_would_have_been_overdue_28_days_earlier(self):
        """A device calibrated on the 3rd used to be overdue from the 4th."""
        calibrated = date(2026, 3, 3)
        old_style_due = date(2027, 3, 3)
        new_due = next_due_date(calibrated, 12)

        mid_month = date(2027, 3, 20)
        self.assertTrue(old_style_due < mid_month, "old rule: already overdue")
        self.assertFalse(is_overdue(new_due, mid_month), "new rule: still in its month")

    def test_a_missing_due_date_is_not_overdue(self):
        self.assertFalse(is_overdue(None))


class DaysUntilDueTests(SimpleTestCase):
    def test_counts_forward_to_the_month_end(self):
        due = date(2027, 3, 31)
        self.assertEqual(days_until_due(due, date(2027, 3, 1)), 30)

    def test_zero_on_the_due_day(self):
        self.assertEqual(days_until_due(date(2027, 3, 31), date(2027, 3, 31)), 0)

    def test_negative_once_overdue(self):
        self.assertEqual(days_until_due(date(2027, 3, 31), date(2027, 4, 10)), -10)

    def test_none_passes_through(self):
        self.assertIsNone(days_until_due(None))
