
# permissions.py
from django.core.exceptions import PermissionDenied
from functools import wraps


def hod_required(view_func):
    """Decorator to ensure only HODs can access the view"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            if request.user.userprofile.role != 'HOD':
                raise PermissionDenied("Only HODs can access this page.")
        except AttributeError:
            raise PermissionDenied("User profile not found.")
        return view_func(request, *args, **kwargs)
    return wrapper


def role_required(*allowed_roles):
    """Decorator to check if user has one of the allowed roles"""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            try:
                user_role = request.user.userprofile.role
                if user_role not in allowed_roles:
                    raise PermissionDenied(f"Access denied. Required role: {', '.join(allowed_roles)}")
            except AttributeError:
                raise PermissionDenied("User profile not found.")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
