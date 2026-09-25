from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("months/", views.months, name="months"),
    path("months/<int:year>-<int:month>/", views.month_detail, name="month"),
    path("unscheduled/", views.unscheduled, name="unscheduled"),
    path("plans/", views.plans, name="plans"),
    path("plans/new/", views.plan_new, name="plan_new"),
    path("plans/<uuid:plan_id>/", views.plan_detail, name="plan"),
    path("plans/<uuid:plan_id>/preview/", views.plan_preview, name="plan_preview"),
    path("plans/<uuid:plan_id>/activate/", views.plan_activate, name="plan_activate"),
    path("plans/<uuid:plan_id>/delete/", views.plan_delete, name="plan_delete"),
    path("equipment/<uuid:equipment_id>/", views.equipment_history, name="equipment"),
]
