from django.urls import path
from . import views
from . import views
app_name='dashboard'
urlpatterns = [
    # url for parts_tolls
    path('', views.Dashboard, name='dashboard-main'),
    path('nic-dashboard/', views.nic_dashboard, name='nic_dashboard'),
    path('Department_inventory/', views.nurse_inventory, name='Department_inventory'),
    path('nurse_ppms/', views.nurse_ppms, name='nurse_ppms'),
    path('hod-dashboard/', views.hod_dashboard, name='hod_dashboard'),
    path('log-out/',views.hod_logout_view, name='hod_logout'),
    path('search/', views.global_search, name='global_search'),

]
