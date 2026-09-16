import logging
import zipstream
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_GET
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.db.models import Q, Case, When, Value, IntegerField
from django.db.models.functions import Cast, Substr
from django.core.paginator import Paginator
from django.core.mail import send_mail
from django.conf import settings

from CalSoft.models import (
    CalibrationSession,
    CalibrationSchedule,
    CalibrationProcedure,
    Equipment,
    CalibrationAuditLog,
    PendingCertificate,
    HistoricalCalibration,
)
from Inventory.models import Department
from CalSoft.pdf_generators import BtwelveHospitalCertificateGenerator, generate_btwelve_certificate

logger = logging.getLogger(__name__)


def require_certificate_access(view_func):
    from functools import wraps

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
                return HttpResponseForbidden("Insufficient permissions")

            if role == "Tech" and not user_profile.workshop:
                messages.error(request, "No workshop assigned. Please contact your administrator.")
                return HttpResponseForbidden("No workshop assigned")

        except AttributeError:
            messages.error(request, "User profile not configured properly.")
            return HttpResponseForbidden("Profile configuration error")

        return view_func(request, *args, **kwargs)

    return wrapper


@login_required
def certificate_list(request):
    user_profile = request.user.userprofile
    user_role = user_profile.role

    base_sessions = CalibrationSession.objects.select_related(
        "procedure", "performed_by", "device_description", "device_manufacturer"
    ).filter(status="approved")

    if user_role == "Tech":
        user_workshop = user_profile.workshop
        if not user_workshop:
            messages.error(request, "No workshop assigned to your profile. Contact admin.")
            return HttpResponseForbidden("No workshop assigned.")

        if user_workshop.category == "calibration_center":
            sessions = base_sessions
            accessible_equipment = Equipment.objects.all()
        elif user_workshop.category == "maintenance":
            workshop_equipment = Equipment.objects.filter(department__workshop=user_workshop)
            sessions = base_sessions.filter(
                device_serial__in=workshop_equipment.values_list("serial_number", flat=True)
            )
            accessible_equipment = workshop_equipment
        else:
            messages.error(request, f"Unknown workshop category: {user_workshop.category}")
            return HttpResponseForbidden("Invalid workshop category.")
    elif user_role == "NIC":
        if not user_profile.department:
            messages.error(request, "No department assigned to your account. Contact admin.")
            return HttpResponseForbidden("No department assigned.")

        accessible_equipment = Equipment.objects.filter(department=user_profile.department)
        sessions = base_sessions.filter(
            device_serial__in=accessible_equipment.values_list("serial_number", flat=True)
        )
        user_workshop = None
    else:
        messages.error(request, f"Unknown user role: {user_role}. Contact admin.")
        return HttpResponseForbidden("Invalid user role.")

    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    equipment_filter = request.GET.get("equipment")
    status = request.GET.get("status")
    department_name = request.GET.get("department")

    if date_from:
        try:
            from datetime import datetime

            start = datetime.strptime(date_from, "%Y-%m-%d")
            sessions = sessions.filter(timestamp__date__gte=start)
        except ValueError:
            messages.warning(request, "Invalid start date format.")

    if date_to:
        try:
            from datetime import datetime

            end = datetime.strptime(date_to, "%Y-%m-%d")
            sessions = sessions.filter(timestamp__date__lte=end)
        except ValueError:
            messages.warning(request, "Invalid end date format.")

    if equipment_filter:
        sessions = sessions.filter(
            Q(device_description__name__icontains=equipment_filter)
            | Q(device_serial__icontains=equipment_filter)
            | Q(device_model__icontains=equipment_filter)
            | Q(device_manufacturer__name__icontains=equipment_filter)
            | Q(certificate_number__icontains=equipment_filter)
        )

    if status in ["pass", "fail"]:
        sessions = sessions.filter(overall_pass=(status == "pass"))

    if department_name:
        accessible_serials = accessible_equipment.filter(
            department__name__icontains=department_name
        ).values_list("serial_number", flat=True)
        sessions = sessions.filter(device_serial__in=accessible_serials)

    sessions = sessions.annotate(
        has_cert=Case(
            When(certificate_number__isnull=False, then=Value(1)),
            When(certificate_number="", then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        cert_numeric=Case(
            When(
                certificate_number__isnull=False,
                certificate_number__regex=r"^BNH-\d+$",
                then=Cast(Substr("certificate_number", 5), output_field=IntegerField()),
            ),
            default=Value(0),
            output_field=IntegerField(),
        ),
    ).order_by("-has_cert", "-cert_numeric", "-timestamp", "-id")

    departments = (
        accessible_equipment.select_related("department")
        .values_list("department__name", flat=True)
        .distinct()
        .order_by("department__name")
    )
    departments = [dept for dept in departments if dept]

    enhanced_sessions = []
    for session in sessions:
        if not hasattr(session, "department_name") and session.device_serial:
            try:
                equipment = accessible_equipment.select_related("department").get(
                    serial_number=session.device_serial
                )
                session.department_name = (
                    equipment.department.name if equipment.department else "Unknown"
                )
                session.workshop_name = (
                    equipment.department.workshop.name
                    if hasattr(equipment.department, "workshop") and equipment.department.workshop
                    else "Unknown"
                )
            except Equipment.DoesNotExist:
                session.department_name = "Restricted"
                session.workshop_name = "Restricted"

        if session.timestamp:
            cal_due_date = session.timestamp.date() + relativedelta(months=12)
            session.cal_due_date = cal_due_date
            current_date = timezone.now().date()
            session.days_until_due = (cal_due_date - current_date).days
            session.is_overdue = session.days_until_due < 0

        enhanced_sessions.append(session)

    paginator = Paginator(enhanced_sessions, 10 if user_role == "NIC" else 50)
    page_number = request.GET.get("page")
    certificates_page = paginator.get_page(page_number)

    total_certificates = len(enhanced_sessions)
    overdue_count = sum(1 for s in enhanced_sessions if s.is_overdue)
    due_soon_count = sum(
        1 for s in enhanced_sessions if s.days_until_due is not None and 0 <= s.days_until_due <= 30
    )

    # AJAX response - return only the certificates HTML
    if (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.GET.get("ajax") == "1"
    ):
        html = render(
            request,
            "Calibrition/partials/certificates_list.html",
            {
                "certificates": certificates_page,
                "page_obj": certificates_page,
            },
        ).content.decode("utf-8")
        return JsonResponse({"html": html})

    template_name = (
        "Calibrition/department_certificates.html"
        if user_role == "NIC"
        else "Calibrition/certificate_list.html"
    )

    context = {
        "certificates": certificates_page,
        "page_obj": certificates_page,
        "departments": departments,
        "current_filters": {
            "department": department_name,
            "date_from": date_from,
            "date_to": date_to,
            "equipment": equipment_filter or "",
            "status": status,
        },
        "user_role": user_role,
        "certificate_stats": {
            "total": total_certificates,
            "overdue": overdue_count,
            "due_soon": due_soon_count,
        },
        "show_sidebar": True,
    }

    return render(request, template_name, context)


@login_required
@require_http_methods(["GET", "POST"])
def generate_comprehensive_certificate(request, session_pk):
    try:
        # The FK is named ``Department`` (capital D); the old code followed it
        # as ``session.department`` and raised AttributeError on every call.
        session = get_object_or_404(
            CalibrationSession.objects.select_related("Department__workshop"), pk=session_pk
        )

        department_name = "Unknown Department"
        workshop_name = "Unknown Workshop"

        if session.Department:
            department_name = session.Department.name
            if session.Department.workshop:
                workshop_name = session.Department.workshop.name
        elif session.device_serial:
            try:
                equipment = Equipment.objects.select_related("department").get(
                    serial_number=session.device_serial
                )
                if equipment.department:
                    department_name = equipment.department.name
                    if equipment.department.workshop:
                        workshop_name = equipment.department.workshop.name
            except Equipment.DoesNotExist:
                pass

        context = {
            "certificate_number": session.certificate_number
            or f"CAL-{session.pk}-{timezone.now().strftime('%Y%m%d')}",
            "location": department_name,
            "workshop": workshop_name,
            "generation_timestamp": timezone.now(),
            "generated_by": request.user.get_full_name() or request.user.username,
        }

        if request.method == "POST":
            context.update(
                {
                    "reviewer_name": request.POST.get("reviewer_name", ""),
                    "approver_name": request.POST.get("approver_name", ""),
                    "custom_notes": request.POST.get("custom_notes", ""),
                    "laboratory_info": request.POST.get("laboratory_info", ""),
                }
            )

        pdf_buffer = generate_btwelve_certificate(session, context)
        response = HttpResponse(pdf_buffer.getvalue(), content_type="application/pdf")
        filename = f"certificate_{session.certificate_number or session.id}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    except CalibrationSession.DoesNotExist:
        logger.error(f"Session {session_pk} not found")
        messages.error(request, f"Calibration session {session_pk} not found.")
        return redirect("calibration:certificate_list")

    except Exception as e:
        logger.error(
            f"Error generating certificate for session {session_pk}: {str(e)}", exc_info=True
        )
        messages.error(request, f"Error generating certificate: {str(e)}")
        return redirect("calibration:session_detail", pk=session_pk)


@login_required
def download_declined_certificate(request, pk):
    session = get_object_or_404(CalibrationSession, pk=pk, status="rejected")

    context = {
        "certificate_number": session.certificate_number or f"DECLINED-{session.pk}",
        "location": getattr(session, "Department_name", None) or "Unknown Department",
        "workshop": getattr(session, "workshop_name", None) or "Unknown Workshop",
        "generation_timestamp": timezone.now(),
        "generated_by": request.user.get_full_name() or request.user.username,
        "procedure": session.procedure.name if session.procedure else "Unknown Procedure",
        "device_model": session.device_model or "",
        "device_serial": session.device_serial or "",
        "device_description": str(session.device_description) if session.device_description else "",
        "is_declined": True,
        "rejection_reason": (
            session.get_rejection_reason_display() if session.rejection_reason else "Not specified"
        ),
        "rejection_comments": session.rejection_comments or "No comments provided",
        "rejected_by": (
            session.rejected_by.get_full_name() or session.rejected_by.username
            if session.rejected_by
            else "Unknown"
        ),
        "rejected_at": (
            session.rejected_at.strftime("%Y-%m-%d %H:%M") if session.rejected_at else "Unknown"
        ),
    }

    pdf_buffer = generate_btwelve_certificate(session, context)
    filename = f"declined_certificate_{session.certificate_number or session.id}.pdf"
    response = HttpResponse(pdf_buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _store_historical_data(session):
    try:
        for reading in session.readings.all():
            if reading.mean is not None:
                HistoricalCalibration.objects.create(
                    device_serial=session.device_serial,
                    parameter_name=reading.parameter.name,
                    sub_parameter_name=reading.sub_parameter.name if reading.sub_parameter else "",
                    set_value=reading.set_value.value,
                    measured_value=reading.mean,
                    error=reading.error,
                    uncertainty=reading.expanded_uncertainty,
                    calibration_date=session.timestamp,
                )
    except Exception as e:
        logger.error(f"Error in _store_historical_data: {str(e)}", exc_info=True)


def get_accessible_sessions(user, date_from=None, date_to=None):
    """
    Returns CalibrationSessions the user is allowed to access
    based on role and workshop/department rules.
    """
    from django.utils.dateparse import parse_date

    user_profile = user.userprofile
    user_workshop = user_profile.workshop

    base_sessions = CalibrationSession.objects.select_related("procedure", "performed_by").filter(
        status="approved"
    )

    if date_from and date_to:
        base_sessions = base_sessions.filter(approved_at__date__range=(date_from, date_to))

    if user_profile.role == "Tech":
        if not user_workshop:
            raise PermissionError("No workshop assigned to Tech profile.")

        if user_workshop.category == "calibration_center":
            sessions = base_sessions
            accessible_equipment = Equipment.objects.all()

        elif user_workshop.category == "maintenance":
            workshop_equipment = Equipment.objects.filter(department__workshop=user_workshop)
            sessions = base_sessions.filter(
                device_serial__in=workshop_equipment.values_list("serial_number", flat=True)
            )
            accessible_equipment = workshop_equipment
        else:
            raise PermissionError(f"Unknown workshop category: {user_workshop.category}")

    elif user_profile.role == "NIC":
        user_department = getattr(user_profile, "department", None)
        if not user_department:
            raise PermissionError("No department assigned to NIC profile.")

        # A session belongs to the NIC's department if it was recorded against
        # it, or if it calibrated a device that department owns. (The old code
        # filtered on ``department``; the field is ``Department``, so it raised.)
        nic_equipment = Equipment.objects.filter(department=user_department)
        sessions = base_sessions.filter(
            Q(Department=user_department)
            | Q(device_serial__in=nic_equipment.values_list("serial_number", flat=True))
        )
        accessible_equipment = nic_equipment
    else:
        raise PermissionError(
            f"Unknown role: {user_profile.role}. Only Tech and NIC roles can access certificates."
        )

    return sessions, accessible_equipment


@login_required
@require_GET
def bulk_certificates_download(request):
    """Stream calibration certificates in bulk for a date range."""
    from django.utils.dateparse import parse_date
    from django.http import StreamingHttpResponse
    from zipstream import ZipFile

    today = timezone.now().date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    date_from = parse_date(request.GET.get("date_from", "")) or week_start
    date_to = parse_date(request.GET.get("date_to", "")) or week_end
    check_only = request.GET.get("check_only") == "1"

    try:
        sessions, accessible_equipment = get_accessible_sessions(request.user, date_from, date_to)
    except PermissionError as e:
        return HttpResponseForbidden(str(e))
    except Exception as e:
        return JsonResponse({"error": f"Error retrieving sessions: {str(e)}"}, status=500)

    if not sessions.exists():
        if check_only:
            return JsonResponse({"count": 0})
        return JsonResponse({"error": "No approved sessions found in this date range."}, status=200)

    if check_only:
        return JsonResponse({"count": sessions.count()})

    try:
        z = ZipFile(mode="w", compression=zipstream.ZIP_STORED)

        for session in sessions:
            try:
                department_name = "Unknown Department"
                workshop_name = "Unknown Workshop"

                if session.Department:
                    department_name = session.Department.name
                    if session.Department.workshop:
                        workshop_name = session.Department.workshop.name
                elif session.device_serial:
                    try:
                        equipment = accessible_equipment.select_related("department").get(
                            serial_number=session.device_serial
                        )
                        if equipment.department:
                            department_name = equipment.department.name
                            if (
                                hasattr(equipment.department, "workshop")
                                and equipment.department.workshop
                            ):
                                workshop_name = equipment.department.workshop.name
                    except Equipment.DoesNotExist:
                        pass

                context = {
                    "certificate_number": session.certificate_number
                    or f"CAL-{session.pk}-{timezone.now().strftime('%Y%m%d')}",
                    "location": department_name,
                    "workshop": workshop_name,
                    "generation_timestamp": timezone.now(),
                    "generated_by": request.user.get_full_name()
                    or request.user.username
                    or "System",
                    "procedure": (
                        session.procedure.name if session.procedure else "Unknown Procedure"
                    ),
                    "device_model": session.device_model or "",
                    "device_serial": session.device_serial or "",
                    "device_description": (
                        str(session.device_description) if session.device_description else ""
                    ),
                }

                pdf_buffer = generate_btwelve_certificate(session, context)
                filename = f"certificate_{session.certificate_number or session.id}.pdf"
                z.writestr(filename, pdf_buffer.getvalue())

            except Exception as e:
                logger.error(
                    f"PDF generation failed for session {session.id}: {str(e)}", exc_info=True
                )
                continue

        response = StreamingHttpResponse(z, content_type="application/zip")
        response["Content-Disposition"] = (
            f'attachment; filename="certificates_{date_from}_to_{date_to}.zip"'
        )
        return response

    except Exception as e:
        logger.error(f"Error creating ZIP file: {str(e)}", exc_info=True)
        return JsonResponse({"error": f"Error creating certificate bundle: {str(e)}"}, status=500)
