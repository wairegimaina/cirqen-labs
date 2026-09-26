from django.urls import path
from . import views

app_name = 'jobcard'

urlpatterns = [
    path('', views.create_job_card, name='create_job_card'),
    path('ajax/load-equipment/', views.load_equipment, name='load_equipment'),
    path('jobcards/waiting/', views.waiting_jobcards, name='waiting_jobcards'),
    path('jobcards/approved/', views.approved_jobcards, name='approved_jobcards'),
    path('jobcards/declined/', views.declined_jobcards, name='declined_jobcards'),
    # Checklists module + the work order form's lookup
    path('checklist-for-work/', views.checklist_for_work, name='checklist_for_work'),
    path('checklists/', views.checklist_list, name='checklist_settings'),
    path('checklists/new/', views.checklist_edit, name='checklist_create'),
    path('checklists/from-starter/', views.checklist_from_starter, name='checklist_from_starter'),
    path('checklists/import/template/', views.checklist_import_template, name='checklist_import_template'),
    path('checklists/import/', views.checklist_import_upload, name='checklist_import_upload'),
    path('checklists/<uuid:template_id>/', views.checklist_detail, name='checklist_detail'),
    path('checklists/<uuid:template_id>/edit/', views.checklist_edit, name='checklist_edit'),
    path('checklists/<uuid:template_id>/toggle/', views.checklist_toggle_active, name='checklist_toggle_active'),
    # One work order, with its checklist
    path('work-orders/<uuid:jobcard_id>/', views.work_order_detail, name='work_order_detail'),
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
