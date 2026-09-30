"""Phase 0: the outbox survives being offline, expires after N days, carries
attachments, and is set up and watched from Settings > Email."""
import smtplib
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.testing import requires_postgres
from notifications import mailer
from notifications.mailer import queue, send_pending
from notifications.models import EmailOutbox, EmailSettings

from .tests import EMAIL, Base

LOCMEM_SEND = "django.core.mail.backends.locmem.EmailBackend.send_messages"


@override_settings(**EMAIL)
class OutboxTests(Base):
    def _queue(self, key="k", **extra):
        return queue("t", key, [self.tech], "Hello", "simple", {"message": "Hi", "link": ""}, **extra)

    def test_a_refused_message_counts_and_is_given_up_after_the_limit(self):
        msg = self._queue()
        refusal = smtplib.SMTPRecipientsRefused({"x@y": (550, b"no such user")})
        with mock.patch(LOCMEM_SEND, side_effect=refusal):
            for _ in range(EmailOutbox.MAX_ATTEMPTS):
                EmailOutbox.objects.filter(pk=msg.pk).update(next_attempt_at=None)
                send_pending()
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.attempts), ("failed", EmailOutbox.MAX_ATTEMPTS))

    def test_a_week_offline_uses_up_no_attempts_and_still_sends(self):
        msg = self._queue()
        with mock.patch("django.core.mail.backends.locmem.EmailBackend.open", side_effect=OSError("no network")):
            for _ in range(50):
                EmailOutbox.objects.filter(pk=msg.pk).update(next_attempt_at=None)
                send_pending()
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.attempts), ("pending", 0))
        self.assertIn("Could not reach the mail server", msg.last_error)
        EmailOutbox.objects.filter(pk=msg.pk).update(next_attempt_at=None)
        self.assertEqual(send_pending(), (1, 0))
        self.assertEqual(len(mail.outbox), 1)

    def test_a_dropped_connection_mid_batch_is_not_charged(self):
        msg = self._queue()
        with mock.patch(LOCMEM_SEND, side_effect=smtplib.SMTPServerDisconnected("gone")):
            send_pending()
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.attempts), ("pending", 0))

    def test_unsent_mail_expires_after_the_keep_days(self):
        msg = self._queue()
        EmailOutbox.objects.filter(pk=msg.pk).update(created_at=timezone.now() - timedelta(days=8))
        with override_settings(EMAIL_HOST_USER=""):  # not set up: nothing is sent, but expiry still runs
            send_pending()
        msg.refresh_from_db()
        self.assertEqual(msg.status, "expired")
        self.assertEqual(mail.outbox, [])

    def test_a_pdf_attachment_is_delivered(self):
        self._queue(attachment=("report.pdf", b"%PDF-1.4 x", "application/pdf"))
        send_pending()
        self.assertEqual(mail.outbox[0].attachments, [("report.pdf", b"%PDF-1.4 x", "application/pdf")])

    def test_copy_rule_can_be_left_out_and_the_actor_excluded(self):
        msg = self._queue(key="a", copy_rule=False)
        self.assertEqual((msg.to, msg.cc), ("n_tech@hospital.test", ""))
        msg = queue("t", "b", [self.tech, self.nic], "Hi", "simple", {"message": "x", "link": ""},
                    exclude=[self.nic])
        self.assertEqual(msg.to, "n_tech@hospital.test")

    def test_settings_on_this_pc_override_config(self):
        self.assertEqual(mailer.smtp().source, "config")
        EmailSettings(host="mail.hospital.org", port=465, security="ssl", username="cirqen@hospital.org",
                      password="secret", is_site_sender=True).save()
        server = mailer.smtp()
        self.assertEqual((server.source, server.host, server.use_ssl, server.from_email),
                         ("this PC", "mail.hospital.org", True, "cirqen@hospital.org"))
        self.assertTrue(mailer.is_site_sender())


@override_settings(**EMAIL)
class CoreNotifyTests(Base):
    def test_notify_email_is_queued_not_sent_directly_and_not_copied(self):
        from core.notify import notify

        notify([self.tech], "stock_low", "2 parts low", "Gloves (1)", url="/assets/stock/")
        self.assertEqual(mail.outbox, [])  # queued, not sent inline
        msg = EmailOutbox.objects.get(kind="notify")
        self.assertEqual((msg.to, msg.cc), ("n_tech@hospital.test", ""))
        send_pending()
        self.assertEqual(mail.outbox[0].subject, "[Cirqen] 2 parts low")


@override_settings(**EMAIL)
class EmailPagesTests(Base):
    URL = reverse("notifications:email_settings")

    def test_only_the_hod_can_open_them(self):
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(self.URL).status_code, 403)
        self.assertEqual(self.client.get(reverse("notifications:outbox")).status_code, 403)
        self.client.force_login(self.hod)
        self.assertContains(self.client.get(self.URL), "Mail server")

    def test_saving_keeps_the_password_when_left_blank_and_never_shows_it(self):
        self.client.force_login(self.hod)
        data = {"host": "smtp.gmail.com", "port": 587, "security": "starttls", "username": "c@h.org",
                "password": "s3cret-pass", "from_email": "", "is_site_sender": "on"}
        self.client.post(self.URL, data)
        self.client.post(self.URL, {**data, "password": ""})
        row = EmailSettings.load()
        self.assertEqual((row.password, row.is_site_sender, row.updated_by), ("s3cret-pass", True, self.hod))
        self.assertNotContains(self.client.get(self.URL), "s3cret-pass")

    def test_send_test_reports_success_and_failure(self):
        self.client.force_login(self.hod)
        response = self.client.post(reverse("notifications:send_test_email"), {"to": "me@h.org"}, follow=True)
        self.assertContains(response, "Test email sent to me@h.org")
        self.assertEqual(mail.outbox[-1].to, ["me@h.org"])
        with mock.patch(LOCMEM_SEND, side_effect=smtplib.SMTPAuthenticationError(535, b"bad password")):
            response = self.client.post(reverse("notifications:send_test_email"), {"to": "me@h.org"}, follow=True)
        self.assertContains(response, "The test email was not sent")

    def test_outbox_retry_and_discard(self):
        msg = queue("t", "x", [self.tech], "Hello", "simple", {"message": "Hi", "link": ""})
        EmailOutbox.objects.filter(pk=msg.pk).update(status="expired", attempts=3,
                                                     created_at=timezone.now() - timedelta(days=9))
        self.client.force_login(self.hod)
        self.assertContains(self.client.get(reverse("notifications:outbox")), "Hello")
        action = reverse("notifications:outbox_action", args=[msg.pk])
        self.client.post(action, {"action": "retry"})
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.attempts), ("pending", 0))
        send_pending()  # the retry restarted its clock, so it is not expired again
        msg.refresh_from_db()
        self.assertEqual(msg.status, "sent")

        other = queue("t", "y", [self.tech], "Bye", "simple", {"message": "Hi", "link": ""})
        response = self.client.post(reverse("notifications:outbox_action", args=[other.pk]),
                                    {"action": "discard", "next": "https://evil.example/"})
        self.assertEqual(response["Location"], reverse("notifications:outbox"))
        other.refresh_from_db()
        self.assertEqual(other.status, "cancelled")


@requires_postgres
@override_settings(**EMAIL)
class HeartbeatEmailSummaryTests(Base):
    """The sync agent tells HQ how this PC's email is doing (heartbeat)."""

    def _summary(self):
        from types import SimpleNamespace

        from django.db import connection

        connection.ensure_connection()
        raw = connection.connection
        pool = SimpleNamespace(getconn=lambda: raw, putconn=lambda c: None)
        from sync.sync_agent_7 import DownloadCertHeartbeatMixin

        return DownloadCertHeartbeatMixin.get_email_summary(SimpleNamespace(pool=pool))

    def test_it_counts_waiting_and_problem_mail(self):
        queue("t", "h1", [self.tech], "Waiting", "simple", {"message": "m", "link": ""})
        refused = queue("t", "h2", [self.tech], "Refused", "simple", {"message": "m", "link": ""})
        EmailOutbox.objects.filter(pk=refused.pk).update(status="failed")
        summary = self._summary()
        self.assertEqual((summary["pending"], summary["refused_or_expired_7d"], summary["last_sent"]), (1, 1, None))
        self.assertIsNotNone(summary["oldest_pending_hours"])
