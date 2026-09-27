from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("hq-connection/", views.hq_connection, name="hq_connection"),
    path("hq-connection/save/", views.hq_connection_save, name="hq_connection_save"),
    path("hq-connection/reset/", views.hq_connection_reset, name="hq_connection_reset"),
    path("hq-connection/test/", views.hq_connection_test, name="hq_connection_test"),
    path("site/", views.site_profile, name="site_profile"),
    path("site/logo/", views.site_logo, name="site_logo"),
    path("reports/<slug:name>/start/", views.report_start, name="report_start"),
    path("reports/job/<slug:key>/", views.report_status, name="report_status"),
    path("reports/job/<slug:key>/download/", views.report_download, name="report_download"),
]
