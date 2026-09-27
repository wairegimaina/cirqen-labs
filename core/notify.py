"""Notifications: in-app always, email and SMS when configured.

In-app notifications use CalSoft.CalibrationNotification (a general
recipient/type/title/message/link record despite its name), which the header
bell already counts. Email goes to the user's address when outgoing mail is
configured (EMAIL_HOST_USER). SMS goes through Africa's Talking when
AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY are set, for the kinds
listed in NOTIFY_SMS_KINDS (default: approval_needed, calibration_overdue), to
the phone number on the user's profile.

The same notification is not repeated while an unread copy from today is
still there, so a daily check does not pile up duplicates.
"""
import logging
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_SMS_KINDS = "approval_needed,calibration_overdue"


def recipients(role, workshop=None, department=None, level=None):
    """Active users with this role (and workshop / department / level)."""
    User = get_user_model()
    users = User.objects.filter(is_active=True, userprofile__role=role)
    if workshop is not None:
        users = users.filter(userprofile__workshop=workshop)
    if department is not None:
        users = users.filter(userprofile__department=department)
    if level is not None:
        users = users.filter(userprofile__level=level)
    return list(users.select_related("userprofile"))


def workshop_leads(workshop):
    """The workshop's Engineer In-charge, else every technologist there."""
    return recipients("Tech", workshop=workshop, level="Engineer Incharge") or recipients("Tech", workshop=workshop)


def notify(users, kind, title, message, url="", email=True):
    """Create one notification per user; returns how many were created."""
    from CalSoft.models import CalibrationNotification

    today = timezone.localdate()
    created = 0
    seen = set()
    for user in users:
        if user is None or user.pk in seen:
            continue
        seen.add(user.pk)
        duplicate = CalibrationNotification.objects.filter(
            recipient=user, notification_type=kind, title=title, is_read=False,
            created_at__date=today).exists()
        if duplicate:
            continue
        CalibrationNotification.objects.create(
            recipient=user, notification_type=kind, title=title, message=message, action_url=url)
        created += 1
        if email:
            _email(user, title, message, url)
        if kind in _sms_kinds():
            _sms(user, f"{title}: {message}"[:300])
    return created


def _absolute(url):
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    return f"{base}{url}" if base and url.startswith("/") else url


def _email(user, title, message, url):
    if not (user.email and getattr(settings, "EMAIL_HOST_USER", "")):
        return
    from django.core.mail import send_mail

    from core.branding import organisation_name

    body = f"{message}\n\n{_absolute(url)}\n\n— Cirqen, {organisation_name()}" if url else message
    try:
        send_mail(f"[Cirqen] {title}", body, None, [user.email], fail_silently=False)
    except Exception as exc:  # mail down must not stop the work that triggered it
        logger.warning("Notification email to %s failed: %s", user.email, exc)


def _sms_kinds():
    return {k.strip() for k in os.getenv("NOTIFY_SMS_KINDS", DEFAULT_SMS_KINDS).split(",") if k.strip()}


def _sms(user, text):
    username = os.getenv("AFRICASTALKING_USERNAME", "")
    api_key = os.getenv("AFRICASTALKING_API_KEY", "")
    phone = getattr(getattr(user, "userprofile", None), "phone_number", "") or ""
    if not (username and api_key and phone):
        return
    import requests

    host = "api.sandbox.africastalking.com" if username == "sandbox" else "api.africastalking.com"
    try:
        response = requests.post(
            f"https://{host}/version1/messaging",
            data={"username": username, "to": phone, "message": text,
                  **({"from": os.environ["AFRICASTALKING_SENDER_ID"]} if os.getenv("AFRICASTALKING_SENDER_ID") else {})},
            headers={"apiKey": api_key, "Accept": "application/json"}, timeout=15)
        if response.status_code >= 300:
            logger.warning("SMS to %s refused: %s %s", phone, response.status_code, response.text[:200])
    except Exception as exc:
        logger.warning("SMS to %s failed: %s", phone, exc)
