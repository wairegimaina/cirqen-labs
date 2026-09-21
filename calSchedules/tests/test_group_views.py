"""The group view and the regroup endpoint.

    ./venv/bin/python manage.py test calSchedules.tests.test_group_views \
        --settings=Equiper.test_settings
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from Inventory.models import Department, Equipment, EquipmentDescription
from calSchedules.models import CalibrationSchedule
from workshop.models import Workshop


class GroupViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="viewer", password="pw", email="viewer@example.test"
        )
        cls.workshop = Workshop.objects.create(name="Biomed")
        cls.dept = Department.objects.create(name="Radiology", workshop=cls.workshop)
        cls.desc = EquipmentDescription.objects.create(name="Infusion Pump")
        cls.month = date.today().replace(day=1)

    def setUp(self):
        self.client.force_login(self.user)

    def _equipment(self, serial, active=True):
        return Equipment.objects.create(
            department=self.dept, workshop=self.workshop, description=self.desc,
            model="M", serial_number=serial, status="Working", active_status=active,
        )

    def _schedule(self, equipment, *, status="pending", locked=False, source="manual"):
        return CalibrationSchedule.objects.create(
            equipment=equipment, workshop=self.workshop, scheduled_month=self.month,
            status=status, active_status=True, planning_logic="department",
            calibration_period=12, is_locked=locked, generation_source=source,
        )


class ScheduleGroupsPageTests(GroupViewTestCase):
    def test_the_page_loads(self):
        self._schedule(self._equipment("SN-V1"))
        response = self.client.get(reverse("schedule:schedule_groups"))
        self.assertEqual(response.status_code, 200)

    def test_it_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("schedule:schedule_groups"))
        self.assertIn(response.status_code, (302, 403))

    def test_groups_and_completion_reach_the_template(self):
        self._schedule(self._equipment("SN-V2"), status="completed")
        self._schedule(self._equipment("SN-V3"))

        response = self.client.get(reverse("schedule:schedule_groups"))
        groups = response.context["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["total"], 2)
        self.assertEqual(groups[0]["completed"], 1)
        self.assertEqual(groups[0]["percent"], 50)

    def test_the_month_summary_is_computed(self):
        self._schedule(self._equipment("SN-V4"), status="completed")
        self._schedule(self._equipment("SN-V5"))

        summary = self.client.get(reverse("schedule:schedule_groups")).context["summary"]
        self.assertEqual(summary["devices"], 2)
        self.assertEqual(summary["completed_devices"], 1)
        self.assertEqual(summary["remaining_devices"], 1)
        self.assertEqual(summary["percent"], 50)

    def test_another_month_can_be_requested(self):
        response = self.client.get(reverse("schedule:schedule_groups"), {"month": "2030-07"})
        self.assertEqual(response.context["month"], date(2030, 7, 1))

    def test_an_unparseable_month_falls_back_to_this_one(self):
        """A bad query string should not 500 the page."""
        response = self.client.get(reverse("schedule:schedule_groups"), {"month": "not-a-month"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["month"], self.month)

    def test_the_grouping_logic_can_be_switched(self):
        self._schedule(self._equipment("SN-V6"))
        response = self.client.get(reverse("schedule:schedule_groups"), {"logic": "description"})
        self.assertEqual(response.context["logic"], "description")
        self.assertEqual(response.context["groups"][0]["name"], "Infusion Pump")

    def test_an_empty_month_renders_without_groups(self):
        response = self.client.get(reverse("schedule:schedule_groups"), {"month": "2031-02"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["groups"], [])

    def test_protected_members_are_marked_unmovable(self):
        self._schedule(self._equipment("SN-V7"))
        self._schedule(self._equipment("SN-V8"), locked=True)

        members = self.client.get(reverse("schedule:schedule_groups")).context["groups"][0]["members"]
        blocked = [m for m in members if not m["movable"]]
        self.assertEqual(len(blocked), 1)
        self.assertIn("Locked", blocked[0]["reason"])


class RegroupEndpointTests(GroupViewTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("schedule:regroup_schedules")
        self.target = "2030-09"

    def test_a_schedule_is_moved(self):
        schedule = self._schedule(self._equipment("SN-R1"))
        self.client.post(self.url, {
            "schedule_ids": [str(schedule.id)], "target_month": self.target,
        })
        schedule.refresh_from_db()
        self.assertEqual(schedule.scheduled_month, date(2030, 9, 1))

    def test_a_preview_moves_nothing(self):
        schedule = self._schedule(self._equipment("SN-R2"))
        response = self.client.post(self.url, {
            "schedule_ids": [str(schedule.id)], "target_month": self.target,
            "preview": "1", "format": "json",
        })
        schedule.refresh_from_db()
        self.assertEqual(schedule.scheduled_month, self.month)
        self.assertTrue(response.json()["dry_run"])

    def test_json_reports_what_moved(self):
        schedule = self._schedule(self._equipment("SN-R3"))
        response = self.client.post(self.url, {
            "schedule_ids": [str(schedule.id)], "target_month": self.target,
            "format": "json",
        })
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(len(payload["moved"]), 1)
        self.assertEqual(payload["target_month"], "2030-09-01")

    def test_a_protected_schedule_is_reported_with_its_reason(self):
        schedule = self._schedule(self._equipment("SN-R4"), locked=True)
        payload = self.client.post(self.url, {
            "schedule_ids": [str(schedule.id)], "target_month": self.target,
            "format": "json",
        }).json()

        self.assertEqual(payload["moved"], [])
        self.assertIn("Locked", payload["skipped"][0]["reason"])

    def test_no_selection_is_rejected(self):
        response = self.client.post(self.url, {
            "target_month": self.target, "format": "json",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one", response.json()["message"])

    def test_no_target_month_is_rejected(self):
        schedule = self._schedule(self._equipment("SN-R5"))
        response = self.client.post(self.url, {
            "schedule_ids": [str(schedule.id)], "format": "json",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("month", response.json()["message"].lower())

    def test_get_is_not_allowed(self):
        """Moving schedules changes data, so it must not be a GET."""
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_it_requires_login(self):
        self.client.logout()
        response = self.client.post(self.url, {"target_month": self.target})
        self.assertIn(response.status_code, (302, 403))

    def test_a_whole_group_moves_together(self):
        schedules = [self._schedule(self._equipment(f"SN-G{i}")) for i in range(4)]
        self.client.post(self.url, {
            "schedule_ids": [str(s.id) for s in schedules], "target_month": self.target,
        })
        for s in schedules:
            s.refresh_from_db()
            self.assertEqual(s.scheduled_month, date(2030, 9, 1))
