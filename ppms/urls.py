
from django.urls import path
from . import views

urlpatterns = [
    # Main dashboard
    path('', views.ppm_dashboard, name='ppm_dashboard'),

    # UUID-based schedule actions
    path('push/<uuid:schedule_id>/', views.push_schedule, name='push_schedule'),
    path('complete/<uuid:schedule_id>/', views.mark_completed, name='mark_completed'),
    path('delete/<uuid:schedule_id>/', views.delete_schedule, name='delete_schedule'),

    # PPM PDF Export
    path('export-pdf/', views.export_ppm_pdf, name='export_ppm_pdf'),
    path('department/<uuid:dept_id>/export-pdf/', views.export_department_ppm_pdf, name='export_department_ppm_pdf'),

    # Bulk actions
    path('bulk_delete/', views.bulk_delete_schedules, name='bulk_delete_schedules'),
    path('bulk_complete/', views.bulk_mark_completed, name='bulk_mark_completed'),
    path('bulk_push_schedules/', views.bulk_push_schedules, name='bulk_push_schedules'),

    # Equipment schedule (UUID)
    path('schedule/<uuid:equipment_id>/', views.schedule_equipment, name='schedule_equipment'),
    path('bulk_schedule/', views.bulk_schedule_unscheduled, name='bulk_schedule_unscheduled'),

    # Export and filters
    path('export/', views.export_ppm_excel, name='export_ppm_excel'),
    path('clear_filter/', views.clear_department_filter, name='clear_department_filter'),

    # Department filter (UUID)
    path('department/<uuid:dept_id>/', views.ppm_by_department, name='ppm_by_department'),

    path('api/analytics/', views.get_analytics_data, name='get_analytics_data'),
    path('api/summary/', views.get_ppm_summary_api, name='ppm_summary_api'),
]
