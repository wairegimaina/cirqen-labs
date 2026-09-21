# parts_tools/urls.py
from django.urls import path
from . import views

app_name = 'partstools'

urlpatterns = [
    # ── Dashboard ─────────────────────────────────────────────────────────────
    path('', views.accessories_dashboard, name='accessories_dashboard'),
    path('department/<uuid:dept_id>/', views.accessories_dashboard, name='accessories_dashboard_dept'),

    # ── JSON API endpoints (consumed by accessories.js fetch calls) ───────────
    path('api/accessories', views.api_accessories, name='api_accessories'),
    path('api/accessories/<uuid:pk>', views.api_accessory_detail, name='api_accessory_detail'),
    path('api/tools', views.api_tools, name='api_tools'),
    path('api/tools/<uuid:pk>', views.api_tool_detail, name='api_tool_detail'),
    path('api/accessory-requests', views.api_accessory_requests, name='api_accessory_requests'),

    # ── Tools management (Tech users) ─────────────────────────────────────────
    path('tools/add/', views.add_tool, name='add_tool'),
    path('tools/edit/<uuid:pk>/', views.edit_tool, name='edit_tool'),
    path('tools/get/<uuid:pk>/', views.get_tool, name='get_tool'),
    path('tools/delete/<uuid:pk>/', views.delete_tool, name='delete_tool'),
    path('tools/', views.tool_list, name='tool_list'),

    # ── AJAX helpers — tools ──────────────────────────────────────────────────
    path('ajax/add-tool-manufacturer/', views.ajax_add_tool_manufacturer, name='add_tool_manufacturer'),
    path('ajax/add-tool-name/', views.ajax_add_tool_name, name='add_tool_name'),

    # ── Accessory request workflow (regular users) ────────────────────────────
    path('request/', views.request_accessory, name='request_accessory'),
    path('requests/<uuid:request_id>/approve/', views.approve_accessory_request, name='approve_accessory_request'),
    path('requests/<uuid:request_id>/accept/', views.accept_accessory_request, name='accept_accessory_request'),

    # ── Accessory direct management (HOD only) ────────────────────────────────
    path('edit/<uuid:pk>/', views.edit_accessory, name='edit_accessory'),
    path('get/<uuid:pk>/', views.get_accessory, name='get_accessory'),
    path('delete/<uuid:pk>/', views.delete_accessory, name='delete_accessory'),
    path('list/', views.accessory_list, name='accessory_list'),

    # ── AJAX helpers — accessories ────────────────────────────────────────────
    path('ajax/add-accessory-name/', views.ajax_add_accessory_name, name='add_accessory_name'),
    path('ajax/add-manufacturer/', views.ajax_add_manufacturer, name='add_accessory_manufacturer'),

    # ── Delete: names & manufacturers ─────────────────────────────────────────
    path('ajax/delete-accessory-name/<uuid:name_id>/', views.delete_accessory_name, name='delete_accessory_name'),
    path('ajax/delete-tool-name/<uuid:name_id>/', views.delete_tool_name, name='delete_tool_name'),
    path('ajax/delete-accessory-manufacturer/<uuid:manufacturer_id>/', views.delete_accessory_manufacturer, name='delete_accessory_manufacturer'),
    path('ajax/delete-tool-manufacturer/<uuid:manufacturer_id>/', views.delete_tool_manufacturer, name='delete_tool_manufacturer'),

    # ── Exports ───────────────────────────────────────────────────────────────
    path('export/tools/', views.export_excel_tools, name='export_excel_tools'),
    path('export/accessories/', views.export_excel_accessories, name='export_excel_accessories'),
    path('export/pdf/tools/', views.export_pdf_tools, name='export_pdf_tools'),
    path('export/pdf/accessories/', views.export_pdf_accessories, name='export_pdf_accessories'),
]
