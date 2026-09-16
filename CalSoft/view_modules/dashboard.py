import logging
import random
from zoneinfo import ZoneInfo
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from core import aggregate_cache
from django.shortcuts import render, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.contrib.auth import get_user_model

from CalSoft.models import (
    CalibrationSchedule,
    CalibrationSession,
    CalibrationProcedure,
    CalibrationNotification,
    Equipment,
)

User = get_user_model()
logger = logging.getLogger(__name__)


def generate_greeting(user_first_name):
    try:
        local_time = timezone.localtime(timezone.now(), ZoneInfo("Africa/Nairobi"))
        hour = local_time.hour

        if 5 <= hour < 12:
            messages_list = [
                f"Good morning, {user_first_name}! Wishing you a bright and productive start",
                f"Rise and shine, {user_first_name}! Let's make today count",
                f"Morning {user_first_name}! A new day brings new opportunities",
            ]
        elif 12 <= hour < 15:
            messages_list = [
                f"Good afternoon, {user_first_name}! I hope your day is going smoothly",
                f"Hello {user_first_name}, wishing you a productive afternoon",
            ]
        elif 15 <= hour < 22:
            messages_list = [
                f"Good evening, {user_first_name}! Hope you had a successful day",
                f"Relax and recharge, {user_first_name}. You've earned it",
            ]
        else:
            messages_list = [
                f"Late shift hero, {user_first_name}! Stay strong",
                f"Still going strong, {user_first_name}? Much respect",
            ]

        return random.choice(messages_list)
    except Exception:
        return f"Hello, {user_first_name}!"


@login_required
def calsoft_dashboard(request):
    if "greeting" not in request.session:
        request.session["greeting"] = generate_greeting(request.user.first_name)

    context = {
        "greeting": request.session["greeting"],
        "show_sidebar": True,
    }

    return render(request, "Calibrition/calsoft_dashboard.html", context)


@login_required
def api_dashboard_metrics(request):
    try:
        current_date = timezone.now().date()

        pending_count = CalibrationSchedule.objects.filter(
            status="pending", equipment__active_status=True
        ).count()
        pushed_count = CalibrationSchedule.objects.filter(
            status="pushed", equipment__active_status=True
        ).count()

        overdue_count = 0
        for schedule in CalibrationSchedule.objects.filter(
            status__in=["pending", "pushed"], equipment__active_status=True
        ):
            if schedule.scheduled_month:
                last_day = schedule.scheduled_month + timezone.timedelta(days=32)
                last_day = last_day.replace(day=1) - timezone.timedelta(days=1)
                if last_day < current_date:
                    overdue_count += 1

        in_progress_count = CalibrationSchedule.objects.filter(
            status="in_progress", equipment__active_status=True
        ).count()

        # completed = approved sessions that have a certificate number generated
        completed_count = (
            CalibrationSession.objects.filter(
                status__in=["approved", "approved_pending_certificate"],
                active_status=True,
                certificate_number__isnull=False,
            )
            .exclude(certificate_number="")
            .count()
        )

        approved_sessions = CalibrationSession.objects.filter(status="approved")

        week_pass_rate = round(
            (
                approved_sessions.filter(overall_pass=True).count()
                / approved_sessions.count()
                * 100
                if approved_sessions.count() > 0
                else 0
            ),
            1,
        )
        month_pass_rate = week_pass_rate  # legacy endpoint — both use all-time approved sessions

        total_equipment = Equipment.objects.filter(active_status=True).count()
        equipment_needing_calibration = (
            CalibrationSchedule.objects.filter(
                status__in=["pending", "pushed"], equipment__active_status=True
            )
            .values("equipment")
            .distinct()
            .count()
        )

        recent_sessions = (
            CalibrationSession.objects.filter(status="approved")
            .select_related("procedure", "performed_by")
            .order_by("-timestamp")[:5]
        )

        session_data = [
            {
                "id": str(s.id),
                "certificate_number": s.certificate_number,
                "device_model": s.device_model,
                "device_serial": s.device_serial,
                "procedure_name": s.procedure.name if s.procedure else "Unknown",
                "performed_by": s.performed_by.username if s.performed_by else "Unknown",
                "timestamp": s.timestamp.isoformat(),
                "overall_pass": s.overall_pass,
            }
            for s in recent_sessions
        ]

        return JsonResponse(
            {
                "success": True,
                "schedule_counts": {
                    "pending": pending_count,
                    "pushed": pushed_count,
                    "overdue": overdue_count,
                    "in_progress": in_progress_count,
                    "completed": completed_count,
                },
                "performance": {
                    "week_pass_rate": week_pass_rate,
                    "month_pass_rate": month_pass_rate,
                },
                "equipment": {
                    "total": total_equipment,
                    "needing_calibration": equipment_needing_calibration,
                },
                "recent_sessions": session_data,
            }
        )
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required
def api_dashboard_data(request):
    """
    Primary dashboard data endpoint used by calsoft_dashboard.html.
    - schedule_counts: pending/pushed/overdue from CalibrationSchedule
    - completed: approved sessions that have a certificate_number generated
    - pass rates: computed from approved sessions (overall_pass=True), scoped to week/month
    - total_sessions: sessions with a certificate number (all time)
    - pending_approval_count: sessions awaiting review (for the approval link badge)
    - recent_sessions: latest 5 approved sessions for the table
    """
    try:
        # Global (not per-workshop) figures, cached per day; schedule, session and
        # equipment saves invalidate the "cal" namespace.
        today = timezone.now().date()
        data = aggregate_cache.get_or_compute(
            "cal", ["dashboard", today], lambda: _calibration_dashboard_data(today)
        )
        return JsonResponse(data)

    except Exception as e:
        logger.error(f"api_dashboard_data error: {e}", exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


def _calibration_dashboard_data(current_date):
    # ── Schedule-based counts ──────────────────────────────────────────────
    pending_count = CalibrationSchedule.objects.filter(
        status="pending", equipment__active_status=True
    ).count()
    pushed_count = CalibrationSchedule.objects.filter(
        status="pushed", equipment__active_status=True
    ).count()

    # Overdue = pending or pushed schedules whose end-of-month deadline has passed
    overdue_count = 0
    for schedule in CalibrationSchedule.objects.filter(
        status__in=["pending", "pushed"], equipment__active_status=True
    ):
        if schedule.scheduled_month:
            next_month = schedule.scheduled_month.replace(day=28) + timezone.timedelta(days=4)
            last_day = next_month - timezone.timedelta(days=next_month.day)
            if last_day < current_date:
                overdue_count += 1

    # ── Session-based counts ───────────────────────────────────────────────
    APPROVED_STATUSES = ["approved", "approved_pending_certificate"]

    approved_sessions = CalibrationSession.objects.filter(
        status__in=APPROVED_STATUSES,
        active_status=True,
    )

    # completed = sessions that have a certificate number generated
    completed_count = (
        approved_sessions.filter(
            certificate_number__isnull=False,
        )
        .exclude(certificate_number="")
        .count()
    )

    # Pass rates scoped to current week and current month
    week_start = current_date - timezone.timedelta(days=current_date.weekday())
    month_start = current_date.replace(day=1)

    week_sessions = approved_sessions.filter(timestamp__date__gte=week_start)
    month_sessions = approved_sessions.filter(timestamp__date__gte=month_start)

    week_total = week_sessions.count()
    month_total = month_sessions.count()

    week_pass_rate = round(
        (
            week_sessions.filter(overall_pass=True).count() / week_total * 100
            if week_total > 0
            else 0
        ),
        1,
    )
    month_pass_rate = round(
        (
            month_sessions.filter(overall_pass=True).count() / month_total * 100
            if month_total > 0
            else 0
        ),
        1,
    )

    # ── Equipment ──────────────────────────────────────────────────────────
    total_equipment = Equipment.objects.filter(active_status=True).count()
    equipment_needing_calibration = (
        CalibrationSchedule.objects.filter(
            status__in=["pending", "pushed"], equipment__active_status=True
        )
        .values("equipment")
        .distinct()
        .count()
    )

    # ── Pending approval badge ─────────────────────────────────────────────
    pending_approval_count = CalibrationSession.objects.filter(
        status="pending_review", active_status=True
    ).count()

    # ── Recent approved sessions (latest 5) ────────────────────────────────
    recent_qs = approved_sessions.select_related("procedure", "performed_by").order_by(
        "-timestamp"
    )[:5]
    recent_sessions = [
        {
            "id": str(s.id),
            "certificate_number": s.certificate_number,
            "device_model": s.device_model,
            "device_serial": s.device_serial,
            "procedure_name": s.procedure.name if s.procedure else "Unknown",
            "performed_by": (
                (s.performed_by.get_full_name() or s.performed_by.username)
                if s.performed_by
                else "Unknown"
            ),
            "timestamp": s.timestamp.isoformat(),
            "overall_pass": s.overall_pass,
        }
        for s in recent_qs
    ]

    return {
        "success": True,
        "schedule_counts": {
            "pending": pending_count,
            "pushed": pushed_count,
            "overdue": overdue_count,
            "completed": completed_count,
        },
        "week_pass_rate": week_pass_rate,
        "month_pass_rate": month_pass_rate,
        "week_total": week_total,
        "month_total": month_total,
        "equipment_needing_calibration": equipment_needing_calibration,
        "total_equipment": total_equipment,
        "total_sessions": completed_count,
        "pending_approval_count": pending_approval_count,
        "current_month": current_date.strftime("%B %Y"),
        "recent_sessions": recent_sessions,
    }


@login_required
def api_equipment_status(request):
    return JsonResponse({"success": True, "equipment": []})


@login_required
def api_schedule_status(request):
    return JsonResponse({"success": True, "schedules": []})
