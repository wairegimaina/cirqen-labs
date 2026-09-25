"""Test support: data from before the one-open-schedule index existed."""
from django.db import connection

ONE_OPEN_INDEXES = ("ppms_one_open_schedule", "calschedules_one_open_schedule")


class LegacyDuplicatesAllowed:
    """Drop the one-open indexes for the test (rolled back with its transaction).

    For tests of the tools that clean up duplicates made before the index
    existed; such rows cannot be created once it is in place.
    """

    def setUp(self):
        with connection.cursor() as cur:
            for name in ONE_OPEN_INDEXES:
                cur.execute(f'DROP INDEX IF EXISTS "{name}"')
        super().setUp()
