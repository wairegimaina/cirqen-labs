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

class IdleTimeoutMiddleware:
    """Sign a user out after SESSION_IDLE_SECONDS without activity.

    The session cookie is renewed by every request, including the timers some
    pages use to refresh themselves, so a dashboard left open on a ward PC
    stayed signed in for ever. Here only a person's activity counts: page
    loads and form submissions, plus the ping base.html sends (at most every
    few minutes) while someone is typing or clicking, so a long calibration
    entry is never cut off. Background fetches (Sec-Fetch-Mode other than
    "navigate") are ignored.
    """

    EXEMPT_PREFIXES = ("/static/", "/media/", "/health/")

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _is_activity(request):
        if request.method != "GET":
            return True
        mode = request.headers.get("Sec-Fetch-Mode")
        return mode is None or mode == "navigate"

    def __call__(self, request):
        from django.conf import settings

        idle = int(getattr(settings, "SESSION_IDLE_SECONDS", 0) or 0)
        user = getattr(request, "user", None)
        if idle and user is not None and user.is_authenticated and not request.path.startswith(self.EXEMPT_PREFIXES):
            now = timezone.now().timestamp()
            last = request.session.get("last_activity")
            if last is not None and now - last > idle:
                try:
                    from users.models import UserSecurityLog

                    UserSecurityLog.log_event(
                        user=user, event_type='SESSION_EXPIRED',
                        description=f'Signed out after {idle // 60} minutes without activity',
                        ip_address=request.META.get('REMOTE_ADDR'),
                        user_agent=request.META.get('HTTP_USER_AGENT', ''))
                except Exception:
                    pass
                logout(request)
                login_url = '/'
                if request.method != "GET" or request.headers.get("Sec-Fetch-Mode", "navigate") != "navigate":
                    return JsonResponse({'success': False, 'error': 'Signed out after inactivity',
                                         'redirect': login_url}, status=401)
                messages.info(request, 'You were signed out after a period without activity. Please sign in again.')
                return redirect(login_url)
            if last is None or self._is_activity(request):
                request.session["last_activity"] = now
        return self.get_response(request)
