from django.urls import path
from . import views

app_name = 'jobcard'

urlpatterns = [
    path('', views.create_job_card, name='create_job_card'),
    path('ajax/load-equipment/', views.load_equipment, name='load_equipment'),
    path('jobcards/waiting/', views.waiting_jobcards, name='waiting_jobcards'),
    path('jobcards/approved/', views.approved_jobcards, name='approved_jobcards'),
    path('jobcards/declined/', views.declined_jobcards, name='declined_jobcards'),
    path('get-pending-ppm-schedules/', views.get_pending_ppm_schedules, name='get_pending_ppm_schedules'),
    # Use UUID instead of int
    path('jobcards/download/<uuid:jobcard_id>/', views.download_jobcard_docx, name='download_jobcard'),
    path('download/pdf/<uuid:jobcard_id>/', views.download_jobcard_pdf, name='download_jobcard_pdf'),

    # HOD view job cards by workshop (UUID)
    path('jobcards/workshop/<uuid:workshop_id>/', views.hod_workshop_jobcards, name='hod_workshop_jobcards'),

    path('load-accessories/', views.load_accessories, name='load_accessories'),
    path('check_stock_availability/', views.check_stock_availability, name='check_stock_availability'),
    path('get-user-signature/', views.get_user_signature_data, name='get_user_signature_data'),

    path("bulk-download/approved/", views.bulk_download_approved_jobcards, name="bulk_download_approved_jobcards"),
    path("bulk-download/declined/", views.bulk_download_declined_jobcards, name="bulk_download_declined_jobcards"),
    path("bulk-download/waiting/", views.bulk_download_waiting_jobcards, name="bulk_download_waiting_jobcards"),
]
