"""Work orders left unreviewed for 48 hours remind the department's In-Charge."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from CalSoft.models import CalibrationNotification
from jobcard.models import jobcard
from notifications.approvals import remind_overdue_approvals
from notifications.models import EmailOutbox
from notifications.tasks import overdue_approval_reminders

from .tests import EMAIL, Base


@override_settings(**EMAIL)
class OverdueApprovalTests(Base):
    def _wo(self, hours_ago, status='Waiting Approval'):
        wo = jobcard.objects.create(department=self.department, equipment=self.equipment, workshop=self.workshop,
                                    priority_level="Low", action_taken="Repair", job_description="x",
                                    performed_by=self.tech, status=status)
        jobcard.objects.filter(pk=wo.pk).update(created_at=timezone.now() - timedelta(hours=hours_ago))
        return wo

    def test_in_charge_is_reminded_after_48_hours(self):
        wo = self._wo(49)
        self.assertEqual(remind_overdue_approvals(), 1)
        msg = EmailOutbox.objects.get(kind="work_order_overdue")
        self.assertEqual((msg.to, msg.cc), ("n_nic@hospital.test", ""))  # the In-Charge only
        self.assertIn("1 work order waiting for your review (ICU)", msg.subject)
        self.assertIn(str(wo.id)[:8].upper(), msg.body_text)
        self.assertIn("VENT-1", msg.body_text)
        self.assertTrue(CalibrationNotification.objects.filter(recipient=self.nic,
                                                               notification_type="work_order_overdue").exists())

    def test_sent_even_with_work_order_email_off(self):
        self._wo(49)
        with override_settings(NOTIFICATIONS_WORK_ORDER_EMAIL=False):
            self.assertEqual(remind_overdue_approvals(), 1)

    def test_not_before_48_hours(self):
        self._wo(47)
        self.assertEqual(remind_overdue_approvals(), 0)

    def test_not_once_reviewed(self):
        self._wo(72, status='Approved')
        self._wo(72, status='Declined')
        self.assertEqual(remind_overdue_approvals(), 0)

    def test_one_email_per_department_per_day(self):
        self._wo(49)
        self._wo(100)
        self.assertEqual(remind_overdue_approvals(), 1)
        self.assertIn("2 work orders", EmailOutbox.objects.get().subject)
        self.assertEqual(remind_overdue_approvals(), 0)
        self.assertEqual(remind_overdue_approvals(now=timezone.now() + timedelta(days=1)), 1)

    def test_hod_is_copied_after_96_hours(self):
        self._wo(97)
        self.assertEqual(remind_overdue_approvals(), 1)
        msg = EmailOutbox.objects.get(kind="work_order_overdue")
        self.assertEqual((msg.to, msg.cc), ("n_nic@hospital.test", "n_hod@hospital.test"))

    def test_hod_hears_the_day_a_work_order_crosses_96_hours(self):
        wo = self._wo(90)
        self.assertEqual(remind_overdue_approvals(), 1)  # In-Charge only
        jobcard.objects.filter(pk=wo.pk).update(created_at=timezone.now() - timedelta(hours=97))
        self.assertEqual(remind_overdue_approvals(), 1)
        self.assertEqual(EmailOutbox.objects.filter(cc="n_hod@hospital.test").count(), 1)
        self.assertEqual(remind_overdue_approvals(), 0)

    @override_settings(NOTIFICATIONS_DIGEST_SENDER=False)
    def test_only_the_site_sender_pc_sends_reminders(self):
        self._wo(49)
        self.assertEqual(overdue_approval_reminders(), 0)
