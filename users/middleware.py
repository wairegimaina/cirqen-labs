from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth import logout

class ActiveUserMiddleware:
    """
    Middleware to check if the logged-in user's account is still active.
    If deactivated, log them out automatically.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Check if user account is deactivated
            if not request.user.active_status:
                # Log the forced logout
                try:
                    from .models import UserSecurityLog
                    UserSecurityLog.log_event(
                        user=request.user,
                        event_type='FORCED_LOGOUT',
                        description='User logged out - account deactivated',
                        ip_address=request.META.get('REMOTE_ADDR'),
                        user_agent=request.META.get('HTTP_USER_AGENT', '')
                    )
                except Exception:
                    pass

                # Logout the user
                logout(request)
                messages.error(
                    request,
                    'Your account has been deactivated. Please contact the administrator.'
                )
                return redirect('custom_login')

        response = self.get_response(request)
        return response
