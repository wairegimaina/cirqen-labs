"""Phase 1: accessory requests email the HOD when made and when received, and
the requester when the HOD decides; each step once."""
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from CalSoft.models import CalibrationNotification
from Inventory.models import EquipmentDescription
from notifications.mailer import send_pending
from notifications.models import EmailOutbox
from parts_tools.models import Accessories, Accessoriesname, AccessoryRequest

from .tests import EMAIL, Base


@override_settings(**EMAIL, SITE_URL="https://cirqen.hospital.test")
class AccessoryRequestMailTests(Base):
    def setUp(self):
        super().setUp()
        self.vent = EquipmentDescription.objects.get(name="Ventilator")

    def _request(self, **extra):
        self.client.force_login(self.tech)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("partstools:request_accessory"), {
                "request_type": "new", "equipment_description": self.vent.pk, "accessory_name": "Flow sensor",
                "manufacturer_name": "Draeger", "requested_quantity": 4, "note": "Two broken", **extra})
        return AccessoryRequest.objects.get(accessory_name="Flow sensor")

    def _decide(self, req, action="approve", **extra):
        self.client.force_login(self.hod)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("partstools:approve_accessory_request", args=[req.pk]), {
                "action": action, "approved_quantity": 4, "approved_unit_cost": "2500", **extra})

    def _receive(self, req):
        self.client.force_login(self.tech)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("partstools:accept_accessory_request", args=[req.pk]))

    def test_a_request_emails_the_hod_copying_the_deputy(self):
        req = self._request()
        msg = EmailOutbox.objects.get(kind="accessory_requested")
        self.assertEqual((msg.to, msg.cc), ("n_hod@hospital.test", "n_deputy@hospital.test"))
        self.assertIn("4 × Flow sensor", msg.subject)
        self.assertIn("Two broken", msg.body_text)
        self.assertIn("https://cirqen.hospital.test/", msg.body_text)
        self.assertTrue(CalibrationNotification.objects.filter(recipient=self.hod,
                                                               notification_type="accessory_requested").exists())
        self.assertEqual(req.status, "Pending")

    def test_approval_emails_the_requester_not_the_hod_who_approved(self):
        req = self._request()
        self._decide(req, approval_reason="Order from MedEquip")
        msg = EmailOutbox.objects.get(kind="accessory_decided")
        self.assertEqual(msg.to, "n_tech@hospital.test")
        self.assertNotIn("n_hod@", msg.cc)  # the HOD decided it; not copied on their own decision
        self.assertIn("was approved", msg.subject)
        self.assertIn("KSh 10000", msg.body_text)
        self.assertIn("Order from MedEquip", msg.body_text)

    def test_a_decline_gives_the_reason(self):
        req = self._request()
        self._decide(req, action="decline", approval_reason="Use the spare in store")
        msg = EmailOutbox.objects.get(kind="accessory_decided")
        self.assertIn("was declined", msg.subject)
        self.assertIn("Reason:      Use the spare in store", msg.body_text)

    def test_receipt_emails_the_hod_with_the_new_stock(self):
        req = self._request()
        self._decide(req)
        self._receive(req)
        msg = EmailOutbox.objects.get(kind="accessory_received")
        self.assertEqual(msg.to, "n_hod@hospital.test")
        self.assertIn("Stock now:   4", msg.body_text)

    def test_a_restock_names_the_existing_part(self):
        part = Accessories.objects.create(name=Accessoriesname.objects.create(name="Oxygen Cell"),
                                          equipment_description=self.vent, stock_count=1, workshop=self.workshop)
        self.client.force_login(self.tech)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("partstools:request_accessory"), {
                "request_type": "restock", "equipment_description": self.vent.pk, "existing_accessory": part.pk,
                "requested_quantity": 2})
        self.assertIn("2 × Oxygen Cell", EmailOutbox.objects.get(kind="accessory_requested").subject)

    def test_each_step_is_mailed_once(self):
        req = self._request()
        self._decide(req)
        self._decide(req)  # a second click is refused by the view and sends nothing
        self.assertEqual(EmailOutbox.objects.filter(kind="accessory_decided").count(), 1)

    def test_steps_taken_offline_are_sent_when_the_pc_is_back_online(self):
        req = self._request()
        self._decide(req)
        self._receive(req)
        with mock.patch("django.core.mail.backends.locmem.EmailBackend.open", side_effect=OSError("offline")):
            self.assertEqual(send_pending(), (0, 3))
        EmailOutbox.objects.update(next_attempt_at=None)
        self.assertEqual(send_pending(), (3, 0))
        self.assertEqual({m.subject.split(":")[0] for m in mail.outbox},
                         {"[Action] New accessory requested", "Your request for Flow sensor was approved",
                          "Received"})
