"""Report Hub reports by email.

    submitted  -> the HOD: one line and the report as a PDF
    Monday     -> the Engineer In-charge, if last week's report is not in yet

This is how the HOD hears about work orders: per-work-order email is off
(notifications.events), and the weekly report carries them.
"""
import calendar
import logging
from datetime import date, timedelta

from django.db import transaction

from .mailer import queue
from .recipients import hods

logger = logging.getLogger(__name__)

PERIOD_NAMES = {"weekly": "Weekly", "monthly": "Monthly", "quarterly": "Quarterly", "annual": "Annual"}
# Reminders go out from this hour (EAT) on Monday.
REMINDER_HOUR = 8


def period(report):
    """(start, end, label) of a report's period."""
    start = report.period_start
    if report.period_type == "weekly":
        end = start + timedelta(days=6)
        label = f"Week {start.isocalendar().week} ({start:%d %b} – {end:%d %b %Y})"
    elif report.period_type == "monthly":
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        label = f"{start:%B %Y}"
    elif report.period_type == "quarterly":
        month = start.month + 2
        end = date(start.year, month, calendar.monthrange(start.year, month)[1])
        label = f"Q{(start.month - 1) // 3 + 1} {start.year}"
    else:
        end = date(start.year, 12, 31)
        label = str(start.year)
    return start, end, label


def report_pdf(report):
    """The report's PDF, as Report Hub's download builds it."""
    from reporthub.pdf_generators import PDFReportGenerator
    from reporthub.utils import get_workshop_performance_data

    start, end, label = period(report)
    submitter = report.submitted_by
    generator = PDFReportGenerator(
        workshop=report.workshop, periods=[(label, start, end)], report_type=report.period_type,
        context={
            "workshop": report.workshop,
            "generated_by": (submitter.get_full_name() or submitter.username) if submitter else "Cirqen",
            "period_label": label, "remarks": report.remarks or "",
            "total_waiting_approval": 0, "total_approved": 0, "total_declined": 0,
            "annual_data": None,
            "workshop_performance": get_workshop_performance_data(report.workshop, start, end),
            "report": report,
        })
    buffer = generator.generate_pdf()
    try:
        return buffer.getvalue()
    finally:
        buffer.close()


def report_submitted(report_id, actor=None):
    """Queue the report to the HOD after the transaction commits."""
    def run():
        try:
            _report_submitted(report_id, actor)
        except Exception:  # mail must never break submitting the report
            logger.exception("notifications: report email failed for report %s", report_id)
    transaction.on_commit(run)


def _report_submitted(report_id, actor):
    from reporthub.models import Report

    report = Report.objects.select_related("workshop", "submitted_by").get(pk=report_id)
    if report.period_type == "annual":
        return None
    start, end, label = period(report)
    kind = PERIOD_NAMES.get(report.period_type, "")
    submitter = report.submitted_by
    by = (submitter.get_full_name() or submitter.username) if submitter else "the Engineer In-charge"
    workshop = report.workshop.name
    file_name = f"{kind}_Report_{workshop.replace(' ', '_')}_{start:%Y%m%d}.pdf"
    return queue(
        kind="report_submitted", dedupe_key=f"report:{report.pk}",
        to_users=hods(), exclude=[actor],
        subject=f"{kind} report: {workshop}, {label}",
        template="simple",
        context={"message": f"{by} submitted the {kind.lower()} report for {workshop}, {label}. It is attached.",
                 "link": ""},
        attachment=(file_name, report_pdf(report), "application/pdf"),
        in_app_message=f"{by} submitted the {kind.lower()} report for {workshop} ({label}).",
    )


def remind_missing_weekly_reports(today=None, hour=None):
    """On Monday, remind each workshop's Engineer In-charge whose report for
    last week is not in. Once per workshop per week. Returns how many were queued."""
    from django.utils import timezone

    from reporthub.models import Report
    from users.models import UserProfile
    from workshop.models import Workshop

    now = timezone.localtime()
    today = today or now.date()
    hour = now.hour if hour is None else hour
    if today.weekday() != 0 or hour < REMINDER_HOUR:
        return 0
    last_monday = today - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)
    week = last_monday.isocalendar().week
    queued = 0
    for workshop in Workshop.objects.filter(active_status=True).order_by("name"):
        if Report.objects.filter(workshop=workshop, period_type="weekly", period_start=last_monday,
                                 active_status=True).exists():
            continue
        leads = [p.user for p in UserProfile.objects.filter(
            workshop=workshop, role="Tech", level="Engineer Incharge", active_status=True, user__is_active=True)
            .select_related("user")]
        if not leads:
            continue
        if queue(
            kind="report_reminder", dedupe_key=f"weekly-report-reminder:{workshop.pk}:{last_monday.isoformat()}",
            to_users=leads, copy_rule=False,
            subject=f"Reminder: submit {workshop.name}'s weekly report for week {week}",
            template="simple",
            context={"message": (f"The weekly report for {workshop.name}, week {week} "
                                 f"({last_monday:%d %b} – {last_sunday:%d %b %Y}), has not been submitted yet. "
                                 f"Submit it in Report Hub; the HOD receives it as a PDF."),
                     "link": ""},
            in_app_message=f"Submit {workshop.name}'s weekly report for week {week}.",
        ):
            queued += 1
    return queued
