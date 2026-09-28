"""
Notification center — list + mark-as-read for CalibrationNotification.

The model (CalSoft.models.CalibrationNotification) already existed with a
full schema (recipient, title, message, is_read, action_url, created_at)
but nothing in the codebase ever queried or created it — this module is
the first UI surface for it. Follows the same @login_required +
@require_http_methods + JsonResponse({"success": ...}) convention used by
the other AJAX views in this app (see view_modules/pending_sessions.py).
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods
from django.utils import timezone

from ..models import CalibrationNotification


def _notification_payload(n):
    return {
        "id": str(n.id),
        "notification_type": n.notification_type,
        "title": n.title,
        "message": n.message,
        "action_url": n.action_url,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def _unread_queryset(user):
    return CalibrationNotification.objects.filter(
        recipient=user, active_status=True, pending_delete=False
    )


@login_required
@require_GET
def notifications_list_api(request):
    """The current user's unread notifications, most recent first (up to 20),
    for the header dropdown. Read ones are cleared from the list: opening one
    or Mark all read removes it for this user only (each user has their own)."""
    unread = _unread_queryset(request.user).filter(is_read=False)
    notifications = list(unread.order_by("-created_at")[:20])
    unread_count = unread.count()
    return JsonResponse(
        {
            "success": True,
            "unread_count": unread_count,
            "notifications": [_notification_payload(n) for n in notifications],
        }
    )


@login_required
@require_http_methods(["POST"])
def notification_mark_read_ajax(request, pk):
    notification = get_object_or_404(
        CalibrationNotification, pk=pk, recipient=request.user
    )
    if not notification.is_read:
        notification.is_read = True
        notification.updated_at = timezone.now()
        notification.save(update_fields=["is_read", "updated_at"])
    return JsonResponse({"success": True})


@login_required
@require_http_methods(["POST"])
def notifications_mark_all_read_ajax(request):
    _unread_queryset(request.user).filter(is_read=False).update(
        is_read=True, updated_at=timezone.now()
    )
    return JsonResponse({"success": True})
