"""Calibration session review -> certificate, end to end (IMPROVEMENT_PLAN.md section 6).

A recorded session waits in pending_review. A calibration-centre technologist
approves it: with HQ reachable it gets a certificate number at once; offline it
is approved pending a certificate that is minted on reconnect. Rejection keeps
the reason. Only reviewers may do any of this, and an approved session produces
a certificate PDF.
"""
import datetime
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from calSchedules.models import CalibrationSchedule
from CalSoft.models import CalibrationProcedure, CalibrationSession, PendingCertificate
from Inventory.models import Department, Equipment, EquipmentDescription
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()

HQ_UP = mock.Mock(status_code=200)


class CalibrationReviewFlowTests(TestCase):
    def setUp(self):
        self.cal_centre = Workshop.objects.create(name="Cal Centre", category="calibration_center")
        maintenance = Workshop.objects.create(name="Biomed", category="maintenance")
        self.department = Department.objects.create(name="ICU", workshop=maintenance)
        self.equipment = Equipment.objects.create(
            description=EquipmentDescription.objects.create(name="Infusion pump"),
            model="P1", serial_number="PUMP-1", department=self.department,
            workshop=maintenance, status="Working",
        )
        self.reviewer = self._user("cal_rev", "Tech", workshop=self.cal_centre, level="Engineer Incharge")
        self.maintenance_tech = self._user("biomed_tech", "Tech", workshop=maintenance, level="Engineer")
        self.nic = self._user("icu_nic", "NIC", department=self.department)

        procedure = CalibrationProcedure.objects.create(name="Flow rate", created_by=self.reviewer)
        self.schedule = CalibrationSchedule.objects.create(
            equipment=self.equipment, workshop=maintenance,
            scheduled_month=datetime.date.today().replace(day=1),
        )
        self.session = CalibrationSession.objects.create(
            procedure=procedure, performed_by=self.reviewer, schedule=self.schedule,
            device_model="P1", device_serial="PUMP-1", Department=self.department,
            status="pending_review", overall_pass=True,
        )

    def _user(self, username, role, **profile):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile,
        })
        return user

    def approve(self, user):
        self.client.force_login(user)
        return self.client.post(reverse("calibration:approve_calibration_session_ajax", args=[self.session.pk]))

    def test_online_approval_issues_a_certificate_number(self):
        with mock.patch("requests.get", return_value=HQ_UP):
            response = self.approve(self.reviewer)
        self.assertEqual(response.status_code, 200, response.content)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "approved")
        self.assertTrue(self.session.certificate_number.startswith("BNH-"))
        self.assertEqual(self.session.approved_by, self.reviewer)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, "completed")

    def test_offline_approval_queues_the_certificate(self):
        with mock.patch("requests.get", side_effect=requests.ConnectionError("offline")):
            response = self.approve(self.reviewer)
        self.assertEqual(response.json()["mode"], "offline")
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "approved_pending_certificate")
        self.assertIsNone(self.session.certificate_number)
        self.assertTrue(PendingCertificate.objects.filter(session=self.session, sync_status="pending").exists())

    def test_only_reviewers_can_approve(self):
        for user in (self.maintenance_tech, self.nic):
            with mock.patch("requests.get", return_value=HQ_UP):
                self.assertEqual(self.approve(user).status_code, 403)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "pending_review")

    def test_rejection_keeps_the_reason(self):
        self.client.force_login(self.reviewer)
        self.client.post(
            reverse("calibration:reject_calibration_session", args=[self.session.pk]),
            {"rejection_reason": "data_quality", "rejection_comments": "Readings drift"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "rejected")
        self.assertEqual(self.session.rejection_reason, "data_quality")
        self.assertEqual(self.session.rejected_by, self.reviewer)

    def test_approved_session_produces_a_certificate_pdf(self):
        with mock.patch("requests.get", return_value=HQ_UP):
            self.approve(self.reviewer)
        response = self.client.get(
            reverse("calibration:generate_comprehensive_certificate", args=[self.session.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
