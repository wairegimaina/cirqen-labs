"""Email notifications: the copy rule, work order events, digest, and sending."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from CalSoft.models import CalibrationNotification
from Inventory.models import Department, Equipment, EquipmentDescription
from jobcard.models import jobcard
from jobcard.tests import SIGNATURE
from notifications import digest
from notifications.mailer import queue, send_pending
from notifications.models import EmailOutbox
from notifications.recipients import addresses, cc_for
from notifications.tasks import send_daily_digests
from ppms.models import PPMSchedule
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()

EMAIL = dict(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_HOST_USER="cirqen@hospital.test")


class Base(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        self.department = Department.objects.create(name="ICU", workshop=self.workshop)
        self.equipment = Equipment.objects.create(
            description=EquipmentDescription.objects.create(name="Ventilator"), model="V1",
            serial_number="VENT-1", department=self.department, status="Working")
        self.hod = self._user("n_hod", "HOD")
        self.deputy = self._user("n_deputy", "Tech", workshop=self.workshop, level="Engineer Incharge",
                                 is_deputy_hod=True)
        self.tech = self._user("n_tech", "Tech", workshop=self.workshop, level="Engineer")
        self.nic = self._user("n_nic", "NIC", department=self.department)

    def _user(self, username, role, email=True, **profile):
        user = User.objects.create_user(username=username, password="pw12345!",
                                        email=f"{username}@hospital.test" if email else "",
                                        first_name=username.split('_')[1].title())
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile})
        return user


class CopyRuleTests(Base):
    def test_staff_mail_copies_the_hod(self):
        self.assertEqual(cc_for(self.tech), [self.hod])
        self.assertEqual(cc_for(self.nic), [self.hod])

    def test_hod_mail_copies_the_deputy(self):
        self.assertEqual(cc_for(self.hod), [self.deputy])

    def test_nobody_is_both_recipient_and_copy(self):
        to, cc = addresses([self.tech, self.hod])
        self.assertEqual(to, ["n_tech@hospital.test", "n_hod@hospital.test"])
        self.assertEqual(cc, ["n_deputy@hospital.test"])

    def test_users_without_email_are_skipped(self):
        self.hod.email = ""
        self.hod.save()
        self.assertEqual(addresses([self.tech]), (["n_tech@hospital.test"], []))


@override_settings(**EMAIL)
class WorkOrderEventTests(Base):
    def _raise(self):
        self.client.force_login(self.tech)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("jobcard:create_job_card"), {
                "priority_level": "High", "department": self.department.id, "equipment": self.equipment.id,
                "job_description": "Alarm failing", "action_taken": "Repair", "time_started": "09:00",
                "signature_data": SIGNATURE, "spare_parts_data": "[]", "labor_cost": "0", "additional_costs": "0"})
        return jobcard.objects.get(equipment=self.equipment)

    def test_submitted_work_order_mails_the_in_charge_copying_the_hod(self):
        self._raise()
        msg = EmailOutbox.objects.get(kind="work_order_submitted")
        self.assertEqual(msg.to_list(), ["n_nic@hospital.test"])
        self.assertEqual(msg.cc_list(), ["n_hod@hospital.test"])
        self.assertIn("VENT-1", msg.body_text)
        self.assertTrue(CalibrationNotification.objects.filter(recipient=self.nic).exists())

    def test_decision_mails_the_technician(self):
        card = self._raise()
        self.client.force_login(self.nic)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("jobcard:create_job_card"), {
                "jobcard_id": card.id, "nurse_name": "Nic", "nurse_signature_data": SIGNATURE,
                "decline": "1", "decline_reason": "Still alarming"})
        msg = EmailOutbox.objects.get(kind="work_order_decided")
        self.assertEqual(msg.to_list(), ["n_tech@hospital.test"])
        self.assertEqual(msg.cc_list(), ["n_hod@hospital.test"])
        self.assertIn("Still alarming", msg.body_text)

    def test_queue_is_idempotent(self):
        queue("x", "same-key", [self.tech], "S", "work_order_decided", {"wo": None})
        queue("x", "same-key", [self.tech], "S", "work_order_decided", {"wo": None})
        self.assertEqual(EmailOutbox.objects.filter(dedupe_key="same-key").count(), 1)


@override_settings(**EMAIL)
class SendingTests(Base):
    def test_sends_with_cc(self):
        queue("t", "k1", [self.hod], "Hello", "work_order_decided", {"wo": None})
        self.assertEqual(send_pending(), (1, 0))
        self.assertEqual(mail.outbox[0].to, ["n_hod@hospital.test"])
        self.assertEqual(mail.outbox[0].cc, ["n_deputy@hospital.test"])
        self.assertEqual(EmailOutbox.objects.get(dedupe_key="k1").status, "sent")

    def test_offline_backs_off_and_keeps_the_message(self):
        queue("t", "k2", [self.hod], "Hello", "work_order_decided", {"wo": None})
        with mock.patch("django.core.mail.backends.locmem.EmailBackend.open", side_effect=OSError("offline")):
            self.assertEqual(send_pending(), (0, 1))
        msg = EmailOutbox.objects.get(dedupe_key="k2")
        self.assertEqual((msg.status, msg.attempts), ("pending", 1))
        self.assertIsNotNone(msg.next_attempt_at)
        self.assertEqual(send_pending(), (0, 0))  # not due yet

    @override_settings(EMAIL_HOST_USER="")
    def test_nothing_sent_without_smtp_account(self):
        queue("t", "k3", [self.hod], "Hello", "work_order_decided", {"wo": None})
        self.assertEqual(send_pending(), (0, 0))


@override_settings(**EMAIL)
class DigestTests(Base):
    def setUp(self):
        super().setUp()
        self.today = datetime.date(2026, 9, 24)
        # One open schedule per equipment: replace the one the new-equipment signal made.
        PPMSchedule.objects.filter(equipment=self.equipment).delete()
        PPMSchedule.objects.create(equipment=self.equipment, workshop=self.workshop,
                                   scheduled_month=datetime.date(2026, 8, 1))

    def test_tech_digest_lists_overdue_ppm(self):
        sections = digest.build(self.tech, self.today)
        ppm = next(s for s in sections if s["title"].startswith("PPMs"))
        self.assertIn("OVERDUE Aug 2026", ppm["items"][0])

    def test_tech_digest_lists_expiring_warranties(self):
        from Inventory.models import Supplier, Warranty
        supplier = Supplier.objects.create(name="MedEquip", phone="0700 111")
        Warranty.objects.create(equipment=self.equipment, supplier=supplier, start_date=datetime.date(2024, 10, 10),
                                expiry_date=datetime.date(2026, 10, 10))
        sections = digest.build(self.tech, self.today)
        warranties = next(s for s in sections if s["title"].startswith("Warranties expiring"))
        self.assertIn("10 Oct 2026", warranties["items"][0])
        self.assertIn("MedEquip (0700 111)", warranties["items"][0])

    def test_in_charge_digest_lists_waiting_approvals(self):
        jobcard.objects.create(department=self.department, equipment=self.equipment, workshop=self.workshop,
                               priority_level="Low", action_taken="Repair", job_description="x",
                               performed_by=self.tech)
        titles = [s["title"] for s in digest.build(self.nic, self.today)]
        self.assertIn("Work orders waiting for your approval", titles)

    def test_hod_digest_summarises_workshops(self):
        sections = digest.build(self.hod, self.today)
        self.assertIn("Biomed: 1 overdue PPM", sections[0]["items"][0])

    @override_settings(NOTIFICATIONS_DIGEST_SENDER=False)
    def test_only_the_designated_machine_sends(self):
        self.assertEqual(send_daily_digests(), 0)

    @override_settings(NOTIFICATIONS_DIGEST_SENDER=True, NOTIFICATIONS_DIGEST_HOUR=0)
    def test_one_digest_per_user_per_day_with_copy_rule(self):
        send_daily_digests()
        send_daily_digests()
        digests = EmailOutbox.objects.filter(kind="daily_digest")
        hod_mail = digests.get(to="n_hod@hospital.test")
        self.assertEqual(hod_mail.cc_list(), ["n_deputy@hospital.test"])
        self.assertEqual(digests.filter(to="n_tech@hospital.test").count(), 1)
