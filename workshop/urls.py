from django.urls import path
from . import imports, views

app_name = 'workshop'

urlpatterns = [
    path('', views.create_workshop, name='create_workshop'),
    path('create_workshop/', views.create_workshop, name='create_workshop'),
    path('edit_workshop/<uuid:workshop_id>/', views.edit_workshop, name='edit_workshop'),

    # NEW: Separate transfer endpoint for two-step deletion
    path('transfer_dependencies/<uuid:workshop_id>/', views.transfer_workshop_dependencies, name='transfer_dependencies'),

    path('delete_workshop/<uuid:workshop_id>/', views.delete_workshop, name='delete_workshop'),
    path('dependency_count/<uuid:workshop_id>/', views.get_dependency_count, name='get_dependency_count'),

    # Bulk import (Excel upload)
    path('import_template/', imports.download_workshop_import_template, name='workshop_import_template'),
    path('upload_excel/', imports.upload_workshops_excel, name='upload_workshops_excel'),
]
