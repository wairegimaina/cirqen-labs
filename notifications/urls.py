from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.email_settings, name="email_settings"),
    path("test/", views.send_test_email, name="send_test_email"),
    path("outbox/", views.outbox, name="outbox"),
    path("outbox/<int:pk>/", views.outbox_action, name="outbox_action"),
    path("mine/", views.my_email, name="my_email"),
]
