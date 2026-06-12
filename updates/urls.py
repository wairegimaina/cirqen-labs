from django.urls import path
from . import views

app_name = "updates"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("broadcast/", views.broadcast_dashboard, name="broadcast"),
    path("check/", views.check_updates, name="check"),
    path("upload/", views.upload_package, name="upload"),
    path("apply/<str:version>/", views.apply_update, name="apply"),
    path("stream/<str:version>/", views.stream_progress, name="stream"),
    path("rollback/<str:version>/", views.rollback_update, name="rollback"),
    path("delete/<str:version>/", views.delete_package, name="delete"),
    path("history/", views.update_history, name="history"),
]
