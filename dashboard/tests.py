"""HOD dashboard — workshop statistics and the department drill-down.

This app had no tests. The HOD dashboard is the first screen a head of
department sees and it now aggregates across calibration, PPM and inventory, so
a wrong number here is a wrong decision about where to send people.

    ./venv/bin/python manage.py test dashboard --settings=Equiper.test_settings
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from CalSoft.models import CalibrationProcedure, CalibrationSession
from Inventory.models import Department, Equipment, EquipmentDescription
from ppms.models import PPMSchedule
from users.models import UserProfile
from workshop.models import Workshop


class HodDashboardTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hod = get_user_model().objects.create_user(
            username="hod", password="pw", email="hod@example.test"
        )
        profile = UserProfile.objects.filter(user=cls.hod).first()
        if profile is None:
            profile = UserProfile(user=cls.hod, role="HOD")
        profile.role = "HOD"
        profile.save()

        cls.workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        cls.radiology = Department.objects.create(name="Radiology", workshop=cls.workshop)
        cls.theatre = Department.objects.create(name="Theatre", workshop=cls.workshop)
        cls.description = EquipmentDescription.objects.create(name="Infusion Pump")
        cls.procedure = CalibrationProcedure.objects.create(name="NIBP", created_by=cls.hod)

    def setUp(self):
        self.client.force_login(self.hod)

    def _equipment(self, serial, department):
        return Equipment.objects.create(
            department=department, workshop=self.workshop, description=self.description,
            model="M", serial_number=serial, status="Working", active_status=True,
        )

    def _session(self, equipment, *, status="approved", certificate="BNH-0001"):
        return CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.hod,
            device_serial=equipment.serial_number, status=status,
            certificate_number=certificate, overall_pass=True,
        )

    def _stats(self):
        response = self.client.get(reverse("dashboard:hod_dashboard"))
        self.assertEqual(response.status_code, 200)
        return response.context["workshops"][0].stats


class WorkshopStatsTests(HodDashboardTestCase):
    def test_the_dashboard_loads(self):
        response = self.client.get(reverse("dashboard:hod_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_equipment_is_counted(self):
        self._equipment("SN-1", self.radiology)
        self._equipment("SN-2", self.theatre)
        self.assertEqual(self._stats()["equipment_count"], 2)

    def test_certificates_are_counted_where_the_equipment_lives(self):
        """A certificate belongs to a device in a ward, not to the issuing centre."""
        self._session(self._equipment("SN-3", self.radiology), certificate="BNH-0003")
        stats = self._stats()
        self.assertEqual(stats["certificates_count"], 1)
        self.assertEqual(stats["certified_equipment_count"], 1)

    def test_a_session_without_a_number_is_not_counted_as_certified(self):
        """Approved-pending-certificate is not the same as certified."""
        equipment = self._equipment("SN-4", self.radiology)
        self._session(equipment, status="approved_pending_certificate", certificate=None)

        stats = self._stats()
        self.assertEqual(stats["certificates_count"], 0)
        self.assertEqual(stats["awaiting_certificate_count"], 1)

    def test_several_sessions_on_one_device_count_once_as_certified_equipment(self):
        """Recalibrating a device does not make it two certified devices."""
        equipment = self._equipment("SN-5", self.radiology)
        self._session(equipment, certificate="BNH-0005")
        self._session(equipment, certificate="BNH-0006")

        stats = self._stats()
        self.assertEqual(stats["certificates_count"], 2)
        self.assertEqual(stats["certified_equipment_count"], 1)

    def test_another_workshops_equipment_is_not_counted(self):
        other = Workshop.objects.create(name="Other", category="maintenance")
        other_dept = Department.objects.create(name="Elsewhere", workshop=other)
        self._session(self._equipment("SN-6", other_dept), certificate="BNH-0007")
        self._equipment("SN-7", self.radiology)

        stats = self._stats()
        self.assertEqual(stats["equipment_count"], 1)
        self.assertEqual(stats["certificates_count"], 0)

    def test_ppm_state_is_split_into_done_and_outstanding(self):
        equipment = self._equipment("SN-8", self.radiology)
        PPMSchedule.objects.filter(equipment=equipment).delete()
        month = date.today().replace(day=1)
        PPMSchedule.objects.create(equipment=equipment, workshop=self.workshop,
                                   scheduled_month=month, status="completed")
        PPMSchedule.objects.create(equipment=equipment, workshop=self.workshop,
                                   scheduled_month=date(month.year + 1, month.month, 1),
                                   status="pending")

        stats = self._stats()
        self.assertGreaterEqual(stats["ppms_completed_count"], 1)
        self.assertGreaterEqual(stats["ppms_pending_count"], 1)


class DepartmentBreakdownTests(HodDashboardTestCase):
    """A workshop total says whether we are covered; this says which ward is behind."""

    def test_each_department_appears(self):
        self._equipment("SN-D1", self.radiology)
        self._equipment("SN-D2", self.theatre)

        names = {d["name"] for d in self._stats()["departments"]}
        self.assertEqual(names, {"Radiology", "Theatre"})

    def test_coverage_is_computed_per_department(self):
        self._session(self._equipment("SN-D3", self.radiology), certificate="BNH-0010")
        self._equipment("SN-D4", self.theatre)

        by_name = {d["name"]: d for d in self._stats()["departments"]}
        self.assertEqual(by_name["Radiology"]["percent"], 100)
        self.assertEqual(by_name["Radiology"]["certified_count"], 1)
        self.assertEqual(by_name["Theatre"]["percent"], 0)
        self.assertEqual(by_name["Theatre"]["uncertified_count"], 1)

    def test_the_least_covered_department_is_listed_first(self):
        """That is where the work is, so it should not need hunting for."""
        self._session(self._equipment("SN-D5", self.radiology), certificate="BNH-0011")
        self._equipment("SN-D6", self.theatre)

        departments = self._stats()["departments"]
        self.assertEqual(departments[0]["name"], "Theatre")
        self.assertEqual(departments[0]["percent"], 0)

    def test_awaiting_certificates_are_reported_per_department(self):
        equipment = self._equipment("SN-D7", self.theatre)
        self._session(equipment, status="approved_pending_certificate", certificate=None)

        by_name = {d["name"]: d for d in self._stats()["departments"]}
        self.assertEqual(by_name["Theatre"]["awaiting_count"], 1)

    def test_a_department_with_no_equipment_is_omitted(self):
        """An empty department is not a coverage problem."""
        self._equipment("SN-D8", self.radiology)
        names = {d["name"] for d in self._stats()["departments"]}
        self.assertNotIn("Theatre", names)

    def test_the_department_totals_agree_with_the_workshop_total(self):
        """The drill-down must reconcile with the number above it."""
        self._session(self._equipment("SN-D9", self.radiology), certificate="BNH-0012")
        self._equipment("SN-D10", self.radiology)
        self._equipment("SN-D11", self.theatre)

        stats = self._stats()
        self.assertEqual(
            sum(d["equipment_count"] for d in stats["departments"]),
            stats["equipment_count"],
        )
        self.assertEqual(
            sum(d["certified_count"] for d in stats["departments"]),
            stats["certified_equipment_count"],
        )


class DashboardAccessTests(HodDashboardTestCase):
    def test_it_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard:hod_dashboard"))
        self.assertIn(response.status_code, (302, 403))
