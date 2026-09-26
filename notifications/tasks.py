"""Beat tasks: send the outbox, and the morning digest."""
import logging

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.timezone import localdate

from . import digest
from .mailer import queue, send_pending
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
    Only a desktop with ``notifications.digest_sender`` set does this; with
    the flag on every desktop, every user would get one copy per machine.
    """
    if not getattr(settings, 'NOTIFICATIONS_DIGEST_SENDER', False) and not force:
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
