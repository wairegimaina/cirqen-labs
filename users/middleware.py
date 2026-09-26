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


class FirstLoginSetupMiddleware:
    """Keep an account with a temporary password on the setup page.

    An account created with a temporary or one-time password (create_hod, the
    desktop setup wizard, an HOD adding a user) must set its own password and
    signature first. The login view redirected there, but nothing stopped the
    user from opening any other page directly, so the temporary password could
    be used indefinitely.
    """

    ALLOWED_URL_NAMES = ("force_setup", "two_factor_setup", "activity_ping", "logout", "dashboard:hod_logout",
                         "custom_login", "health_check")
    ALLOWED_PREFIXES = ("/static/", "/media/", "/favicon")

    def __init__(self, get_response):
        self.get_response = get_response
        self._allowed_paths = None

    def _allowed(self, path):
        if self._allowed_paths is None:
            from django.urls import NoReverseMatch, reverse

            paths = set()
            for name in self.ALLOWED_URL_NAMES:
                try:
                    paths.add(reverse(name))
                except NoReverseMatch:
                    pass
            self._allowed_paths = paths
        return path in self._allowed_paths or path.startswith(self.ALLOWED_PREFIXES)

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not self._allowed(request.path):
            profile = getattr(user, "userprofile", None)
            # The temporary password is the risk; a missing signature is
            # already refused where a signature is needed (work orders,
            # certificates), and force_setup collects both.
            target = None
            if profile is not None and profile.must_change_password:
                target = "force_setup"
            elif profile is not None and self._needs_two_factor(user, profile):
                target = "two_factor_setup"
            if target:
                wants_json = (request.headers.get("x-requested-with") == "XMLHttpRequest"
                              or "application/json" in request.headers.get("accept", ""))
                if wants_json:
                    from django.http import JsonResponse

                    return JsonResponse({"error": "Finish your account setup first."}, status=403)
                return redirect(target)
        return self.get_response(request)

    @staticmethod
    def _needs_two_factor(user, profile):
        """With CIRQEN_REQUIRE_HOD_2FA, an HOD must register an authenticator app."""
        from django.conf import settings

        if not getattr(settings, "REQUIRE_HOD_TWO_FACTOR", False) or profile.role != "HOD":
            return False
        from users.models import TwoFactorDevice

        return not TwoFactorDevice.objects.filter(user=user).exists()
