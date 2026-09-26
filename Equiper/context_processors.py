from django.conf import settings

from users.control import get_user_role


def _unread_notification_count(user):
    from CalSoft.models import CalibrationNotification

    return CalibrationNotification.objects.filter(
        recipient=user, is_read=False, active_status=True, pending_delete=False
    ).count()


def _pending_approval_count(role):
    """Mirrors CalSoft.view_modules.pending_sessions.sessions_pending_approval's
    own pending_count query exactly (global, not workshop-scoped) — only
    computed for Tech, the role that sees the Calibration sidebar block."""
    if role != "Tech":
        return 0
    from CalSoft.models import CalibrationSession

    return CalibrationSession.objects.filter(status="pending_review").count()


def _job_cards_waiting_count(user, role):
    """Mirrors jobcard.views.listing.waiting_jobcards' own scoping exactly:
    NIC sees their department's queue, Tech sees their workshop's."""
    if role not in ("Tech", "NIC"):
        return 0
    from jobcard.models import jobcard as JobCard

    profile = getattr(user, "userprofile", None)
    if not profile:
        return 0
    if role == "NIC":
        if not profile.department_id:
            return 0
        return JobCard.objects.filter(status="Waiting Approval", department=profile.department).count()
    if not profile.workshop_id:
        return 0
    return JobCard.objects.filter(status="Waiting Approval", workshop=profile.workshop).count()


def nav_context(request):
    """Expose role-based navigation flags and queue/notification counts to
    every template.

    Single source of truth for "what role is this" — built on the same
    get_user_role() the view-level hod_required/role_required decorators
    use (users/control.py), so the sidebar's visibility can never drift
    from what the views actually enforce.

    Deliberately does NOT resolve workshop category here — that already
    depends on a per-view `selected_workshop` override (e.g. Inventory's
    HOD workshop switcher) that isn't available at the context-processor
    stage, and templates already handle it correctly via
    `{% with ws=selected_workshop|default:user.userprofile.workshop %}`.
    """
    user = getattr(request, "user", None)
    is_authenticated = bool(user and user.is_authenticated)
    role = get_user_role(user) if is_authenticated else None

    return {
        # client.name from config.json; replaces the hospital name that was hard-coded in titles.
        "site_name": getattr(settings, "SITE_NAME", "Cirqen"),
        "nav": {
            "role": role,
            "is_hod": role == "HOD",
            "is_tech": role == "Tech",
            "is_nic": role == "NIC",
            "unread_notification_count": _unread_notification_count(user) if is_authenticated else 0,
            "pending_approval_count": _pending_approval_count(role),
            "job_cards_waiting_count": _job_cards_waiting_count(user, role) if is_authenticated else 0,
        }
    }
