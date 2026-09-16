import logging
from functools import wraps

from users.control import get_user_role
import uuid
import hashlib
from datetime import timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_GET
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse, HttpResponseForbidden
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError

from CalSoft.models import (
    CalibrationSession,
    CalibrationAuditLog,
    PendingCertificate,
    CalibrationReading,
)
from calSchedules.models import CalibrationSchedule


def is_ajax(request):
    return request.headers.get(
        "X-Requested-With"
    ) == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", "")


def can_review_calibrations(user):
    """Approving, rejecting and restoring calibration sessions issues or voids
    certificates, so it is limited to calibration-centre technologists (the
    users the sidebar shows this queue to) and HODs."""
    role = get_user_role(user)
    if role == "HOD":
        return True
    workshop = getattr(getattr(user, "userprofile", None), "workshop", None)
    return role == "Tech" and workshop is not None and workshop.category == "calibration_center"


def calibration_reviewer_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not can_review_calibrations(request.user):
            logger.warning("Denied calibration review %s to %s", request.path, request.user)
            return JsonResponse(
                {"success": False, "error": "Only calibration-centre staff can review calibration sessions."},
                status=403,
            )
        return view_func(request, *args, **kwargs)
    return wrapper


def _mark_schedule_for_session_approval(schedule, status, now):
    if not schedule or schedule.status == "completed":
        return

    try:
        schedule.status = status
        schedule.completed_date = now.date() if status == "completed" else None
        if hasattr(schedule, "is_locked"):
            schedule.is_locked = True
        schedule.updated_at = now

        update_fields = ["status", "completed_date", "updated_at"]
        if hasattr(schedule, "is_locked"):
            update_fields.append("is_locked")

        schedule.save(update_fields=update_fields)
    except ValidationError as e:
        logger.warning(f"Skipping schedule status update after session approval: {e}")


logger = logging.getLogger(__name__)


@login_required
def sessions_pending_approval(request):
    active_tab = request.GET.get("tab", "pending")
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    three_days_ago = now - timedelta(days=3)

    pending_count = CalibrationSession.objects.filter(status="pending_review").count()
    declined_count = CalibrationSession.objects.filter(
        status="rejected", rejected_at__gte=thirty_days_ago
    ).count()
    approved_today_count = CalibrationSession.objects.filter(
        status="approved", approved_at__date=now.date()
    ).count()
    high_priority_count = CalibrationSession.objects.filter(
        status="pending_review", overall_pass=False
    ).count()
    can_review_count = CalibrationSession.objects.filter(
        status="rejected", rejected_at__gte=thirty_days_ago, rejected_at__lte=three_days_ago
    ).count()
    awaiting_cert_count = CalibrationSession.objects.filter(
        status="approved_pending_certificate"
    ).count()

    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    equipment = request.GET.get("equipment")

    if active_tab == "awaiting_certificate":
        sessions = (
            CalibrationSession.objects.filter(status="approved_pending_certificate")
            .select_related("procedure", "performed_by", "approved_by")
            .order_by("-approved_at")
        )

        if date_from:
            sessions = sessions.filter(approved_at__date__gte=date_from)
        if date_to:
            sessions = sessions.filter(approved_at__date__lte=date_to)
        if equipment:
            sessions = sessions.filter(device_description__name__icontains=equipment)

        paginator = Paginator(sessions, 10)
        page_obj = paginator.get_page(request.GET.get("page"))

    elif active_tab == "declined":
        sessions = CalibrationSession.objects.filter(
            status="rejected", rejected_at__gte=thirty_days_ago
        ).select_related("procedure", "performed_by", "rejected_by")

        if date_from:
            sessions = sessions.filter(rejected_at__date__gte=date_from)
        if date_to:
            sessions = sessions.filter(rejected_at__date__lte=date_to)
        if equipment:
            sessions = sessions.filter(device_description__name__icontains=equipment)

        paginator = Paginator(sessions, 10)
        page_obj = paginator.get_page(request.GET.get("page"))

        for session in page_obj:
            session.can_restore = session.rejected_at and session.rejected_at <= three_days_ago
    else:
        sessions = CalibrationSession.objects.filter(status="pending_review").select_related(
            "procedure", "performed_by"
        )

        if date_from:
            sessions = sessions.filter(timestamp__date__gte=date_from)
        if date_to:
            sessions = sessions.filter(timestamp__date__lte=date_to)
        if equipment:
            sessions = sessions.filter(device_description__name__icontains=equipment)

        paginator = Paginator(sessions, 10)
        page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "active_tab": active_tab,
        "pending_count": pending_count,
        "declined_count": declined_count,
        "approved_count": approved_today_count,
        "high_priority_count": high_priority_count,
        "can_review_count": can_review_count,
        "awaiting_cert_count": awaiting_cert_count,
        "approval_rate": (
            round((approved_today_count / (approved_today_count + pending_count) * 100), 1)
            if (approved_today_count + pending_count)
            else 100
        ),
        "current_filters": {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "equipment": equipment or "",
        },
        "show_sidebar": True,
    }

    # AJAX response - return JSON for dynamic updates
    if (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.GET.get("format") == "json"
    ):
        table_html = render(
            request,
            "Calibrition/partials/sessions_table.html",
            {
                "page_obj": page_obj,
                "active_tab": active_tab,
            },
        ).content.decode("utf-8")
        return JsonResponse(
            {
                "html": table_html,
                "pending_count": pending_count,
                "declined_count": declined_count,
                "approved_count": approved_today_count,
                "high_priority_count": high_priority_count,
                "can_review_count": can_review_count,
                "awaiting_cert_count": awaiting_cert_count,
            }
        )

    return render(request, "Calibrition/sessions_pending_approval.html", context)


@login_required
@require_GET
def session_details(request, pk):
    """Return full session details (device, environment, readings) as JSON
    for the review modal on the pending-approval page."""
    session = get_object_or_404(
        CalibrationSession.objects.select_related(
            "procedure", "performed_by", "device_description", "device_manufacturer"
        ),
        pk=pk,
    )

    device_description = str(session.device_description) if session.device_description else None
    device_manufacturer = str(session.device_manufacturer) if session.device_manufacturer else None

    performed_by_name = (
        session.performed_by.get_full_name() or session.performed_by.username
        if session.performed_by
        else None
    )

    readings_qs = (
        session.readings.filter(active_status=True)
        .select_related("parameter", "sub_parameter", "set_value")
        .order_by("parameter__order", "set_value__order")
    )

    readings = []
    for r in readings_qs:
        readings.append(
            {
                "parameter": r.parameter.name if r.parameter else None,
                "sub_parameter": r.sub_parameter.name if r.sub_parameter else None,
                "set_value": float(r.set_value.value) if r.set_value else None,
                "unit": r.parameter.unit if r.parameter else "",
                "readings": [float(v) for v in r.get_readings_list()],
                "mean": float(r.mean) if r.mean is not None else None,
                "std_dev": (
                    float(r.standard_deviation) if r.standard_deviation is not None else None
                ),
                "error": float(r.error) if r.error is not None else None,
                "expanded_unc": (
                    float(r.expanded_uncertainty) if r.expanded_uncertainty is not None else None
                ),
                "passes_tolerance": r.passes_tolerance,
            }
        )

    data = {
        "id": str(session.id),
        "status": session.status,
        "overall_pass": session.overall_pass,
        "priority": "High" if not session.overall_pass else "Normal",
        "procedure": session.procedure.name if session.procedure else None,
        "performed_by": performed_by_name,
        "timestamp": (
            timezone.localtime(session.timestamp).strftime("%Y-%m-%d %H:%M")
            if session.timestamp
            else None
        ),
        "certificate_number": session.certificate_number,
        "approved_by": (
            session.approved_by.get_full_name() or session.approved_by.username
            if session.approved_by
            else None
        ),
        "approved_at": (
            timezone.localtime(session.approved_at).strftime("%Y-%m-%d %H:%M")
            if session.approved_at
            else None
        ),
        "device": {
            "description": device_description,
            "model": session.device_model,
            "serial": session.device_serial,
            "manufacturer": device_manufacturer,
        },
        "environment": {
            "temperature": (
                float(session.actual_temperature)
                if session.actual_temperature is not None
                else None
            ),
            "humidity": (
                float(session.actual_humidity) if session.actual_humidity is not None else None
            ),
            "pressure": (
                float(session.actual_pressure) if session.actual_pressure is not None else None
            ),
        },
        "readings": readings,
    }

    return JsonResponse(data)


@login_required
@require_http_methods(["POST"])
@calibration_reviewer_required
def approve_calibration_session_ajax(request, pk):
    try:
        session = get_object_or_404(CalibrationSession, pk=pk, status="pending_review")

        def is_hq_online():
            try:
                import requests
                from django.conf import settings

                sync_url = getattr(settings, "SYNC_API_URL", "http://192.168.10.50:5000")
                response = requests.get(f"{sync_url}/api/sync/health", timeout=5)
                return response.status_code == 200
            except Exception:
                return False

        def get_machine_identifier():
            try:
                mac_int = uuid.getnode()
                mac_hex = ":".join(f"{(mac_int >> ele) & 0xff:02x}" for ele in range(40, -1, -8))
                mac_hash = hashlib.sha1(mac_hex.encode()).hexdigest()[:12]
                return f"mac-{mac_hash}"
            except Exception:
                return f"machine-{str(uuid.uuid4())[:8]}"

        with transaction.atomic():
            locked_session = CalibrationSession.objects.select_for_update().get(pk=pk)

            if locked_session.status != "pending_review":
                return JsonResponse(
                    {
                        "success": False,
                        "error": f"Session status is '{locked_session.status}', cannot approve",
                    },
                    status=400,
                )

            now = timezone.now()

            if is_hq_online():
                try:
                    certificate_number = CalibrationSession.generate_certificate_number()
                    if not certificate_number:
                        raise ValueError("Certificate number generation failed")

                    locked_session.certificate_number = certificate_number
                    locked_session.status = "approved"
                    locked_session.approved_by = request.user
                    locked_session.approved_at = now
                    locked_session.updated_at = now
                    locked_session.save()

                    _mark_schedule_for_session_approval(locked_session.schedule, "completed", now)

                    PendingCertificate.objects.filter(
                        session=locked_session, sync_status="pending"
                    ).update(sync_status="completed", processed_at=now, updated_at=now)

                    logger.info(f"Session {locked_session.id} approved online")

                    return JsonResponse(
                        {
                            "success": True,
                            "certificate_number": certificate_number,
                            "mode": "online",
                            "session_id": str(locked_session.id),
                            "status": "approved",
                            "updated_at": now.isoformat(),
                        }
                    )

                except Exception as cert_error:
                    logger.error(f"Certificate generation error: {cert_error}", exc_info=True)
                    return JsonResponse(
                        {
                            "success": False,
                            "error": f"Failed to generate certificate: {str(cert_error)}",
                        },
                        status=500,
                    )

            else:
                locked_session.status = "approved_pending_certificate"
                locked_session.approved_by = request.user
                locked_session.approved_at = now
                locked_session.certificate_number = None
                locked_session.updated_at = now
                locked_session.save()

                machine_id = get_machine_identifier()
                pending_cert = PendingCertificate.objects.create(
                    session=locked_session,
                    machine_id=machine_id,
                    sync_status="pending",
                    created_at=now,
                    updated_at=now,
                )

                _mark_schedule_for_session_approval(locked_session.schedule, "pushed", now)

                logger.info(f"Session {locked_session.id} approved offline")

                return JsonResponse(
                    {
                        "success": True,
                        "certificate_number": None,
                        "mode": "offline",
                        "session_id": str(locked_session.id),
                        "status": "approved_pending_certificate",
                        "pending_cert_id": str(pending_cert.id),
                        "updated_at": now.isoformat(),
                        "message": "Approved offline. Certificate will be generated when connection is restored.",
                    }
                )

    except CalibrationSession.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "Session not found or not in pending_review status"},
            status=404,
        )
    except Exception as e:
        logger.error(f"Error approving session {pk}: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required
@calibration_reviewer_required
def reject_calibration_session(request, pk):
    try:
        session = get_object_or_404(CalibrationSession, pk=pk, status="pending_review")

        if request.method == "POST":
            rejection_reason = request.POST.get("rejection_reason")
            rejection_comments = request.POST.get("rejection_comments")

            session.status = "rejected"
            session.rejection_reason = rejection_reason
            session.rejection_comments = rejection_comments
            session.rejected_by = request.user
            session.rejected_at = timezone.now()
            session.save(
                update_fields=[
                    "status",
                    "rejection_reason",
                    "rejection_comments",
                    "rejected_by",
                    "rejected_at",
                ]
            )

            _mark_schedule_for_session_approval(session.schedule, "pending", timezone.now())

            messages.warning(request, f"Session rejected and schedule reset to pending.")

            if is_ajax(request):
                return JsonResponse({"success": True, "message": "Session rejected successfully"})

            return redirect("calibration:sessions_pending_approval")

        session_data = {
            "id": str(session.id),
            "certificate_number": session.certificate_number,
            "device_model": session.device_model,
            "device_serial": session.device_serial,
            "device_description": (
                str(session.device_description) if session.device_description else None
            ),
        }
        return JsonResponse(session_data)

    except Exception as e:
        logger.error(f"Error rejecting session {pk}: {str(e)}", exc_info=True)
        if is_ajax(request):
            return JsonResponse({"success": False, "error": str(e)}, status=500)
        raise


@login_required
@calibration_reviewer_required
def restore_rejected_session(request, pk):
    session = get_object_or_404(CalibrationSession, pk=pk)

    if session.status != "rejected":
        return JsonResponse(
            {
                "success": False,
                "error": f"Session status is '{session.status}', can only restore rejected sessions",
            },
            status=400,
        )

    if not session.rejected_at:
        return JsonResponse(
            {"success": False, "error": "Session has no rejection timestamp"}, status=400
        )

    three_days_ago = timezone.now() - timedelta(days=3)
    if session.rejected_at > three_days_ago:
        days_remaining = (session.rejected_at + timedelta(days=3) - timezone.now()).days
        return JsonResponse(
            {"success": False, "error": f"Cannot restore yet. {days_remaining} day(s) remaining."},
            status=400,
        )

    session.status = "pending_review"
    session.rejection_reason = None
    session.rejection_comments = None
    session.rejected_by = None
    session.rejected_at = None
    session.approved_by = None
    session.approved_at = None
    session.save(
        update_fields=[
            "status",
            "rejection_reason",
            "rejection_comments",
            "rejected_by",
            "rejected_at",
            "approved_by",
            "approved_at",
        ]
    )

    _mark_schedule_for_session_approval(session.schedule, "pending", timezone.now())

    logger.info(f"Session {session.id} restored from rejected to pending_review")

    return JsonResponse({"success": True, "message": "Session restored to pending review status"})


@login_required
def download_declined_certificate(request, pk):
    """Generate and download a PDF certificate for a declined/rejected session."""
    from django.http import HttpResponse
    from CalSoft.pdf_generators import generate_btwelve_certificate

    session = get_object_or_404(CalibrationSession, pk=pk, status="rejected")

    context = {
        "certificate_number": session.certificate_number or f"DECLINED-{session.pk}",
        "location": session.Department_name or "Unknown Department",
        "workshop": session.workshop_name or "Unknown Workshop",
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
