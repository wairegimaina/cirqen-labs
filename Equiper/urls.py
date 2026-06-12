from django.contrib import admin
from django.urls import path, include
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
    path("", include("machineReports.urls")),
    path("parts-tools/", include("parts_tools.urls")),
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

# ✅ Static and Media files for development
if settings.DEBUG:
    # Add staticfiles URL patterns (serves from STATICFILES_DIRS)
    urlpatterns += staticfiles_urlpatterns()

    # Add media files
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
