from django.urls import path
from . import views

urlpatterns = [
    # General inventory view
    path('', views.inventory, name='inventory'),

    # HOD-specific views
    path('view/<uuid:workshop_id>/', views.inventory_for_hod, name='inventory_for_hod'),

    # Department-filtered inventory
    path('department/<uuid:dept_id>/', views.inventory_by_department, name='inventory_by_department'),

    # Department creation
    path('create_department/', views.create_department, name='create_department'),
    path('create_department/<uuid:workshop_id>/', views.create_department, name='create_department_for_hod'),

    # Equipment management
    path('add_inventory/', views.add_inventory, name='add_inventory'),
    path("edit_inventory/<uuid:equipment_id>/", views.edit_inventory, name="edit_inventory"),
    path('delete_equipment/<uuid:pk>/', views.delete_equipment, name='delete_equipment'),

    # Suppliers
    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/save/', views.supplier_save, name='supplier_create'),
    path('suppliers/save/<uuid:supplier_id>/', views.supplier_save, name='supplier_update'),
    path('suppliers/<uuid:supplier_id>/toggle/', views.supplier_toggle_active, name='supplier_toggle_active'),
    path('suppliers/import/template/', views.supplier_import_template, name='supplier_import_template'),
    path('suppliers/import/', views.supplier_import_upload, name='supplier_import_upload'),

    # Warranties (Equipment -> Warranty -> Supplier)
    path('warranties/', views.warranty_list, name='warranty_list'),
    path('warranties/save/', views.warranty_save, name='warranty_create'),
    path('warranties/<uuid:warranty_id>/', views.warranty_detail, name='warranty_detail'),
    path('warranties/<uuid:warranty_id>/save/', views.warranty_save, name='warranty_update'),
    path('warranties/<uuid:warranty_id>/remove/', views.warranty_remove, name='warranty_remove'),
    path('warranties/equipment/<uuid:equipment_id>/', views.equipment_warranty, name='equipment_warranty'),
    path('warranties/import/template/', views.warranty_import_template, name='warranty_import_template'),
    path('warranties/import/', views.warranty_import_upload, name='warranty_import_upload'),

    # Equipment description and manufacturer
    path('create_equipment_description/', views.create_equipment_description, name='create_equipment_description'),
    path('create-manufacturer/', views.create_manufacturer, name='create_manufacturer'),

    # Bulk import (Excel upload)
    path('import_equipment_template/', views.download_equipment_import_template, name='download_equipment_import_template'),
    path('upload_equipment_excel/', views.upload_equipment_excel, name='upload_equipment_excel'),
    path('import_departments_template/<uuid:workshop_id>/', views.download_department_import_template, name='download_department_import_template'),
    path('upload_departments_excel/<uuid:workshop_id>/', views.upload_departments_excel, name='upload_departments_excel'),

    # Export views
    path('export_equipment_excel/', views.export_equipment_to_excel, name='export_equipment_excel'),
    path('export_inventory_summary_excel/', views.export_inventory_summary_excel, name='export_inventory_summary_excel'),

    # Department management (existing URLs)
    path('department/edit/<uuid:dept_id>/', views.edit_department, name='edit_department'),
    path('department/delete/<uuid:dept_id>/', views.delete_department, name='delete_department'),
    path('department/transfer/<uuid:dept_id>/', views.transfer_department, name='transfer_department'),

    # Department dependency management
    path('department/<uuid:dept_id>/dependency_count/',
         views.get_department_dependency_count,
         name='get_department_dependency_count'),

    path('department/<uuid:dept_id>/transfer_dependencies/',
         views.transfer_department_dependencies,
         name='transfer_department_dependencies'),

    # Dashboard and analytics

    path('api/equipment-analytics/', views.equipment_analytics_api, name='equipment_analytics_api'),
    path('api/inventory-summary/', views.inventory_summary_api, name='inventory_summary_api'),

    # PDF exports
    path('export-equipment-pdf/', views.export_equipment_to_pdf, name='export_equipment_to_pdf'),
    path('export-summary-pdf/', views.export_inventory_summary_to_pdf, name='export_inventory_summary_to_pdf'),
    path('check-pdf-status/', views.check_pdf_generation_status, name='check_pdf_generation_status'),
    path('bulk-export-departments/', views.bulk_export_departments_pdf, name='bulk_export_departments_pdf'),

    # Equipment availability check - matches JavaScript underscore convention
    path('check_equipment_availability/', views.check_equipment_availability, name='check_equipment_availability'),


    path('reactivate_equipment/<uuid:equipment_id>/', views.reactivate_equipment, name='reactivate_equipment'),
    path('equipment/<uuid:equipment_id>/check-ppm-locations/',
         views.check_equipment_ppm_locations,
         name='check_equipment_ppm_locations'),
    # Transfer equipment
    path('transfer_equipment/<uuid:equipment_id>/', views.transfer_equipment, name='transfer_equipment'),
    path('get_available_departments_for_transfer/', views.get_available_departments_for_transfer, name='get_available_departments_for_transfer'),

    # Fetches models names from descriptions
    path('api/models/<uuid:description_id>/', views.get_models_by_description, name='get_models_by_description'),

]
