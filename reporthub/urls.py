# reporthub/urls.py
from django.urls import path
from . import views

app_name = 'report_hub'

urlpatterns = [
    path('', views.report_hub, name='report_hub'),
    path('report/', views.report_hub, name='report_hub'),
    path('export/', views.export_report, name='export_report'),
    path('download-pdf/', views.download_pdf_report, name='download_pdf_report'),
    
    # HOD reports now using UUID instead of int
    path('hod-reports/', views.hod_reports_landing, name='hod_reports_landing'),
    path('hod-reports/<uuid:workshop_id>/', views.hod_reports_view, name='hod_reports_view'),
    
    path('report/get-weekly-choices/', views.get_weekly_choices_json, name='get_weekly_choices_json'),
]
