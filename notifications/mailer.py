"""Queue an email (plus the matching in-app bell notification) and send the queue.

Sending is offline-safe. A batch that cannot reach the mail server at all (no
internet, DNS, timeout, a wrong password) is simply retried a few minutes
later and does not count against a message: only the server refusing a
message counts, and after EmailOutbox.MAX_ATTEMPTS of those it is marked
refused. Whatever is still unsent after OUTBOX_KEEP_DAYS is marked expired
rather than delivered late.
"""
import logging
import smtplib
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailOutbox, EmailSettings
from .preferences import wants
from .recipients import addresses

logger = logging.getLogger(__name__)

# While the mail server cannot be reached, look again this often.
OFFLINE_RETRY_MINUTES = 5
# A refused message is retried after 2, 4, 8 ... minutes, at most this long.
MAX_BACKOFF_MINUTES = 30

# The server answered and would not take this message.
REFUSED = (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError,
           smtplib.SMTPNotSupportedError)


def enabled():
    return getattr(settings, 'NOTIFICATIONS_EMAIL_ENABLED', True)


def keep_days():
    return int(getattr(settings, 'NOTIFICATIONS_OUTBOX_KEEP_DAYS', 7))


@dataclass
class Smtp:
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    use_ssl: bool
    from_email: str
    source: str  # "this PC" (Settings > Email) or "config" (config.json / environment)

    @property
    def configured(self):
        return bool(self.username)


def smtp():
    """The mail server this PC sends through: Settings > Email when filled
    in, otherwise config.json / the environment."""
    row = EmailSettings.objects.filter(pk=1).first()
    if row and row.username:
        return Smtp(row.host, row.port, row.username, row.password, row.security == 'starttls',
                    row.security == 'ssl', row.from_email or row.username, "this PC")
    user = getattr(settings, 'EMAIL_HOST_USER', '') or ''
    return Smtp(getattr(settings, 'EMAIL_HOST', ''), getattr(settings, 'EMAIL_PORT', 587), user,
                getattr(settings, 'EMAIL_HOST_PASSWORD', ''), getattr(settings, 'EMAIL_USE_TLS', True),
                getattr(settings, 'EMAIL_USE_SSL', False),
                getattr(settings, 'DEFAULT_FROM_EMAIL', '') or user, "config")


def is_site_sender():
    """Whether this PC sends the site's scheduled mail (digest, reminders, reports)."""
    row = EmailSettings.objects.filter(pk=1).first()
    return bool((row and row.is_site_sender) or getattr(settings, 'NOTIFICATIONS_DIGEST_SENDER', False))


def connection_for(server):
    if server.source == "config":
        # Django's own settings, and whatever EMAIL_BACKEND they name (tests use locmem).
        return get_connection(fail_silently=False)
    return get_connection(
        'django.core.mail.backends.smtp.EmailBackend', fail_silently=False, host=server.host, port=server.port,
        username=server.username, password=server.password, use_tls=server.use_tls, use_ssl=server.use_ssl,
        timeout=getattr(settings, 'EMAIL_TIMEOUT', 30))


def queue(kind, dedupe_key, to_users, subject, template, context, in_app_message=None, *,
          attachment=None, copy_rule=True, exclude=()):
    """Queue one email to ``to_users``.

    ``template`` names ``notifications/<template>.txt`` (required) and
    ``.html`` (optional). ``attachment`` is ``(file name, bytes, mime type)``.
    ``copy_rule`` applies the hospital's copy rule (staff mail copies the HOD,
    HOD mail the Deputy HOD). Users in ``exclude`` (usually whoever did the
    thing) get neither mail nor bell. Returns the EmailOutbox row, or None
    when nothing was queued (disabled, no addresses, or already queued).
    """
    skip = {getattr(u, 'pk', u) for u in exclude if u is not None}
    to_users = [u for u in to_users if u is not None and u.pk not in skip]
    if in_app_message:
        _bell(to_users, kind, subject, in_app_message)  # the bell comes whatever email is wanted
    if not enabled():
        return None
    # People who switched this kind of email off (My email) get the bell only.
    # Copies follow the event, so the HOD is still copied when every main
    # recipient has switched this email off; then the copies become the recipients.
    to, cc = addresses(to_users, copy_rule=copy_rule, exclude=skip, keep=lambda u: wants(u, kind))
    if not to and cc:
        to, cc = cc, []
    if not to:
        logger.info("notifications: %s has no recipient with an email address", kind)
        return None

    ctx = {'subject': subject, 'app_name': getattr(settings, 'NOTIFICATIONS_APP_NAME', 'Cirqen'), **context}
    body_text = render_to_string(f'notifications/{template}.txt', ctx)
    try:
        body_html = render_to_string(f'notifications/{template}.html', ctx)
    except Exception:  # TemplateDoesNotExist: plain text only
        body_html = ''

    name, data, mime = attachment if attachment else ('', None, '')
    try:
        with transaction.atomic():
            return EmailOutbox.objects.create(
                kind=kind, dedupe_key=dedupe_key[:200], to=','.join(to), cc=','.join(cc),
                subject=subject[:255], body_text=body_text, body_html=body_html,
                attachment=data, attachment_name=name[:150], attachment_type=mime[:100],
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


def expire_old(now=None):
    """Mark mail still unsent after keep_days() as expired. Returns how many."""
    now = now or timezone.now()
    return EmailOutbox.objects.filter(status='pending', created_at__lt=now - timedelta(days=keep_days())).update(
        status='expired', last_error=f"Not sent within {keep_days()} days.")


def _message(msg, server, connection):
    email = EmailMultiAlternatives(subject=msg.subject, body=msg.body_text, to=msg.to_list(), cc=msg.cc_list(),
                                   from_email=server.from_email, connection=connection)
    if msg.body_html:
        email.attach_alternative(msg.body_html, 'text/html')
    if msg.attachment:
        email.attach(msg.attachment_name or 'attachment', bytes(msg.attachment),
                     msg.attachment_type or 'application/octet-stream')
    return email


def send_pending(limit=50):
    """Send due messages. Returns (sent, failed). Safe to run concurrently."""
    if not enabled():
        return 0, 0
    now = timezone.now()
    expire_old(now)
    server = smtp()
    if not server.configured:
        waiting = EmailOutbox.objects.filter(status='pending').count()
        if waiting:
            logger.warning("notifications: outgoing email is not set up; %d message(s) waiting", waiting)
        return 0, 0

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
        connection = connection_for(server)
        try:
            connection.open()
        except Exception as exc:
            # No internet, DNS, timeout or a wrong password: nothing about the
            # messages themselves, so none of them is charged an attempt.
            _wait_offline(batch, exc, now)
            return 0, len(batch)
        try:
            for msg in batch:
                try:
                    _message(msg, server, connection).send()
                except REFUSED as exc:
                    _record_refusal(msg, exc, now)
                    failed += 1
                    continue
                except Exception as exc:
                    # The connection dropped part-way: the rest wait too.
                    _wait_offline([msg], exc, now)
                    failed += 1
                    continue
                msg.status, msg.sent_at, msg.last_error = 'sent', now, ''
                msg.attempts += 1
                msg.save(update_fields=['status', 'sent_at', 'last_error', 'attempts'])
                sent += 1
        finally:
            try:
                connection.close()
            except Exception:
                pass
    return sent, failed


def _wait_offline(batch, exc, now):
    error = f"Could not reach the mail server: {exc}"[:1000]
    for msg in batch:
        msg.last_error = error
        msg.next_attempt_at = now + timedelta(minutes=OFFLINE_RETRY_MINUTES)
        msg.save(update_fields=['last_error', 'next_attempt_at'])


def _record_refusal(msg, exc, now):
    msg.attempts += 1
    msg.last_error = str(exc)[:1000]
    if msg.attempts >= EmailOutbox.MAX_ATTEMPTS:
        msg.status = 'failed'
        logger.error("notifications: giving up on %s after %d refusals: %s", msg.kind, msg.attempts, exc)
    else:
        msg.next_attempt_at = now + timedelta(minutes=min(2 ** msg.attempts, MAX_BACKOFF_MINUTES))
    msg.save(update_fields=['attempts', 'last_error', 'status', 'next_attempt_at'])


def send_test(to_address, server=None):
    """Send one message now, bypassing the queue. Raises on failure."""
    server = server or smtp()
    connection = connection_for(server)
    email = EmailMultiAlternatives(
        subject=f"{getattr(settings, 'NOTIFICATIONS_APP_NAME', 'Cirqen')}: test email",
        body="This is a test from Cirqen. If you can read it, outgoing email from this PC works.",
        to=[to_address], from_email=server.from_email, connection=connection)
    email.send()
