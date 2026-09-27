from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("kpis/", views.kpis, name="kpis"),
    path("machine/<uuid:pk>/", views.machine, name="machine"),
    path("labels/", views.labels, name="labels"),
    path("contracts/", views.contracts, name="contracts"),
    path("stock/", views.stock_alerts, name="stock_alerts"),
]
