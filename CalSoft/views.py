from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods, require_GET
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.db import transaction
from functools import wraps
from django.core.paginator import Paginator

from .view_modules.dashboard import (
    calsoft_dashboard,
    api_dashboard_metrics,
    api_dashboard_data,
    api_equipment_status,
    api_schedule_status,
)
from .view_modules.procedures import (
    procedure_list,
    procedure_create,
    procedure_detail,
    procedure_edit,
    procedure_delete,
)
from .view_modules.sessions import session_detail, session_list, certificate_validation
from .view_modules.calibration import perform_calibration_global, complete_calibration_from_session

# Standards: only non-parameter views come from view_modules.standards
from .view_modules.standards import (
    standard_create,
    standard_edit,
    standard_delete,
    StandardsParameters_lists,
    auto_assign_procedures,
    equipment_procedure_mapping,
)

# Parameters: AJAX-aware versions live in view_modules.parameters
from .view_modules.parameters import (
    parameter_create,
    parameter_edit,
    parameter_delete,
    api_standards_parameters_data,
)

# Bulk Excel import of standards and parameters
from .view_modules.imports import (
    download_standards_import_template,
    upload_standards_excel,
    download_parameters_import_template,
    upload_parameters_excel,
)

# Alias for URL compatibility
standards_list = StandardsParameters_lists

from .view_modules.workflows import calibration_workflow_view, update_workflow_step
from .view_modules.certificates import (
    certificate_list,
    generate_comprehensive_certificate,
    bulk_certificates_download,
)
from .view_modules.pending_sessions import (
    sessions_pending_approval,
    approve_calibration_session_ajax,
    reject_calibration_session,
    restore_rejected_session,
    download_declined_certificate,
)
from .view_modules.api import (
    api_schedule,
    api_equipment_procedure,
    api_procedure,
    api_parameters,
    api_standards,
    api_standard_parameters,
    api_set_values,
    api_procedure_detail,
    api_standard_parameters_detail,
    api_validate_readings,
    api_calculate_uncertainty,
    api_session_details,
)
from .view_modules.analytics import (
    analytics_dashboard,
    trend_analysis,
    performance_analysis,
    reports_dashboard,
)
from .view_modules.notifications import (
    notifications_list_api,
    notification_mark_read_ajax,
    notifications_mark_all_read_ajax,
)

from .models import CalibrationSession, CalibrationSchedule, CalibrationAuditLog
from .forms import CalibrationScheduleForm


def require_certificate_access(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            user_profile = request.user.userprofile
            role = user_profile.role

            if role not in ["Tech", "NIC"]:
                messages.error(
                    request,
                    "Access denied. Only Technologists and In-Charge personnel can access this page.",
                )
                return HttpResponse("Insufficient permissions", status=403)

            if role == "Tech" and not user_profile.workshop:
                messages.error(request, "No workshop assigned. Please contact your administrator.")
                return HttpResponse("No workshop assigned", status=403)

        except AttributeError:
            messages.error(request, "User profile not configured properly.")
            return HttpResponse("Profile configuration error", status=403)

        return view_func(request, *args, **kwargs)

    return wrapper


@login_required
def assign_procedure_to_schedule(request, schedule_id):
    schedule = get_object_or_404(CalibrationSchedule, pk=schedule_id)

    if request.method == "POST":
        form = CalibrationScheduleForm(request.POST, instance=schedule)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    CalibrationAuditLog.objects.create(
                        user=request.user,
                        action="assign_procedure",
                        description=f"Assigned procedure to schedule {schedule.id}",
                        schedule=schedule,
                    )
                    messages.success(request, "Procedure assigned successfully.")
                    return redirect("calibration:cal-dashboard")
            except Exception as e:
                messages.error(request, f"Error assigning procedure: {str(e)}")
    else:
        form = CalibrationScheduleForm(instance=schedule)

    return render(
        request,
        "Calibration/assign_procedure.html",
        {"schedule": schedule, "form": form, "show_sidebar": True},
    )


@login_required
def audit_log(request):
    logs = CalibrationAuditLog.objects.select_related("user", "schedule").order_by("-timestamp")
    paginator = Paginator(logs, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "Calibration/audit_log.html",
        {
            "page_obj": page_obj,
            "total_logs": logs.count(),
            "show_sidebar": True,
        },
    )


@login_required
def backup_calibration_data(request):
    messages.success(request, "Data backup initiated successfully.")
    return redirect("calibration:calsoft_dashboard")
