# permissions.py
import logging
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect

logger = logging.getLogger("users.permissions")


def get_user_role(user):
    """Return the user's role (e.g. 'HOD', 'Tech', 'NIC'), or None if the
    user has no profile / isn't authenticated. Single source of truth —
    used by the decorators below and by Equiper.context_processors so a
    template's idea of "what role is this" can never drift from what the
    view-level permission checks enforce."""
    try:
        return user.userprofile.role
    except AttributeError:
        return None


def role_required(*allowed_roles, json=False, redirect_to=None, message=None, extra_tags=""):
    """Allow a view only for users whose role is in ``allowed_roles``.

    How a refusal looks depends on who calls the view:
      * default           raise PermissionDenied (the 403 page)
      * json=True         JsonResponse({"success": False, "error": message}, status=403)
                          for endpoints called from page scripts
      * redirect_to=name  flash ``message`` and redirect, for pages linked from
                          the UI where a 403 page would be a dead end

    Every refusal is logged to the ``users.permissions`` logger.
    Stack it below @login_required so anonymous users are sent to log in first.
    """
    denial = message or f"Access denied. Required role: {', '.join(allowed_roles)}"

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            role = get_user_role(request.user)
            if role not in allowed_roles:
                logger.warning(
                    "Denied %s to %s (role=%s, allowed=%s)",
                    request.path, getattr(request.user, "username", "?"), role, allowed_roles,
                )
                if json:
                    return JsonResponse({"success": False, "error": denial}, status=403)
                if redirect_to:
                    messages.error(request, denial, extra_tags=extra_tags)
                    return redirect(redirect_to)
                raise PermissionDenied(denial)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def hod_required(view_func=None, **options):
    """``role_required('HOD')``; usable bare (@hod_required) or with options."""
    options.setdefault("message", "Only HODs can access this page.")
    decorator = role_required("HOD", **options)
    return decorator(view_func) if view_func is not None else decorator
