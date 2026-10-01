"""Read-only when the hospital's licence has lapsed (licence.py).

Viewing, searching, printing and exporting keep working; requests that save
something (POST, PUT, PATCH, DELETE) are refused, except signing in and out
and the Subscription page. A form gets a page that says why and who can
renew; a script or API call gets JSON. With no licence published nothing is
enforced.
"""
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render

import licence

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
ALWAYS_ALLOWED = ("/login/", "/settings/subscription/", "/health/")


def current() -> dict:
    return licence.current(settings.DATA_PATH)


class LicenceGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in SAFE_METHODS and not request.path.startswith(ALWAYS_ALLOWED):
            info = current()
            if info["state"] == "read_only":
                wants_json = ("application/json" in request.headers.get("Accept", "")
                              or request.content_type == "application/json"
                              or request.headers.get("X-Requested-With") == "XMLHttpRequest")
                if wants_json:
                    return JsonResponse({"error": "licence_read_only",
                                         "detail": "The subscription has ended; nothing new can be saved."},
                                        status=403)
                return render(request, "core/licence_read_only.html", {"licence": info}, status=403)
        return self.get_response(request)
