"""Queue an email (plus the matching in-app bell notification) and send the queue."""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailOutbox
from .recipients import addresses

logger = logging.getLogger(__name__)


def enabled():
    return getattr(settings, 'NOTIFICATIONS_EMAIL_ENABLED', True)


def queue(kind, dedupe_key, to_users, subject, template, context, in_app_message=None):
    """Queue one email to ``to_users`` with the copy rule applied.

    ``template`` names ``notifications/<template>.txt`` (required) and
    ``.html`` (optional). Returns the EmailOutbox row, or None when nothing
    was queued (disabled, no addresses, or already queued under this key).
    """
    to_users = [u for u in to_users if u is not None]
    if in_app_message:
        _bell(to_users, kind, subject, in_app_message)
    if not enabled():
        return None
    to, cc = addresses(to_users)
    if not to:
        logger.info("notifications: %s has no recipient with an email address", kind)
        return None

    ctx = {'subject': subject, 'app_name': getattr(settings, 'NOTIFICATIONS_APP_NAME', 'Cirqen'), **context}
    body_text = render_to_string(f'notifications/{template}.txt', ctx)
    try:
        body_html = render_to_string(f'notifications/{template}.html', ctx)
    except Exception:  # TemplateDoesNotExist: plain text only
        body_html = ''

    try:
        with transaction.atomic():
            return EmailOutbox.objects.create(
                kind=kind, dedupe_key=dedupe_key[:200], to=','.join(to), cc=','.join(cc),
                subject=subject[:255], body_text=body_text, body_html=body_html,
            )
    except IntegrityError:
        return None  # already queued


def _bell(users, kind, title, message):
    """Mirror the email in the header's notification bell (a synced table)."""
    from CalSoft.models import CalibrationNotification

    for user in users:
        try:
            CalibrationNotification.objects.create(
                recipient=user, notification_type=kind, title=title[:200], message=message,
            )
        except Exception:
            logger.exception("notifications: could not create in-app notification for %s", user)


def send_pending(limit=50):
    """Send due messages. Returns (sent, failed). Safe to run concurrently."""
    if not enabled():
        return 0, 0
    if not settings.EMAIL_HOST_USER:
        logger.warning("notifications: email.host_user is not configured; %d message(s) waiting",
                       EmailOutbox.objects.filter(status='pending').count())
        return 0, 0

    now = timezone.now()
    sent = failed = 0
    with transaction.atomic():
        batch = list(
            EmailOutbox.objects.select_for_update(skip_locked=True)
            .filter(status='pending')
            .exclude(next_attempt_at__gt=now)
            .order_by('created_at')[:limit]
        )
        if not batch:
            return 0, 0
        connection = get_connection(fail_silently=False)
        try:
            connection.open()
        except Exception as exc:
            # Offline or SMTP down: back off every message in the batch alike.
            for msg in batch:
                _record_failure(msg, exc, now)
            return 0, len(batch)
        try:
            for msg in batch:
                email = EmailMultiAlternatives(
                    subject=msg.subject, body=msg.body_text, to=msg.to_list(), cc=msg.cc_list(),
                    from_email=settings.DEFAULT_FROM_EMAIL, connection=connection,
                )
                if msg.body_html:
                    email.attach_alternative(msg.body_html, 'text/html')
                try:
                    email.send()
                except Exception as exc:
                    _record_failure(msg, exc, now)
                    failed += 1
                    continue
                msg.status, msg.sent_at, msg.last_error = 'sent', now, ''
                msg.attempts += 1
                msg.save(update_fields=['status', 'sent_at', 'last_error', 'attempts'])
                sent += 1
        finally:
            connection.close()
    return sent, failed


def _record_failure(msg, exc, now):
    msg.attempts += 1
    msg.last_error = str(exc)[:1000]
    if msg.attempts >= EmailOutbox.MAX_ATTEMPTS:
        msg.status = 'failed'
        logger.error("notifications: giving up on %s after %d attempts: %s", msg.kind, msg.attempts, exc)
    else:
        # 2, 4, 8 … minutes, capped at 6 hours.
        msg.next_attempt_at = now + timedelta(minutes=min(2 ** msg.attempts, 360))
    msg.save(update_fields=['attempts', 'last_error', 'status', 'next_attempt_at'])
