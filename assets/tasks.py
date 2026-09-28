"""Daily alerts and the monthly report for heads of department."""
import logging
from datetime import timedelta

from celery import shared_task
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

CONTRACT_WARNING_DAYS = 60
STANDARD_WARNING_DAYS = 30


def _month_end(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


@shared_task
def daily_alerts(today=None):
    """Reference standards due and service contracts ending.

    PPM and calibration due dates, warranties and high-risk devices are in the
    daily digest (notifications.digest); this covers what it does not. One summary notification per person per subject, repeated each day while
    the condition lasts (core.notify skips an unread copy from the same day).
    Returns {subject: notifications created}.
    """
    from CalSoft.models import Standard
    from core.notify import notify, recipients, workshop_leads
    from notifications.mailer import is_site_sender
    from workshop.models import Workshop

    from .models import ServiceContract

    if not is_site_sender():
        return {}  # one PC per site, or everyone gets a copy per PC
    today = today or timezone.localdate()
    created = {}

    def safe(subject, fn):
        try:
            created[subject] = fn()
        except Exception:
            logger.exception("Daily alert '%s' failed", subject)
            created[subject] = 0

    def standards():
        soon = Standard.objects.filter(active_status=True,
                                       calibration_due_date__lte=today + timedelta(days=STANDARD_WARNING_DAYS))
        if not soon.exists():
            return 0
        names = ", ".join(f"{s.name} (S/N {s.serial_number}, due {s.calibration_due_date:%d %b})" for s in soon[:5])
        people = recipients("HOD")
        for workshop in Workshop.objects.filter(category="calibration_center"):
            people += workshop_leads(workshop)
        return notify(people, "standard_expiring", f"{soon.count()} reference standard(s) due for calibration",
                      f"Recalibrate before use: {names}.")

    def contracts():
        horizon = today + timedelta(days=CONTRACT_WARNING_DAYS)
        ending = ServiceContract.objects.filter(active_status=True, end_date__gte=today, end_date__lte=horizon)
        count = ending.count()
        if not count:
            return 0
        people = recipients("HOD")
        workshops = set(ending.values_list("equipment__workshop", flat=True))
        for workshop in Workshop.objects.filter(pk__in=[w for w in workshops if w]):
            people += workshop_leads(workshop)
        return notify(people, "contract_expiring",
                      f"{count} service contract(s) end within {CONTRACT_WARNING_DAYS} days",
                      "Renew or plan cover before they lapse.", url=reverse("assets:contracts"))

    # Stock running low is mailed when it happens (notifications.stock) and
    # listed in the HOD's digest, so it is not repeated here.
    for subject, fn in (("standards", standards), ("contracts", contracts)):
        safe(subject, fn)
    logger.info("Daily alerts: %s", created)
    return created


@shared_task
def monthly_hod_report(today=None):
    """On the 1st: last month's KPIs, failure-risk list and backlog, emailed to each HOD
    with an email address, as a PDF attachment, through the outbox (so it is
    sent when the PC is next online). Only the site's sender PC does it.
    Returns how many were queued."""
    from core.branding import organisation_name
    from core.notify import recipients
    from Inventory.models import Equipment
    from notifications.mailer import is_site_sender, queue

    from .reports import monthly_report_pdf

    if not is_site_sender():
        return 0
    today = today or timezone.localdate()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    label = last_month_end.strftime("%B %Y")
    hods = [h for h in recipients("HOD") if h.email]
    if not hods:
        return 0
    pdf = monthly_report_pdf(Equipment.objects.filter(active_status=True), last_month_end)
    queued = 0
    for hod in hods:
        if queue(kind="monthly_report", dedupe_key=f"monthly-report:{hod.pk}:{last_month_end:%Y-%m}", to_users=[hod],
                 subject=f"[Cirqen] {organisation_name()} maintenance report — {label}", template="simple",
                 context={"message": f"Attached: equipment maintenance for {label}. KPIs cover the 12 months to "
                                     f"the end of {label}.", "link": ""},
                 attachment=(f"cirqen-report-{last_month_end:%Y-%m}.pdf", pdf, "application/pdf"),
                 copy_rule=False):
            queued += 1
    return queued
