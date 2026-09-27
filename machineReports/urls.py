from django.urls import path
from . import views

urlpatterns = [
    # Main dashboard
    path('', views.equipment_dashboard, name='equipment_dashboard'),

    # Predicted corrective maintenance
    path('failure-risk/', views.failure_risk, name='failure_risk'),

    # Equipment history views (UUID)


    path("reports/manufacturer/pdf/", views.export_manufacturer_performance_pdf, name="export_manufacturer_pdf"),

    # Export functionality (UUID)
    path('export/<uuid:equipment_id>/', views.export_equipment_history, name='export_equipment_history'),


    path('equipment/<uuid:equipment_id>/export/', views.export_equipment_history, name='export_equipment_history'),

    path('equipment/<uuid:equipment_id>/repair-details/',
         views.equipment_repair_details,
         name='equipment_repair_details'),
 
    # Enhanced equipment export URLs
    path('export-equipment-category-detailed-pdf/',
         views.export_equipment_category_detailed_pdf,
         name='export_equipment_category_detailed_pdf'),

    # Equipment count preview API
    path('equipment/count-preview/',
         views.equipment_count_preview_api,
         name='equipment_count_preview_api'),

    path('export-manufacturer-pdf/',
         views.export_manufacturer_performance_pdf,
         name='export_manufacturer_pdf'),
]
