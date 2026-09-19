from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("hq-connection/", views.hq_connection, name="hq_connection"),
    path("hq-connection/save/", views.hq_connection_save, name="hq_connection_save"),
    path("hq-connection/reset/", views.hq_connection_reset, name="hq_connection_reset"),
    path("hq-connection/test/", views.hq_connection_test, name="hq_connection_test"),
]
