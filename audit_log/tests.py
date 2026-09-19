"""The audit timeline — both trails, in one view.

This app had no tests. It also had a viewer that showed one of the two audit
trails the system writes: row-level changes from Inventory and the sync agents,
but not the human-readable calibration trail, which was being written from five
places and displayed nowhere.

    ./venv/bin/python manage.py test audit_log --settings=Equiper.test_settings
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from CalSoft.models import CalibrationAuditLog, CalibrationProcedure
from audit_log.models import AuditLog
from audit_log.timeline import (
    SOURCE_CALIBRATION,
    SOURCE_SYSTEM,
    build_timeline,
    counts,
)
from users.models import UserProfile
from workshop.models import Workshop


class AuditTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hod = get_user_model().objects.create_user(
            username="auditor", password="pw", email="a@example.test"
        )
        profile = UserProfile.objects.filter(user=cls.hod).first()
        if profile is None:
            profile = UserProfile(user=cls.hod, role="HOD")
        profile.role = "HOD"
        profile.save()
        cls.procedure = CalibrationProcedure.objects.create(name="NIBP", created_by=cls.hod)

    def _calibration(self, action="approve_session", description="Approved BNH-0001"):
        return CalibrationAuditLog.objects.create(
            user=self.hod, action=action, description=description
        )

    def _system(self, table_name="Inventory_equipment", operation="t"):
        # row_id is required: an audit entry that cannot say which row changed
        # is not an audit entry.
        return AuditLog.objects.create(
            table_name=table_name, operation=operation, row_id=str(uuid.uuid4())
        )


class TimelineTests(AuditTestCase):
    def test_calibration_activity_appears(self):
        """The regression: these records were written and never displayed."""
        self._calibration()
        entries = build_timeline()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].source, SOURCE_CALIBRATION)

    def test_both_trails_are_merged(self):
        self._calibration()
        self._system()
        sources = {e.source for e in build_timeline()}
        self.assertEqual(sources, {SOURCE_CALIBRATION, SOURCE_SYSTEM})

    def test_entries_are_newest_first(self):
        self._calibration(description="older")
        self._calibration(description="newer")
        entries = build_timeline()
        self.assertGreaterEqual(entries[0].when, entries[-1].when)

    def test_the_source_filter_narrows_to_one_trail(self):
        self._calibration()
        self._system()

        self.assertTrue(all(
            e.source == SOURCE_CALIBRATION
            for e in build_timeline(source=SOURCE_CALIBRATION)
        ))
        self.assertTrue(all(
            e.source == SOURCE_SYSTEM
            for e in build_timeline(source=SOURCE_SYSTEM)
        ))

    def test_a_table_filter_excludes_calibration_activity(self):
        """A filter that cannot apply to a trail should not leave it showing.

        Table and operation describe row-level changes. Narrowing by one while
        still listing calibration activity would show records the filter was
        never applied to, which reads as a broken filter.
        """
        self._calibration()
        self._system(table_name="Inventory_equipment")

        entries = build_timeline(table_name="Inventory")
        self.assertTrue(entries)
        self.assertTrue(all(e.source == SOURCE_SYSTEM for e in entries))

    def test_calibration_descriptions_are_searchable(self):
        self._calibration(description="Approved session for Ward A")
        self._calibration(description="Edited NIBP procedure")

        entries = build_timeline(source=SOURCE_CALIBRATION, search="Ward A")
        self.assertEqual(len(entries), 1)
        self.assertIn("Ward A", entries[0].detail)

    def test_the_actor_is_named(self):
        self._calibration()
        self.assertEqual(build_timeline()[0].actor, self.hod.get_username())

    def test_the_action_is_readable(self):
        """`approve_session` is a database value, not something to show a person."""
        self._calibration(action="approve_session")
        self.assertEqual(build_timeline()[0].action, "Approve session")

    def test_a_soft_deleted_calibration_record_is_hidden(self):
        record = self._calibration()
        record.active_status = False
        record.save()
        self.assertEqual(build_timeline(), [])

    def test_counts_cover_both_trails(self):
        self._calibration()
        self._calibration()
        self._system()

        totals = counts()
        self.assertEqual(totals["calibration"], 2)
        self.assertEqual(totals["system"], 1)

    def test_an_empty_system_trail_still_shows_calibration_activity(self):
        """The situation on a fresh install: no transfers yet, plenty of calibration."""
        self._calibration()
        self.assertEqual(counts()["system"], 0)
        self.assertEqual(len(build_timeline()), 1)


class AuditViewTests(AuditTestCase):
    def setUp(self):
        self.client.force_login(self.hod)
        self.url = reverse("audit_log:list")

    def test_the_page_loads(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_calibration_activity_reaches_the_page(self):
        self._calibration(description="Approved session BNH-0001")
        response = self.client.get(self.url)
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertContains(response, "Approved session BNH-0001")

    def test_the_totals_are_shown(self):
        self._calibration()
        self._system()
        totals = self.client.get(self.url).context["totals"]
        self.assertEqual((totals["calibration"], totals["system"]), (1, 1))

    def test_filtering_by_source(self):
        self._calibration()
        self._system()
        response = self.client.get(self.url, {"source": SOURCE_CALIBRATION})
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_it_is_restricted_to_hod(self):
        other = get_user_model().objects.create_user(
            username="tech", password="pw", email="t@example.test"
        )
        # A Tech profile is only valid with a level and a workshop.
        workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        profile = UserProfile.objects.filter(user=other).first() or UserProfile(user=other)
        profile.role = "Tech"
        profile.level = "Engineer"
        profile.workshop = workshop
        profile.save()

        self.client.force_login(other)
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 200)

    def test_it_requires_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 403))
