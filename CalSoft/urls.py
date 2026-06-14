from django.urls import path
from . import views

app_name = "calibration"

urlpatterns = [
    # 🌐 Dashboard views
    path("", views.calsoft_dashboard, name="cal-dashboard"),
    # 📋 Calibration Procedures
    path("procedures/", views.procedure_list, name="procedure_list"),
    path("procedures/create/", views.procedure_create, name="procedure_create"),
    path("procedures/<uuid:pk>/", views.procedure_detail, name="procedure_detail"),
    path("procedures/<uuid:pk>/edit/", views.procedure_edit, name="procedure_edit"),
    path("procedures/<uuid:pk>/delete/", views.procedure_delete, name="procedure_delete"),
    path(
        "api/standard/<uuid:standard_id>/parameters/",
        views.api_standard_parameters_detail,
        name="api_standard_parameters_detail",
    ),
    # 🔧 Performing Calibration
    path("perform/", views.perform_calibration_global, name="perform_calibration"),
    path(
        "complete-calibration/<uuid:session_id>/",
        views.complete_calibration_from_session,
        name="complete_calibration_from_session",
    ),
    # 📚 Calibration Sessions
    # ⚠️ Specific sub-paths MUST come before the generic <uuid:pk>/ catch-all,
    # otherwise Django matches the catch-all first and routes to session_detail.
    path(
        "sessions/pending-approval/",
        views.sessions_pending_approval,
        name="sessions_pending_approval",
    ),
    path(
        "sessions/<uuid:pk>/approve/",
        views.approve_calibration_session_ajax,
        name="approve_calibration_session_ajax",
    ),
    path(
        "sessions/<uuid:pk>/reject/",
        views.reject_calibration_session,
        name="reject_calibration_session",
    ),
    path(
        "sessions/<uuid:pk>/restore/",
        views.restore_rejected_session,
        name="restore_rejected_session",
    ),
    path(
        "sessions/<uuid:pk>/declined-certificate/",
        views.download_declined_certificate,
        name="download_declined_certificate",
    ),
    path(
        "sessions/<uuid:session_id>/details/", views.api_session_details, name="api_session_details"
    ),
    path(
        "sessions/<uuid:session_pk>/certificate/comprehensive/",
        views.generate_comprehensive_certificate,
        name="generate_comprehensive_certificate",
    ),
    path("sessions/<uuid:pk>/", views.session_detail, name="session_detail"),
    path("sessions/", views.session_list, name="session_list"),
    # 🗓️ Schedule Management
    path(
        "schedules/<uuid:schedule_id>/assign/",
        views.assign_procedure_to_schedule,
        name="assign_procedure_to_schedule",
    ),
    path("schedules/auto-assign/", views.auto_assign_procedures, name="auto_assign_procedures"),
    # 🏭 Equipment Management
    path(
        "equipment/mapping/", views.equipment_procedure_mapping, name="equipment_procedure_mapping"
    ),
    # 📏 Standards Management
    path("standards/", views.standards_list, name="standard_list"),
    path("standards/create/", views.standard_create, name="standard_create"),
    path("standards/<uuid:pk>/edit/", views.standard_edit, name="standard_edit"),
    path("standards/<uuid:pk>/delete/", views.standard_delete, name="standard_delete"),
    # 📊 Parameters Management
    path("parameters/create/", views.parameter_create, name="parameter_create"),
    path("parameters/<uuid:pk>/edit/", views.parameter_edit, name="parameter_edit"),  # ✅ ADDED
    path("parameters/<uuid:pk>/delete/", views.parameter_delete, name="parameter_delete"),
    # Combined lists
    path(
        "StandardsParameters_lists/",
        views.StandardsParameters_lists,
        name="StandardsParameters_lists",
    ),
    # 🧭 Calibration Workflow Steps
    path(
        "workflow/<uuid:schedule_id>/", views.calibration_workflow_view, name="calibration_workflow"
    ),
    path(
        "workflow/step/<uuid:step_id>/update/",
        views.update_workflow_step,
        name="update_workflow_step",
    ),
    # 📊 Analytics & Reports
    path("analytics/", views.analytics_dashboard, name="analytics_dashboard"),
    path("analytics/trends/", views.trend_analysis, name="trend_analysis"),
    path("analytics/performance/", views.performance_analysis, name="performance_analysis"),
    path("reports/", views.reports_dashboard, name="reports_dashboard"),
    path("backup/", views.backup_calibration_data, name="backup_calibration_data"),
    # 📜 Certificate Management
    path("certificates/", views.certificate_list, name="certificate_list"),
    path(
        "certificates/validate/<str:certificate_number>/",
        views.certificate_validation,
        name="certificate_validation",
    ),
    # 🔐 Admin & Settings
    path("audit-log/", views.audit_log, name="audit_log"),
    # 🔌 API Endpoints
    path("api/dashboard/", views.api_dashboard_data, name="api_dashboard_data"),
    path("api/equipment-status/", views.api_equipment_status, name="api_equipment_status"),
    path("api/procedures/<uuid:pk>/", views.api_procedure_detail, name="api_procedure_detail"),
    path("api/validate-readings/", views.api_validate_readings, name="api_validate_readings"),
    path(
        "api/calculate-uncertainty/",
        views.api_calculate_uncertainty,
        name="api_calculate_uncertainty",
    ),
    path("api/schedule-status/", views.api_schedule_status, name="api_schedule_status"),
    path("api/schedule/<uuid:schedule_id>/", views.api_schedule, name="api_schedule"),
    path(
        "api/equipment/<uuid:equipment_id>/procedure/",
        views.api_equipment_procedure,
        name="api_equipment_procedure",
    ),
    path("api/parameters/", views.api_parameters, name="api_parameters"),
    path("api/standards/", views.api_standards, name="api_standards"),
    path("api/standard-parameters/", views.api_standard_parameters, name="api_standard_parameters"),
    path("api/procedure/<uuid:procedure_id>/", views.api_procedure, name="api_procedure"),
    path("api/set_values/", views.api_set_values, name="api_set_values"),
    path("api/dashboard-metrics/", views.api_dashboard_metrics, name="dashboard-metrics"),
    path("certificates/bulk/", views.bulk_certificates_download, name="bulk_certificates_download"),
    # Standards & Parameters SPA data endpoints
    path(
        "api/standards-parameters/",
        views.api_standards_parameters_data,
        name="api_standards_parameters_data",
    ),
]
