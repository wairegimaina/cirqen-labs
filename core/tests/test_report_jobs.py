"""Large reports are built in the background and kept for 15 minutes."""
import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core import report_jobs
from Inventory.models import Department, Equipment, EquipmentDescription
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


def _run_now(name, params, user_id, key):
    report_jobs.build(name, params, user_id, key)


class ReportJobTests(TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)
        override = override_settings(DATA_PATH=self.data)
        override.enable()
        self.addCleanup(override.disable)
        workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        ward = Department.objects.create(name="ICU", workshop=workshop)
        for n in range(3):
            Equipment.objects.create(description=EquipmentDescription.objects.create(name=f"Type {n}"),
                                     model="M", serial_number=f"SN-{n}", department=ward, workshop=workshop,
                                     status="Working")
        self.hod = self._user("rep_hod", "HOD")
        self.other = self._user("rep_hod2", "HOD")
        patcher = mock.patch("core.tasks.build_report.delay", side_effect=_run_now)
        self.delay = patcher.start()
        self.addCleanup(patcher.stop)

    def _user(self, username, role):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True})
        return user

    def test_report_is_built_and_downloaded_by_its_owner(self):
        self.client.force_login(self.hod)
        started = self.client.get(reverse("core:report_start", args=["equipment_category"])).json()
        self.assertEqual(started["status"], "ready")
        download = self.client.get(reverse("core:report_download", args=[started["key"]]))
        self.assertEqual(download.status_code, 200)
        self.assertTrue(b"".join(download.streaming_content).startswith(b"%PDF"))

    def test_a_repeat_request_reuses_the_cached_report(self):
        self.client.force_login(self.hod)
        url = reverse("core:report_start", args=["equipment_category"])
        first = self.client.get(url).json()
        second = self.client.get(url).json()
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(self.delay.call_count, 1)

    def test_another_user_cannot_fetch_the_report(self):
        self.client.force_login(self.hod)
        key = self.client.get(reverse("core:report_start", args=["equipment_category"])).json()["key"]
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("core:report_download", args=[key])).status_code, 404)
        self.assertEqual(self.client.get(reverse("core:report_status", args=[key])).json()["status"], "missing")

    def test_a_down_queue_builds_in_the_request(self):
        self.delay.side_effect = ConnectionError("broker down")
        self.client.force_login(self.hod)
        started = self.client.get(reverse("core:report_start", args=["equipment_category"])).json()
        self.assertEqual(started["status"], "ready")

    def test_unknown_reports_are_refused(self):
        self.client.force_login(self.hod)
        self.assertEqual(self.client.get(reverse("core:report_start", args=["everything"])).status_code, 404)
