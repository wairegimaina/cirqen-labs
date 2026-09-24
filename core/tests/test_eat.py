"""Display times are East Africa Time, whatever zone the value arrived in."""
import datetime as dt

from django.conf import settings
from django.test import SimpleTestCase

from core.eat import EAT, fmt_eat, to_eat

# 21:30 UTC is 00:30 the next day in Nairobi: the case that exposed UTC dates.
LATE_UTC = dt.datetime(2026, 9, 23, 21, 30, tzinfo=dt.timezone.utc)


class EatHelperTests(SimpleTestCase):
    def test_utc_timestamp_is_shown_in_eat(self):
        self.assertEqual(fmt_eat(LATE_UTC), "2026-09-24 00:30")

    def test_naive_datetime_is_taken_as_eat(self):
        self.assertEqual(to_eat(dt.datetime(2026, 9, 23, 9, 0)).tzinfo, EAT)
        self.assertEqual(fmt_eat(dt.datetime(2026, 9, 23, 9, 0)), "2026-09-23 09:00")

    def test_dates_pass_through(self):
        self.assertEqual(fmt_eat(dt.date(2026, 9, 1), "%B %Y"), "September 2026")

    def test_empty_uses_default(self):
        self.assertEqual(fmt_eat(None), "N/A")
        self.assertEqual(fmt_eat(None, default="Unknown"), "Unknown")

    def test_celery_schedules_run_on_nairobi_time(self):
        self.assertEqual(settings.CELERY_TIMEZONE, "Africa/Nairobi")
