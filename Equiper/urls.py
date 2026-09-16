from django.contrib import admin
from django.urls import path, re_path, include
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from django.db import connection
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

# ✅ Admin branding
admin.site.site_title = "Biomedical Engineering Admin"
admin.site.site_header = "Equiper National Hospital"
admin.site.index_title = "Admin Biomedical Engineering"


# ✅ Health check endpoint
def health_check(request):
    """
    Returns a simple JSON indicating the health of the HQ server.
    Used by local routers or sync agents to verify online status.
    """
    try:
        connection.ensure_connection()
        return JsonResponse({
            "status": "ok",
            "db": "connected",
        })
    except Exception as e:
        return JsonResponse({
            "status": "error",
            "db": str(e)
        }, status=500)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("Inventory/", include("Inventory.urls")),
    path("ppms/", include("ppms.urls")),
    path("accessories/", include("parts_tools.urls")),
    path("login/", include("users.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("jobcard/", include("jobcard.urls")),
    path("reports/", include("reporthub.urls")),
    path("workshop/", include("workshop.urls")),
    path("calibration/", include("CalSoft.urls")),
    path("calSchedules/", include("calSchedules.urls")),
    path('updates/', include('updates.urls')),
    path("machineReports/", include("machineReports.urls")),
    # Each app is mounted once (a second include made reverse() ambiguous,
    # urls.W005). The old prefixes redirect so bookmarks keep working; "/" is
    # also where the desktop shell opens.
    path("", RedirectView.as_view(pattern_name="equipment_dashboard", query_string=True)),
    re_path(
        r"^parts-tools/(?P<rest>.*)$",
        RedirectView.as_view(url="/accessories/%(rest)s", query_string=True),
    ),
    path("audit-log/", include("audit_log.urls")),
    path("health/", health_check, name="health_check"),
]

# ✅ Debug toolbar (add BEFORE static files)
if settings.DEBUG:
    try:
        import debug_toolbar
        urlpatterns = [
            path('__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns
    except ImportError:
        pass

# ✅ Static and media files.
# Not gated behind settings.DEBUG: this app always runs its own embedded
# Django server (runserver --insecure, see bulider_tools/runtime.py) rather
# than a separate production WSGI/static stack, and it only ever binds to
# 127.0.0.1 (ALLOWED_HOSTS), so serving these unconditionally is safe and
# is what keeps the UI (CSS/JS/certificates/signatures) working now that
# DEBUG defaults to off.
urlpatterns += staticfiles_urlpatterns()
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
