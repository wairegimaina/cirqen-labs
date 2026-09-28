"""Phase 4: a submitted Report Hub report reaches the HOD as a PDF, and on
Monday the Engineer In-charge is reminded if last week's report is missing."""
from datetime import date, timedelta

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from notifications.mailer import send_pending
from notifications.models import EmailOutbox
from notifications.reports import remind_missing_weekly_reports
from notifications.tasks import weekly_report_reminders
from reporthub.models import Report

from .tests import EMAIL, Base

MONDAY = date(2026, 9, 28)  # a Monday
LAST_MONDAY = MONDAY - timedelta(days=7)


@override_settings(**EMAIL)
class ReportSubmittedTests(Base):
    def _submit(self):
        today = date.today()
        last_week = today - timedelta(days=7)
        iso = last_week.isocalendar()
        self.client.force_login(self.deputy)  # the Engineer In-charge
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("report_hub:report_hub"), {
                "report_type": "weekly", "year": iso.year, "week": str(iso.week),
                "remarks": "Two ventilators repaired; one awaiting parts."})
        return Report.objects.get(workshop=self.workshop, period_type="weekly")

    def test_submitting_sends_the_hod_the_report_as_a_pdf(self):
        report = self._submit()
        self.assertEqual(report.submitted_by, self.deputy)
        msg = EmailOutbox.objects.get(kind="report_submitted")
        self.assertEqual((msg.to, msg.cc), ("n_hod@hospital.test", ""))  # the Deputy submitted it
        self.assertIn("Weekly report: Biomed, Week", msg.subject)
        self.assertTrue(msg.attachment_name.startswith("Weekly_Report_Biomed_"))
        send_pending()
        name, content, mime = mail.outbox[0].attachments[0]
        self.assertEqual(mime, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF"))

    def test_the_hods_deputy_is_copied_when_someone_else_submits(self):
        from notifications.reports import _report_submitted

        report = Report.objects.create(workshop=self.workshop, period_type="weekly", period_start=LAST_MONDAY,
                                       remarks="ok", submitted_by=self.tech)
        msg = _report_submitted(report.pk, actor=self.tech)
        self.assertEqual((msg.to, msg.cc), ("n_hod@hospital.test", "n_deputy@hospital.test"))

    def test_it_is_sent_once(self):
        from notifications.reports import _report_submitted

        report = Report.objects.create(workshop=self.workshop, period_type="weekly", period_start=LAST_MONDAY,
                                       remarks="ok", submitted_by=self.tech)
        _report_submitted(report.pk, None)
        self.assertIsNone(_report_submitted(report.pk, None))
        self.assertEqual(EmailOutbox.objects.filter(kind="report_submitted").count(), 1)


@override_settings(**EMAIL)
class WeeklyReminderTests(Base):
    def test_monday_reminds_the_in_charge_when_last_weeks_report_is_missing(self):
        self.assertEqual(remind_missing_weekly_reports(today=MONDAY, hour=9), 1)
        msg = EmailOutbox.objects.get(kind="report_reminder")
        self.assertEqual((msg.to, msg.cc), ("n_deputy@hospital.test", ""))  # the in-charge only
        self.assertIn("weekly report for week", msg.subject)
        self.assertIn("21 Sep", msg.body_text)

    def test_only_once_per_week(self):
        remind_missing_weekly_reports(today=MONDAY, hour=9)
        self.assertEqual(remind_missing_weekly_reports(today=MONDAY, hour=15), 0)

    def test_not_when_the_report_is_in(self):
        Report.objects.create(workshop=self.workshop, period_type="weekly", period_start=LAST_MONDAY,
                              remarks="done", submitted_by=self.deputy)
        self.assertEqual(remind_missing_weekly_reports(today=MONDAY, hour=9), 0)

    def test_not_on_other_days_or_before_eight(self):
        self.assertEqual(remind_missing_weekly_reports(today=MONDAY + timedelta(days=1), hour=9), 0)
        self.assertEqual(remind_missing_weekly_reports(today=MONDAY, hour=7), 0)

    @override_settings(NOTIFICATIONS_DIGEST_SENDER=False)
    def test_only_the_site_sender_pc_sends_reminders(self):
        self.assertEqual(weekly_report_reminders(), 0)
