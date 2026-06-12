from django.http import JsonResponse
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth import logout
from django.utils import timezone

import logging
logger = logging.getLogger(__name__)


class SessionExpiryMiddleware:
    """
    Detects expired Django sessions and responds gracefully.
    - For normal browser requests → redirect to login with a friendly message.
    - For AJAX requests → return 401 JSON so front-end can react (show modal, redirect).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and hasattr(request, 'session'):
            # Check if session is past SESSION_COOKIE_AGE (default 3600s / 1h)
            session_expiry = request.session.get_expiry_age()
            if session_expiry <= 0:
                # Log forced logout
                try:
                    from users.models import UserSecurityLog
                    UserSecurityLog.log_event(
                        user=request.user,
                        event_type='SESSION_EXPIRED',
                        description='Session expired due to inactivity',
                        ip_address=request.META.get('REMOTE_ADDR'),
                        user_agent=request.META.get('HTTP_USER_AGENT', '')
                    )
                except Exception:
                    pass

                logout(request)
                request.session.flush()

                next_url = request.get_full_path()
                login_url = f'/users/login/?next={next_url}'

                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse(
                        {'success': False, 'error': 'Session expired', 'redirect': login_url},
                        status=401
                    )

                messages.info(request, 'Your session has expired. Please log in again.')
                return redirect(login_url)

        return self.get_response(request)