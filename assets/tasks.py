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
    """Reference standards due, service contracts ending, low stock.

    PPM and calibration due dates, warranties and high-risk devices are in the
    daily digest (notifications.digest); this covers what it does not. One summary notification per person per subject, repeated each day while
    the condition lasts (core.notify skips an unread copy from the same day).
    Returns {subject: notifications created}.
    """
    from CalSoft.models import Standard
    from core.notify import notify, recipients, workshop_leads
    from parts_tools.models import Accessories
    from workshop.models import Workshop

    from .models import ServiceContract

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

    def stock():
        total = 0
        from django.db.models import F

        low = Accessories.objects.filter(active_status=True, reorder_level__gt=0,
                                         stock_count__lte=F("reorder_level")).select_related("workshop")
        by_workshop = {}
        for item in low:
            by_workshop.setdefault(item.workshop, []).append(item)
        for workshop, items in by_workshop.items():
            if workshop is None:
                continue
            total += notify(workshop_leads(workshop), "stock_low", f"{len(items)} part(s) at or below reorder level",
                            ", ".join(f"{i.name} ({i.stock_count})" for i in items[:6]) + ".",
                            url=reverse("assets:stock_alerts"))
        return total

    for subject, fn in (("standards", standards), ("contracts", contracts), ("stock", stock)):
        safe(subject, fn)
    logger.info("Daily alerts: %s", created)
    return created


@shared_task
def monthly_hod_report(today=None):
    """On the 1st: last month's KPIs, failure-risk list and backlog, emailed to each HOD
    with an email address, as a PDF attachment. Returns how many were sent."""
    from django.conf import settings
    from django.core.mail import EmailMessage

    from core.branding import organisation_name
    from core.notify import recipients
    from Inventory.models import Equipment

    from .reports import monthly_report_pdf

    today = today or timezone.localdate()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    label = last_month_end.strftime("%B %Y")
    if not getattr(settings, "EMAIL_HOST_USER", ""):
        logger.info("Monthly report not emailed: outgoing email is not configured")
        return 0
    pdf = monthly_report_pdf(Equipment.objects.filter(active_status=True), last_month_end)
    sent = 0
    for hod in recipients("HOD"):
        if not hod.email:
            continue
        message = EmailMessage(
            f"[Cirqen] {organisation_name()} maintenance report — {label}",
            f"Attached: equipment maintenance for {label}. KPIs cover the 12 months to the end of {label}.",
            to=[hod.email])
        message.attach(f"cirqen-report-{last_month_end:%Y-%m}.pdf", pdf, "application/pdf")
        try:
            message.send()
            sent += 1
        except Exception as exc:
            logger.warning("Monthly report to %s failed: %s", hod.email, exc)
    return sent
