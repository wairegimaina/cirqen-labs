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
from .view_modules.sessions import session_detail, certificate_validation
from .view_modules.calibration import perform_calibration_global, complete_calibration_from_session

from .view_modules.standards import StandardsParameters_lists

# Standards and parameters CRUD: the AJAX-aware versions the list page's
# stand-parameters-list.js expects (JSON from edit/delete, soft delete for sync).
from .view_modules.parameters import (
    standard_create,
    standard_edit,
    standard_delete,
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


