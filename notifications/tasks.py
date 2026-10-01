"""Beat tasks: send the outbox, and the morning digest."""
import logging

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.timezone import localdate

from . import digest
from .mailer import is_site_sender, queue, send_pending
from .models import EmailOutbox

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(name="notifications.tasks.flush_outbox", ignore_result=True)
def flush_outbox():
    sent, failed = send_pending()
    if sent or failed:
        logger.info("notifications: sent %d, failed %d", sent, failed)
    return sent, failed


@shared_task(name="notifications.tasks.send_daily_digests", ignore_result=True)
def send_daily_digests(force=False):
    """Queue today's digest for every user with something to do.

    Runs hourly and acts once the local (EAT) hour reaches
    NOTIFICATIONS_DIGEST_HOUR, so a machine switched on late still sends.
    Only the site's sender PC does this (Settings > Email, or
    ``notifications.digest_sender`` in config.json); with it on every desktop,
    every user would get one copy per machine.
    """
    if not is_site_sender() and not force:
        return 0
    if not force and timezone.localtime().hour < getattr(settings, 'NOTIFICATIONS_DIGEST_HOUR', 7):
        return 0

    today = localdate()
    queued = 0
    users = (User.objects.filter(is_active=True, userprofile__active_status=True)
             .exclude(email='').exclude(email__isnull=True).select_related('userprofile'))
    for user in users:
        key = f"digest:{user.pk}:{today.isoformat()}"
        if EmailOutbox.objects.filter(dedupe_key=key).exists():
            continue
        try:
            sections = digest.build(user, today)
        except Exception:
            logger.exception("notifications: digest failed for %s", user.username)
            continue
        if not digest.any_due(sections):
            continue
        name = user.get_full_name() or user.username
        if queue(
            kind='daily_digest',
            dedupe_key=key,
            to_users=[user],
            subject=f"Your to-do list for {today:%a %d %b %Y}",
            template='daily_digest',
            context={'name': name, 'sections': sections, 'today': today},
        ):
            queued += 1
    return queued


@shared_task(name="notifications.tasks.weekly_report_reminders", ignore_result=True)
def weekly_report_reminders():
    """Hourly; on Monday from 08:00 (EAT) remind the Engineer In-charge of any
    workshop whose report for last week is missing. Site sender PC only."""
    if not is_site_sender():
        return 0
    from .reports import remind_missing_weekly_reports

    return remind_missing_weekly_reports()


@shared_task(name="notifications.tasks.stock_sweep", ignore_result=True)
def stock_sweep():
    """Every 15 minutes on the site sender PC: email any part running low that
    was never announced (for example parts already short before alerts)."""
    if not is_site_sender():
        return 0
    from .stock import alert_all_missing

    return alert_all_missing()


@shared_task(name="notifications.tasks.overdue_approval_reminders", ignore_result=True)
def overdue_approval_reminders():
    """Hourly on the site sender PC: remind each department's In-Charge of
    work orders waiting more than 48 hours for review (once a day)."""
    if not is_site_sender():
        return 0
    from .approvals import remind_overdue_approvals

    return remind_overdue_approvals()
