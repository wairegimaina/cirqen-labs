from django.urls import path

from . import views

app_name = "audit_log"

urlpatterns = [
    path("", views.audit_log_list, name="list"),
]
