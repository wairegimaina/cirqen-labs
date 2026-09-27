from django.urls import path
from . import views

app_name = 'schedule'

urlpatterns = [
    # AJAX endpoints
    path('ajax/schedules/', views.ajax_schedules, name='ajax_schedules'),
    path('ajax/completed-schedules/', views.ajax_completed_schedules, name='ajax_completed_schedules'),
    path('ajax/unscheduled/', views.ajax_unscheduled_equipment, name='ajax_unscheduled_equipment'),
    path('ajax/stats/', views.ajax_schedule_stats, name='ajax_schedule_stats'),

    # API endpoints
    path('api/overdue-status/', views.get_overdue_status, name='api_overdue_status'),

    # Main views
    path('', views.calibration_dashboard, name='calibration_dashboard'),
    path('push/<uuid:schedule_id>/', views.push_calibration_schedule, name='push_calibration_schedule'),
    path('complete/<uuid:schedule_id>/', views.mark_calibration_completed, name='mark_calibration_completed'),
    path('edit/<uuid:schedule_id>/', views.edit_calibration_schedule, name='edit_calibration_schedule'),
    path('delete/<uuid:schedule_id>/', views.delete_calibration_schedule, name='delete_calibration_schedule'),
    path('bulk_delete/', views.bulk_delete_calibration_schedules, name='bulk_delete_calibration_schedules'),
    path('bulk_complete/', views.bulk_mark_calibration_completed, name='bulk_mark_calibration_completed'),
    path('bulk_push/', views.bulk_push_calibration_schedules, name='bulk_push_calibration_schedules'),
    path('schedule/<uuid:equipment_id>/', views.schedule_calibration_equipment, name='schedule_calibration_equipment'),
    path('bulk_schedule/', views.bulk_schedule_unscheduled_calibration, name='bulk_schedule_unscheduled_calibration'),
    path('export/', views.export_calibration_excel, name='export_calibration_excel'),
    path('clear_filter/', views.clear_calibration_department_filter, name='clear_calibration_department_filter'),
    path('department/<uuid:dept_id>/', views.calibration_by_department, name='calibration_by_department'),
    path('calibration/export-pdf/', views.export_calibration_pdf, name='export_calibration_pdf'),
    path('calibration/department/<uuid:dept_id>/export-pdf/', views.export_department_calibration_pdf, name='export_department_calibration_pdf'),
    path('pending/', views.pending_calibrations, name='pending_calibrations'),

    # Group view: the estate as the scheduler sees it, plus targeted regrouping.
]
