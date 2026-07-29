
# permissions.py
from django.core.exceptions import PermissionDenied
from functools import wraps


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


def hod_required(view_func):
    """Decorator to ensure only HODs can access the view"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if get_user_role(request.user) != 'HOD':
            raise PermissionDenied("Only HODs can access this page.")
        return view_func(request, *args, **kwargs)
    return wrapper


def role_required(*allowed_roles):
    """Decorator to check if user has one of the allowed roles"""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if get_user_role(request.user) not in allowed_roles:
                raise PermissionDenied(f"Access denied. Required role: {', '.join(allowed_roles)}")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
