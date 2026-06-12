import zipstream
from django.http import StreamingHttpResponse, HttpResponse, HttpResponseForbidden
from django.utils.dateparse import parse_date
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from time import localtime
from django import forms
from django.forms import formset_factory
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.db.models.functions import Cast, Substr
from django.db import models
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import json
from django.contrib.auth import get_user_model
User = get_user_model()
import uuid
import math
import csv
from django.db.models import IntegerField, Case, When, Value
from django.db.models.functions import Cast, Substr, Length
from django.urls import reverse, reverse_lazy

from django.db import transaction, IntegrityError
from django.db.models import IntegerField
from django.db.models.functions import Cast, Substr
from django.utils import timezone
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
import logging

import random
from zoneinfo import ZoneInfo
from django.utils import timezone
from datetime import datetime, timedelta, date
import openpyxl
import logging
import random
from zoneinfo import ZoneInfo
from django.utils import timezone
from CalSoft.pdf_generators import BtwelveHospitalCertificateGenerator, generate_btwelve_certificate
from Inventory.models import Department
from calSchedules.models import CalibrationSchedule, CalibrationAuditLog
from CalSoft.models import (
    CalibrationProcedure, CalibrationParameter, SessionParameterResolution, SubParameter, SetValue,
    CalibrationSession, CalibrationReading, CalibrationReport,
    HistoricalCalibration, Parameter, Standard, StandardParameter,
    EquipmentCalibrationProcedure, CalibrationWorkflow, CalibrationNotification, Equipment,PendingCertificate
)
from workshop.models import Workshop
from .utils import CalibrationCalculator,  DriftAnalyzer, QualityAssurance, TrendAnalysis, _validate_environmental_conditions
from .forms import (
    CalibrationProcedureForm, ParameterFormSet, ProcedureSearchForm, SubParameterFormSet,
    SetValueFormSet, CalibrationSessionForm, CalibrationReadingForm, SessionSearchForm,
    DataExportForm, TemplateImportForm, AdvancedAnalysisForm, QualityReviewForm,
    ParameterForm, StandardForm, StandardParameterFormSet, CalibrationScheduleForm,
    EquipmentProcedureMappingForm
)
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import Http404, HttpResponseForbidden
from django.contrib import messages
from django.db.models import Q
from django.core.paginator import Paginator
from django.utils import timezone
from functools import wraps

logger = logging.getLogger(__name__)


def is_ajax(request):
    """
    Return True when the request expects a JSON response.
    Covers both the legacy jQuery XMLHttpRequest header and the modern
    fetch() convention of sending 'Accept: application/json'.
    """
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


def _store_historical_data(session):
    """Store calibration data for historical analysis."""
    for reading in session.readings.all():
        # Only store historical data if we have valid measured values
        if reading.mean is not None:
            try:
                HistoricalCalibration.objects.create(
                    device_serial=session.device_serial,
                    parameter_name=reading.parameter.name,
                    sub_parameter_name=reading.sub_parameter.name if reading.sub_parameter else '',
                    set_value=reading.set_value.value,
                    measured_value=reading.mean,
                    error=reading.error,
                    uncertainty=reading.expanded_uncertainty,
                    calibration_date=session.timestamp
                )
            except Exception as e:
                logger.error(
                    f"Error storing historical data for reading {reading.id}: {str(e)}")
        else:
            logger.warning(
                f"Skipping historical data storage for reading {reading.id} - no mean value calculated")


def _calculate_linearity_analysis(readings_by_parameter):
    """Calculate linearity analysis for calibration readings."""
    linearity_data = {}
    for key, readings in readings_by_parameter.items():
        set_values = [float(r.set_value.value) for r in readings]
        measured_values = [float(r.mean) for r in readings if r.mean]

        if len(set_values) < 2 or len(measured_values) < 2:
            continue

        try:
            slope, intercept = TrendAnalysis.calculate_linear_regression(
                set_values, measured_values)
            linearity_error = max(abs(float(
                r.mean) - (slope * float(r.set_value.value) + intercept)) for r in readings if r.mean)

            linearity_data[key] = {
                'slope': slope,
                'intercept': intercept,
                'max_linearity_error': linearity_error
            }
        except Exception:
            linearity_data[key] = {'error': 'Unable to calculate linearity'}

    return linearity_data

@login_required
def calsoft_dashboard(request):
    """Enhanced CalSoft dashboard with approved sessions only."""
    try:
        current_date = timezone.now().date()
        first_day_of_month = current_date.replace(day=1)
        last_day_of_month = first_day_of_month + \
            relativedelta(months=1) - relativedelta(days=1)
        week_ago = timezone.now() - timedelta(days=7)

        # Define the statuses to be treated as pending
        pending_statuses = ['pending', 'pushed']

        # Schedule queries - FILTERED for active equipment only
        pending_schedules = CalibrationSchedule.objects.filter(
            status='pending',  # Only 'pending' status
            scheduled_month__range=(first_day_of_month, last_day_of_month),
            equipment__active_status=True
        ).select_related('equipment', 'workshop', 'calibration_procedure').order_by('scheduled_month')

        # ✅ NEW: Pushed schedules for current month
        pushed_schedules = CalibrationSchedule.objects.filter(
            status='pushed',  # Only 'pushed' status
            scheduled_month__range=(first_day_of_month, last_day_of_month),
            equipment__active_status=True
        ).select_related('equipment', 'workshop', 'calibration_procedure').order_by('scheduled_month')

        # ✅ FIXED: Correct overdue calculation
        # Overdue = schedules where the LAST DAY of scheduled month has passed
        overdue_schedules = CalibrationSchedule.objects.filter(
            status__in=pending_statuses,
            equipment__active_status=True
        ).select_related('equipment', 'workshop', 'calibration_procedure')

        # We'll filter these in Python to avoid complex annotations
        overdue_schedules_list = []
        for schedule in overdue_schedules:
            if schedule.scheduled_month:
                # Get the last day of the scheduled month
                scheduled_month_year = schedule.scheduled_month.year
                scheduled_month_month = schedule.scheduled_month.month

                # Get first day of next month, subtract 1 day to get last day of current month
                if scheduled_month_month == 12:
                    next_month_year = scheduled_month_year + 1
                    next_month_month = 1
                else:
                    next_month_year = scheduled_month_year
                    next_month_month = scheduled_month_month + 1

                from datetime import date
                last_day_of_scheduled_month = date(
                    next_month_year,
                    next_month_month,
                    1
                ) - timedelta(days=1)

                # Schedule is overdue if last day of its month has passed
                if last_day_of_scheduled_month < current_date:
                    overdue_schedules_list.append(schedule)

        overdue_count = len(overdue_schedules_list)
        overdue_schedules_display = overdue_schedules_list[:10]

        in_progress_schedules = CalibrationSchedule.objects.filter(
            status='in_progress',
            scheduled_month__range=(first_day_of_month, last_day_of_month),
            equipment__active_status=True
        ).select_related('equipment', 'workshop', 'calibration_procedure').order_by('scheduled_month')

        # ✅ FIXED: Completed schedules - filter by completed_date, not scheduled_month
        completed_schedules = CalibrationSchedule.objects.filter(
            status='completed',
            completed_date__range=(first_day_of_month, last_day_of_month),  # Changed from scheduled_month
            equipment__active_status=True
        ).select_related('equipment', 'workshop', 'calibration_procedure')

        # Counts
        pending_count = pending_schedules.count()
        pushed_count = pushed_schedules.count()  # ✅ NEW
        in_progress_count = in_progress_schedules.count()
        completed_count = completed_schedules.count()

        # Display limits
        pending_schedules_display = pending_schedules[:10]
        pushed_schedules_display = pushed_schedules[:10]  # ✅ NEW
        in_progress_schedules_display = in_progress_schedules[:10]

        # Notifications
        notifications = CalibrationNotification.objects.filter(
            recipient=request.user,
            is_read=False
        ).order_by('-created_at')[:10]

        # 🔹 Only approved sessions
        recent_calibrations = []
        raw_sessions = CalibrationSession.objects.filter(
            status='approved'
        ).select_related('procedure', 'performed_by').order_by('-timestamp')[:15]

        for session in raw_sessions:
            equipment = None
            if session.device_serial:
                try:
                    equipment = Equipment.objects.select_related('department').get(
                        serial_number=session.device_serial,
                        active_status=True
                    )
                except Equipment.DoesNotExist:
                    pass
            session.equipment = equipment
            recent_calibrations.append(session)

        # Recent failures (approved only)
        recent_failures = CalibrationSession.objects.filter(
            status='approved',
            overall_pass=False
        ).select_related('procedure', 'performed_by').order_by('-timestamp')[:10]

        for session in recent_failures:
            if session.device_serial:
                try:
                    session.equipment = Equipment.objects.select_related('department').get(
                        serial_number=session.device_serial,
                        active_status=True
                    )
                except Equipment.DoesNotExist:
                    session.equipment = None

        # Time-based performance metrics (approved only)
        week_ago = timezone.now() - timedelta(days=7)
        month_ago = timezone.now() - timedelta(days=30)
        quarter_ago = timezone.now() - timedelta(days=90)

        week_calibrations = CalibrationSession.objects.filter(
            status='approved', timestamp__gte=week_ago)
        week_total = week_calibrations.count()
        week_passed = week_calibrations.filter(overall_pass=True).count()
        week_pass_rate = (week_passed / week_total *
                          100) if week_total > 0 else 0

        month_calibrations = CalibrationSession.objects.filter(
            status='approved', timestamp__gte=month_ago)
        month_total = month_calibrations.count()
        month_passed = month_calibrations.filter(overall_pass=True).count()
        month_pass_rate = (month_passed / month_total *
                           100) if month_total > 0 else 0

        quarter_calibrations = CalibrationSession.objects.filter(
            status='approved', timestamp__gte=quarter_ago)
        quarter_total = quarter_calibrations.count()
        quarter_passed = quarter_calibrations.filter(overall_pass=True).count()
        quarter_pass_rate = (quarter_passed / quarter_total *
                             100) if quarter_total > 0 else 0

        # Equipment stats - already filtered for active equipment
        total_equipment = Equipment.objects.filter(active_status=True).count()
        equipment_needing_calibration = CalibrationSchedule.objects.filter(
            status__in=pending_statuses,
            equipment__active_status=True
        ).values('equipment').distinct().count()

        # Workshop distribution - FILTERED for active equipment
        workshop_stats = []
        for workshop in Workshop.objects.all():
            pending_for_workshop = CalibrationSchedule.objects.filter(
                workshop=workshop,
                status__in=pending_statuses,
                equipment__active_status=True
            ).count()
            workshop_stats.append({
                'name': workshop.name,
                'pending_count': pending_for_workshop
            })

        # 🔹 Top procedures (approved sessions only)
        procedure_stats = []
        for procedure in CalibrationProcedure.objects.filter(active_status=True):
            procedure_sessions = CalibrationSession.objects.filter(
                status='approved',
                procedure=procedure
            )
            total_sessions = procedure_sessions.count()
            if total_sessions > 0:
                passed_sessions = procedure_sessions.filter(
                    overall_pass=True).count()
                pass_rate = (passed_sessions / total_sessions * 100)
                procedure_stats.append({
                    'name': procedure.name,
                    'pass_rate': pass_rate,
                    'total_sessions': total_sessions
                })

        procedure_stats.sort(key=lambda x: x['pass_rate'], reverse=True)
        top_procedures = procedure_stats[:5]

        # Alerts
        alerts = []
        if overdue_count > 0:
            alerts.append({
                'type': 'danger',
                'message': f'{overdue_count} calibration(s) are overdue and need immediate attention.',
                'action_url': '{% url "calibration:schedule_list" %}?overdue=true'
            })

        if equipment_needing_calibration > (total_equipment * 0.1):
            alerts.append({
                'type': 'warning',
                'message': f'{equipment_needing_calibration} pieces of equipment need calibration this month.',
                'action_url': '{% url "calibration:equipment_list" %}'
            })

        pending_approval_sessions = CalibrationSession.objects.filter(
            status='pending_review'
        ).select_related('procedure', 'performed_by').order_by('timestamp')

        pending_sessions_count = pending_approval_sessions.count()

        # Generate greeting if not in session
        if "greeting" not in request.session:
            request.session["greeting"] = generate_greeting(
                request.user.first_name)

        context = {
            'greeting': request.session["greeting"],
            'pending_sessions_count': pending_sessions_count,
            'pending_approval_sessions': pending_approval_sessions[:5],
            'pending_schedules': pending_schedules_display,
            'pushed_schedules': pushed_schedules_display,  # ✅ NEW
            'overdue_schedules': overdue_schedules_display,
            'in_progress_schedules': in_progress_schedules_display,
            'completed_schedules': completed_schedules[:5],

            'schedule_counts': {
                'pending': pending_count,
                'pushed': pushed_count,  # ✅ NEW
                'overdue': overdue_count,
                'in_progress': in_progress_count,
                'completed': completed_count,
            },

            'recent_sessions': recent_calibrations[:10],
            'recent_failures': recent_failures,
            'notifications': notifications,

            'week_pass_rate': round(week_pass_rate, 1),
            'month_pass_rate': round(month_pass_rate, 1),
            'quarter_pass_rate': round(quarter_pass_rate, 1),
            'week_total': week_total,
            'month_total': month_total,
            'quarter_total': quarter_total,

            'total_procedures': CalibrationProcedure.objects.filter(active_status=True).count(),
            'total_sessions': CalibrationSession.objects.filter(status='approved').count(),
            'total_schedules': CalibrationSchedule.objects.count(),
            'total_equipment': total_equipment,
            'equipment_needing_calibration': equipment_needing_calibration,

            'workshop_stats': workshop_stats,
            'top_procedures': top_procedures,
            'alerts': alerts,

            'current_month': current_date.strftime('%B %Y'),
            'current_date': current_date,
            'show_sidebar': True,
        }

        return render(request, 'Calibrition/calsoft_dashboard.html', context)

    except Exception as e:
        logger.error(
            f"Error loading CalSoft dashboard: {str(e)}", exc_info=True)
        messages.error(request, f"Error loading dashboard: {str(e)}")

        greeting = generate_greeting(request.user.first_name)

        context = {
            'greeting': greeting,
            'pending_schedules': [],
            'pushed_schedules': [],  # ✅ NEW
            'overdue_schedules': [],
            'in_progress_schedules': [],
            'schedule_counts': {'pending': 0, 'pushed': 0, 'overdue': 0, 'in_progress': 0, 'completed': 0},
            'recent_calibrations': [],
            'recent_failures': [],
            'notifications': [],
            'week_pass_rate': 0,
            'month_pass_rate': 0,
            'quarter_pass_rate': 0,
            'total_procedures': 0,
            'total_sessions': 0,
            'total_schedules': 0,
            'alerts': [],

        }
        return render(request, 'Calibrition/calsoft_dashboard.html', context)

@login_required
@require_http_methods(["GET"])
def api_dashboard_metrics(request):
    """Enhanced API endpoint for dashboard metrics with real-time updates (approved sessions only)."""
    try:
        current_date = timezone.now().date()
        first_day_of_month = current_date.replace(day=1)
        last_day_of_month = first_day_of_month + \
            relativedelta(months=1) - relativedelta(days=1)
        week_ago = timezone.now() - timedelta(days=7)
        month_ago = timezone.now() - timedelta(days=30)

        # Define the statuses to be treated as pending (SAME AS DASHBOARD)
        pending_statuses = ['pending', 'pushed']

        # Schedule queries - FILTERED for active equipment only (SAME AS DASHBOARD)
        pending_count = CalibrationSchedule.objects.filter(
            status='pending',  # Only 'pending' status
            scheduled_month__range=(first_day_of_month, last_day_of_month),
            equipment__active_status=True
        ).count()

        # ✅ NEW: Pushed schedules count for current month
        pushed_count = CalibrationSchedule.objects.filter(
            status='pushed',  # Only 'pushed' status
            scheduled_month__range=(first_day_of_month, last_day_of_month),
            equipment__active_status=True
        ).count()

        # ✅ FIXED: Correct overdue calculation (MIRRORS DASHBOARD LOGIC EXACTLY)
        overdue_schedules = CalibrationSchedule.objects.filter(
            status__in=pending_statuses,
            equipment__active_status=True
        ).select_related('equipment', 'workshop', 'calibration_procedure')

        # Filter these in Python to avoid complex annotations (SAME AS DASHBOARD)
        overdue_schedules_list = []
        for schedule in overdue_schedules:
            if schedule.scheduled_month:
                # Get the last day of the scheduled month
                scheduled_month_year = schedule.scheduled_month.year
                scheduled_month_month = schedule.scheduled_month.month

                # Get first day of next month, subtract 1 day to get last day of current month
                if scheduled_month_month == 12:
                    next_month_year = scheduled_month_year + 1
                    next_month_month = 1
                else:
                    next_month_year = scheduled_month_year
                    next_month_month = scheduled_month_month + 1

                from datetime import date
                last_day_of_scheduled_month = date(
                    next_month_year,
                    next_month_month,
                    1
                ) - timedelta(days=1)

                # Schedule is overdue if last day of its month has passed
                if last_day_of_scheduled_month < current_date:
                    overdue_schedules_list.append(schedule)

        overdue_count = len(overdue_schedules_list)

        in_progress_count = CalibrationSchedule.objects.filter(
            status='in_progress',
            scheduled_month__range=(first_day_of_month, last_day_of_month),
            equipment__active_status=True
        ).count()

        # ✅ FIXED: Completed count - filter by completed_date, not scheduled_month
        completed_count = CalibrationSchedule.objects.filter(
            status='completed',
            completed_date__range=(first_day_of_month, last_day_of_month),  # Changed from scheduled_month
            equipment__active_status=True
        ).count()

        # 🔹 Performance metrics (approved only) - SAME AS DASHBOARD
        week_sessions = CalibrationSession.objects.filter(
            status='approved', timestamp__gte=week_ago)
        week_total = week_sessions.count()
        week_passed = week_sessions.filter(overall_pass=True).count()
        week_pass_rate = (week_passed / week_total * 100) if week_total > 0 else 0

        month_sessions = CalibrationSession.objects.filter(
            status='approved', timestamp__gte=month_ago)
        month_total = month_sessions.count()
        month_passed = month_sessions.filter(overall_pass=True).count()
        month_pass_rate = (month_passed / month_total * 100) if month_total > 0 else 0

        # Equipment metrics - SAME AS DASHBOARD
        total_equipment = Equipment.objects.filter(active_status=True).count()
        equipment_needing_calibration = CalibrationSchedule.objects.filter(
            status__in=pending_statuses,
            equipment__active_status=True
        ).values('equipment').distinct().count()

        # 🔹 Recent activities (approved only) - SAME AS DASHBOARD
        recent_sessions = CalibrationSession.objects.filter(
            status='approved'
        ).select_related(
            'procedure', 'performed_by'
        ).order_by('-timestamp')[:5]

        session_data = []
        for session in recent_sessions:
            # Add equipment info if available (SAME AS DASHBOARD)
            equipment = None
            if session.device_serial:
                try:
                    equipment = Equipment.objects.select_related('department').get(
                        serial_number=session.device_serial,
                        active_status=True
                    )
                except Equipment.DoesNotExist:
                    pass

            session_data.append({
                'id': session.id,
                'certificate_number': session.certificate_number,
                'device_model': session.device_model,
                'device_serial': session.device_serial,
                'device_description': str(session.device_description) if session.device_description else None,
                'procedure_name': session.procedure.name if session.procedure else 'Unknown',
                'performed_by': session.performed_by.username if session.performed_by else 'Unknown',
                'timestamp': session.timestamp.isoformat(),
                'overall_pass': session.overall_pass,
                'equipment_department': equipment.department.name if equipment and equipment.department else None,
            })

        # Notifications (SAME AS DASHBOARD)
        notifications = CalibrationNotification.objects.filter(
            recipient=request.user,
            is_read=False
        ).values('id', 'notification_type', 'title', 'message', 'created_at', 'action_url')[:10]

        notifications_data = [{
            'id': n['id'],
            'type': n['notification_type'],
            'title': n['title'],
            'message': n['message'],
            'created_at': n['created_at'].isoformat() if n['created_at'] else None,
            'action_url': n['action_url'],
        } for n in notifications]

        # Build response data (MIRRORS DASHBOARD CONTEXT)
        data = {
            'success': True,
            'timestamp': timezone.now().isoformat(),
            'metrics': {
                'schedule_counts': {
                    'pending': pending_count,
                    'pushed': pushed_count,  # ✅ NEW
                    'overdue': overdue_count,
                    'in_progress': in_progress_count,
                    'completed': completed_count,
                },
                'performance': {
                    'week_pass_rate': round(week_pass_rate, 1),
                    'month_pass_rate': round(month_pass_rate, 1),
                    'week_total': week_total,
                    'month_total': month_total,
                },
                'equipment': {
                    'total': total_equipment,
                    'needing_calibration': equipment_needing_calibration,
                },
                'recent_sessions': session_data,
                'notifications': notifications_data,
            }
        }

        return JsonResponse(data)

    except Exception as e:
        logger.error(
            f"Error in api_dashboard_metrics: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e),
            'timestamp': timezone.now().isoformat()
        }, status=500)

def generate_greeting(user_first_name):
    # Force Nairobi timezone
    local_time = timezone.localtime(timezone.now(), ZoneInfo("Africa/Nairobi"))
    hour = local_time.hour

    if 5 <= hour < 12:  # Morning
        messages = [
            f"Good morning, {user_first_name}! Wishing you a bright and productive start 🌞",
            f"Rise and shine, {user_first_name}! Let’s make today count 🚀",
            f"Morning {user_first_name}! A new day brings new opportunities ✨",
            f"Good morning, {user_first_name}! Don’t forget your coffee ☕",
            f"Hello {user_first_name}, may your morning be filled with energy and focus 💡",
        ]

    elif 12 <= hour < 15:  # Afternoon
        messages = [
            f"Good afternoon, {user_first_name}! I hope your day is going smoothly 🌼",
            f"Hello {user_first_name}, wishing you a productive and positive afternoon ☀️",
            f"Good afternoon, {user_first_name}! Keep up the great work, you’re doing amazing 💪",
            f"Hi {user_first_name}, hope your afternoon is filled with focus and good energy ✨",
            f"Good afternoon, {user_first_name}! Remember to take a short break and recharge ☕",
        ]

    elif 15 <= hour < 22:  # Evening
        messages = [
            f"Good evening, {user_first_name}! Hope you had a successful day 🌆",
            f"Evening vibes, {user_first_name}! Time to wrap things up strong 💼",
            f"Good evening, {user_first_name}! You’ve done great today 👏",
            f"Relax and recharge, {user_first_name}. You’ve earned it ✨",
            f"Hello {user_first_name}, may your evening be peaceful and fulfilling 🌙",
        ]

    else:  # Late night
        messages = [
            f"Burning the midnight oil, {user_first_name}? Keep pushing 🔥",
            f"Late shift hero, {user_first_name}! Stay strong 🌙",
            f"Still going strong, {user_first_name}? Much respect 🙌",
            f"Midnight hustle mode: ON, {user_first_name} ⚡",
            f"Working under the stars, {user_first_name}. Don’t forget to rest ✨",
        ]

    return random.choice(messages)


@login_required
def complete_calibration_from_session(request, session_id):
    """Complete a calibration schedule from a session, regardless of pass/fail status."""
    try:
        session = get_object_or_404(CalibrationSession, pk=session_id)

        # Find associated schedule
        schedule = None
        if session.device_serial:
            # Try to find a pending or in-progress schedule for this equipment
            try:
                equipment = Equipment.objects.get(
                    serial_number=session.device_serial)
                schedule = CalibrationSchedule.objects.filter(
                    equipment=equipment,
                    status__in=['pending', 'in_progress', 'pushed']
                ).first()
            except Equipment.DoesNotExist:
                pass

        if schedule:
            # Mark schedule as completed regardless of pass/fail status
            schedule.status = 'completed'
            schedule.completed_date = timezone.now().date()
            schedule.save()

            CalibrationAuditLog.objects.create(
                user=request.user,
                action='complete_schedule',
                description=f'Completed schedule {schedule.id} from calibration session {session.certificate_number} (Result: {"PASS" if session.overall_pass else "FAIL"})',
                schedule=schedule,
            )

            messages.success(
                request, f'Schedule completed for {schedule.equipment.description}')
        else:
            messages.warning(
                request, 'No matching schedule found to complete.')

        return redirect('calibration:cal-dashboard')

    except Exception as e:
        logger.error(
            f"Error completing calibration from session {session_id}: {str(e)}")
        messages.error(request, f"Error completing calibration: {str(e)}")
        return redirect('calibration:cal-dashboard')
@login_required
def perform_calibration_global(request):
    """
    Performing calibration with return navigation support and proper schedule handling.
    Includes enhanced rescheduling logic for equipment grouping.
    """
    if request.method == 'POST':
        try:
            # Extract and validate basic form data
            equipment_id = request.POST.get('equipment')
            procedure_id = request.POST.get('procedure')
            schedule_id = request.POST.get('schedule')

            # ✅ NEW: Get rescheduling preference
            reschedule_preference = request.POST.get('reschedule_preference', 'maintain_original')

            # ✅ Get return navigation parameters
            return_to = request.POST.get('return_to', 'dashboard')
            return_page = request.POST.get('return_page', '1')
            return_department = request.POST.get('return_department', '')
            return_month = request.POST.get('return_month', '')
            return_year = request.POST.get('return_year', '')
            return_search = request.POST.get('return_search', '')

            logger.info(f"=== CALIBRATION START ===")
            logger.info(f"User: {request.user.username}")
            logger.info(f"Equipment ID: {equipment_id}")
            logger.info(f"Procedure ID: {procedure_id}")
            logger.info(f"Schedule ID: {schedule_id}")
            logger.info(f"Reschedule preference: {reschedule_preference}")
            logger.info(f"Return to: {return_to}")

            # Validate equipment selection
            if not equipment_id:
                messages.error(request, "Equipment selection is required.")
                return redirect('schedule:pending_calibrations')

            # Get equipment and validate
            try:
                equipment = Equipment.objects.get(pk=equipment_id)
                logger.info(f"Equipment: {equipment.description} ({equipment.serial_number})")
            except Equipment.DoesNotExist:
                messages.error(request, "Selected equipment not found.")
                return redirect('schedule:pending_calibrations')

            # Determine procedure and schedule
            procedure = None
            schedule = None

            # Get procedure
            if procedure_id:
                try:
                    procedure = CalibrationProcedure.objects.get(pk=procedure_id, active_status=True)
                    logger.info(f"Procedure selected: {procedure.name}")
                except CalibrationProcedure.DoesNotExist:
                    messages.error(request, "Selected calibration procedure not found or inactive.")
                    return redirect('schedule:pending_calibrations')

            # Get schedule if provided
            original_schedule_id = schedule_id
            original_schedule = None

            if schedule_id:
                try:
                    schedule = CalibrationSchedule.objects.get(pk=schedule_id)
                    original_schedule = schedule  # Store for reference
                    if not procedure:
                        procedure = schedule.calibration_procedure
                        logger.info(f"Using procedure from schedule: {procedure.name if procedure else 'None'}")
                    equipment = schedule.equipment
                    logger.info(f"Using equipment from schedule: {equipment.description}")
                except CalibrationSchedule.DoesNotExist:
                    messages.error(request, "Selected schedule not found.")
                    return redirect('schedule:pending_calibrations')

            # Auto-determine procedure if not specified
            if not procedure:
                mapping = EquipmentCalibrationProcedure.objects.filter(
                    equipment=equipment, is_default=True
                ).first()
                if mapping:
                    procedure = mapping.calibration_procedure
                    logger.info(f"Auto-selected default procedure: {procedure.name}")
                else:
                    messages.error(
                        request,
                        "No calibration procedure found for this equipment. Please assign a default procedure first."
                    )
                    return redirect('schedule:pending_calibrations')

            # ✅ ENHANCED: Store original schedule info for audit and rescheduling
            original_schedule_info = {
                'id': original_schedule_id,
                'equipment_id': equipment.id,
                'equipment_description': equipment.description,
                'original_schedule': schedule
            }

            # Create or get schedule
            schedule_was_created = False
            if not schedule:
                if schedule_id:
                    try:
                        schedule = CalibrationSchedule.objects.get(pk=schedule_id)
                    except CalibrationSchedule.DoesNotExist:
                        pass

                if not schedule:
                    schedule = CalibrationSchedule.objects.create(
                        equipment=equipment,
                        calibration_procedure=procedure,
                        scheduled_month=timezone.now().date(),
                        status='pending',
                        description=f"Ad-hoc calibration for {equipment.description}",
                        is_ad_hoc=True  # Mark as ad-hoc for tracking
                    )
                    schedule_was_created = True
                    logger.info(f"Created ad-hoc schedule: {schedule.id}")
                    CalibrationAuditLog.objects.create(
                        user=request.user,
                        action='create_schedule',
                        description=f"Ad-hoc calibration schedule created for {equipment.description}",
                        schedule=schedule,
                    )

            # Begin atomic transaction
            with transaction.atomic():
                # Validate and create session
                session_form = CalibrationSessionForm(request.POST)
                if not session_form.is_valid():
                    logger.error(f"Session form validation failed: {session_form.errors}")
                    messages.error(request, f"Invalid session data: {session_form.errors.as_text()}")

                    context = {
                        'pending_schedules': CalibrationSchedule.objects.filter(status__in=['pending', 'pushed']),
                        'equipment_list': Equipment.objects.all(),
                        'procedures': CalibrationProcedure.objects.filter(active_status=True),
                        'form': session_form,
                        'return_to': return_to,
                        'return_page': return_page,
                        'return_department': return_department,
                        'return_month': return_month,
                        'return_year': return_year,
                        'return_search': return_search,
                        'reschedule_preference': reschedule_preference,
                        'show_sidebar': True,
                    }
                    return render(request, 'Calibrition/calibration.html', context)

                # Validate environmental conditions
                temperature = session_form.cleaned_data.get('actual_temperature')
                humidity = session_form.cleaned_data.get('actual_humidity')
                pressure = session_form.cleaned_data.get('actual_pressure')

                env_warnings = _validate_environmental_conditions(temperature, humidity, pressure, procedure)
                for warning in env_warnings:
                    messages.warning(request, warning)

                # ✅ ENHANCED: Check for existing grouped schedule before creating session
                grouped_schedule = None
                schedule_was_grouped = False

                if schedule and not schedule_was_created:
                    # Check if this schedule belongs to a group
                    grouped_schedule = _get_grouped_schedule_if_exists(schedule, equipment)
                    if grouped_schedule and grouped_schedule.id != schedule.id:
                        schedule_was_grouped = True
                        logger.info(f"Found grouped schedule: {grouped_schedule.id} for equipment {equipment.id}")

                        # Handle rescheduling based on preference
                        if reschedule_preference == 'follow_group':
                            # Use the grouped schedule instead
                            original_schedule = schedule
                            schedule = grouped_schedule
                            logger.info(f"Following group schedule: {schedule.id}")
                        elif reschedule_preference == 'maintain_original':
                            # Keep original schedule
                            logger.info(f"Maintaining original schedule: {schedule.id}")
                        elif reschedule_preference == 'create_new':
                            # Create new schedule for next period
                            new_schedule = _create_next_schedule(equipment, procedure)
                            if new_schedule:
                                original_schedule = schedule
                                schedule = new_schedule
                                logger.info(f"Created new schedule: {schedule.id}")

                # Log schedule details
                logger.info(f"Final schedule to use: {schedule.id if schedule else 'None'}")
                logger.info(f"Schedule was grouped: {schedule_was_grouped}")
                logger.info(f"Schedule was created: {schedule_was_created}")

                # Create calibration session
                session = CalibrationSession.objects.create(
                    procedure=procedure,
                    schedule=schedule,
                    performed_by=request.user,
                    timestamp=timezone.now(),
                    device_model=equipment.model,
                    device_serial=equipment.serial_number,
                    device_manufacturer=equipment.manufacturer,
                    device_description=equipment.description,
                    actual_temperature=temperature,
                    actual_humidity=humidity,
                    actual_pressure=pressure,
                    notes=session_form.cleaned_data.get('notes', ''),
                    status='pending_review'
                )

                # Get parameters for this procedure
                parameters = CalibrationParameter.objects.filter(procedure=procedure).order_by('order')
                logger.info(f"Found {parameters.count()} parameters for procedure")

                # Process and validate parameter-specific resolutions
                resolution_errors = []
                resolutions_created = 0
                for parameter in parameters:
                    resolution_key = f"resolution_{parameter.id}"
                    resolution_value = request.POST.get(resolution_key, '').strip()
                    logger.debug(f"Processing resolution for parameter {parameter.name}: {resolution_value}")

                    if not resolution_value:
                        resolution_errors.append(f"Missing resolution for parameter '{parameter.name}'")
                        continue

                    try:
                        resolution = Decimal(resolution_value)
                        if resolution <= 0:
                            resolution_errors.append(f"Resolution for parameter '{parameter.name}' must be positive")
                            continue

                        SessionParameterResolution.objects.create(
                            session=session,
                            parameter=parameter,
                            resolution=resolution
                        )
                        resolutions_created += 1
                        logger.debug(f"Created resolution for {parameter.name}: {resolution}")
                    except (InvalidOperation, ValueError) as e:
                        resolution_errors.append(f"Invalid resolution for parameter '{parameter.name}': {resolution_value}")

                logger.info(f"Created {resolutions_created} parameter resolutions")

                if resolution_errors:
                    for error in resolution_errors:
                        messages.error(request, error)
                        logger.error(f"Resolution error: {error}")
                    return redirect('schedule:pending_calibrations')

                # Process and validate reading data
                logger.info("=== PROCESSING READING DATA ===")
                readings_data = {}
                total_reading_inputs = 0

                # Collect all reading inputs from POST data
                for key, value in request.POST.items():
                    if key.startswith("reading_") and value.strip():
                        total_reading_inputs += 1
                        try:
                            parts = key.split("_")
                            if len(parts) >= 5:
                                _, param_id, sub_param_id, set_value_id, reading_idx = parts[:5]
                                reading_key = f"{param_id}_{sub_param_id}_{set_value_id}"
                                reading_value = Decimal(value.strip())
                                readings_data.setdefault(reading_key, []).append(reading_value)
                                logger.debug(f"Collected reading: {reading_key} index {reading_idx} = {reading_value}")
                        except (ValueError, InvalidOperation, IndexError) as e:
                            messages.error(request, f"Invalid reading format for key '{key}': {value}")
                            logger.error(f"Reading parsing error for {key}: {str(e)}")
                            return redirect('schedule:pending_calibrations')

                logger.info(f"Total reading inputs found: {total_reading_inputs}")
                logger.info(f"Grouped into {len(readings_data)} reading sets")

                # Enhanced validation of reading data completeness
                validation_errors = []
                expected_readings = 0
                actual_readings = 0

                for parameter in parameters:
                    sub_parameters = SubParameter.objects.filter(parameter=parameter)
                    sub_parameters = sub_parameters if sub_parameters.exists() else [None]

                    for sub_param in sub_parameters:
                        set_values = SetValue.objects.filter(parameter=parameter)
                        if sub_param:
                            set_values = set_values.filter(sub_parameter=sub_param)
                        else:
                            set_values = set_values.filter(sub_parameter=None)

                        if not set_values.exists():
                            logger.warning(f"No set values found for parameter {parameter.name}, creating default")
                            set_values = [type('obj', (object,), {'id': 'default', 'value': Decimal('0')})()]

                        for set_value in set_values:
                            expected_readings += parameter.num_readings
                            reading_key = f"{parameter.id}_{sub_param.id if sub_param else 'null'}_{set_value.id}"
                            readings = readings_data.get(reading_key, [])
                            actual_readings += len(readings)

                            if len(readings) < parameter.num_readings:
                                error_msg = f"Insufficient readings for parameter '{parameter.name}'"
                                if sub_param:
                                    error_msg += f" ({sub_param.name})"
                                if hasattr(set_value, 'value'):
                                    error_msg += f" at set value {set_value.value}"
                                error_msg += f": {len(readings)} provided, {parameter.num_readings} required"
                                validation_errors.append(error_msg)
                                logger.error(f"Validation error: {error_msg}")

                logger.info(f"Validation summary: Expected {expected_readings} readings, got {actual_readings}")

                if validation_errors:
                    for error in validation_errors:
                        messages.error(request, error)
                    logger.error("Reading validation failed, aborting calibration")
                    return redirect('schedule:pending_calibrations')

                # Process readings and create CalibrationReading objects
                logger.info("=== CREATING CALIBRATION READINGS ===")
                overall_pass = True
                readings_created = 0
                readings_failed = 0
                statistics_calculated = 0

                for parameter in parameters:
                    logger.info(f"Processing parameter: {parameter.name}")
                    sub_parameters = SubParameter.objects.filter(parameter=parameter).order_by('order')
                    sub_parameters = sub_parameters if sub_parameters.exists() else [None]

                    for sub_param in sub_parameters:
                        sub_param_name = sub_param.name if sub_param else "Default"
                        logger.debug(f"  Processing sub-parameter: {sub_param_name}")

                        set_values = SetValue.objects.filter(parameter=parameter).order_by('order')
                        if sub_param:
                            set_values = set_values.filter(sub_parameter=sub_param)
                        else:
                            set_values = set_values.filter(sub_parameter=None)

                        for sv in set_values:
                            try:
                                logger.debug(f"    Processing set value: {sv.value}")

                                # Get parameter resolution
                                try:
                                    resolution = SessionParameterResolution.objects.get(
                                        session=session, parameter=parameter
                                    ).resolution
                                except SessionParameterResolution.DoesNotExist:
                                    messages.error(request, f"No resolution defined for parameter '{parameter.name}'")
                                    logger.error(f"Missing resolution for parameter {parameter.name}")
                                    return redirect('schedule:pending_calibrations')

                                # Get readings for this parameter/sub-parameter/set-value combination
                                reading_key = f"{parameter.id}_{sub_param.id if sub_param else 'null'}_{sv.id}"
                                readings = readings_data.get(reading_key, [])
                                logger.debug(f"      Reading key: {reading_key}, readings: {readings}")

                                if not readings:
                                    logger.warning(f"No readings found for {reading_key}")
                                    readings_failed += 1
                                    overall_pass = False
                                    continue

                                # Create reading object
                                reading = CalibrationReading.objects.create(
                                    session=session,
                                    parameter=parameter,
                                    sub_parameter=sub_param,
                                    set_value=sv,
                                )
                                logger.debug(f"      Created CalibrationReading {reading.id}")

                                # Set individual readings
                                readings_set = 0
                                for i, val in enumerate(readings[:parameter.num_readings], 1):
                                    try:
                                        reading.set_reading(i, val)
                                        readings_set += 1
                                        logger.debug(f"        Set reading_{i} = {val}")
                                    except Exception as e:
                                        logger.error(f"Error setting reading_{i} for reading {reading.id}: {str(e)}")

                                # Calculate statistics if we have readings
                                if readings_set > 0:
                                    logger.debug(f"      Calculating statistics for {readings_set} readings")

                                    try:
                                        calculation_success = reading.calculate_statistics()
                                        if calculation_success and reading.mean is not None:
                                            logger.info(f"      Statistics calculated: mean={reading.mean}, std_dev={reading.standard_deviation}")

                                            calculator = CalibrationCalculator()
                                            readings_list = [getattr(reading, f'reading_{i}') for i in range(1, 11)
                                                           if getattr(reading, f'reading_{i}', None) is not None]

                                            if readings_list:
                                                stats = calculator.calculate_statistics([float(r) for r in readings_list])
                                                if stats:
                                                    reading.type_a_uncertainty = calculator.calculate_type_a_uncertainty(
                                                        Decimal(str(stats['std_dev'])), stats['count']
                                                    )
                                                    reading.type_b_uncertainty = calculator.calculate_type_b_uncertainty(resolution)
                                                    reading.reference_uncertainty_component = parameter.reference_uncertainty or Decimal('0')
                                                    reading.combined_uncertainty = calculator.calculate_combined_uncertainty(
                                                        reading.type_a_uncertainty,
                                                        reading.type_b_uncertainty,
                                                        reading.reference_uncertainty_component
                                                    )
                                                    reading.expanded_uncertainty = calculator.calculate_expanded_uncertainty(
                                                        reading.combined_uncertainty,
                                                        parameter.coverage_factor or 2
                                                    )
                                                    reading.save()
                                                    statistics_calculated += 1
                                                    logger.debug(f"      Uncertainties calculated and saved")

                                            if not reading.passes_tolerance:
                                                overall_pass = False
                                                logger.info(f"      Reading {reading.id} failed tolerance check")

                                            readings_created += 1
                                        else:
                                            logger.error(f"      Statistics calculation failed for reading {reading.id}")
                                            overall_pass = False
                                            readings_failed += 1
                                    except Exception as e:
                                        logger.error(f"      Error in statistics calculation: {str(e)}", exc_info=True)
                                        overall_pass = False
                                        readings_failed += 1
                                else:
                                    logger.error(f"      No readings were set for reading {reading.id}")
                                    overall_pass = False
                                    readings_failed += 1

                            except Exception as e:
                                logger.error(f"Error processing reading for parameter {parameter.name}: {str(e)}", exc_info=True)
                                messages.error(request, f"Error processing readings for parameter '{parameter.name}': {str(e)}")
                                readings_failed += 1
                                overall_pass = False

                # Verify we have proper statistics
                if readings_created == 0:
                    messages.error(request, "No valid readings were processed. Please check your input data.")
                    logger.error("No readings were successfully created")
                    return redirect('schedule:pending_calibrations')

                # Update session with results
                session.overall_pass = overall_pass
                session.save()
                logger.info(f"Session {session.certificate_number} updated with overall_pass={overall_pass}")

                # ✅ ENHANCED: Handle schedule status update with rescheduling
                schedule_update_success = False
                replacement_schedule = None
                original_schedule_deleted = False

                if schedule:
                    try:
                        # Refresh schedule from database
                        schedule.refresh_from_db()

                        # Update current schedule status
                        schedule.status = 'pending_approval'
                        schedule.completed_date = None
                        schedule.last_calibration_date = timezone.now().date()

                        # Set next due date based on calibration interval
                        # ✅ Use schedule's own calibration_period field (always available)
                        calibration_period = schedule.calibration_period or 12  # Default to 12 months
                        next_due_date = schedule.last_calibration_date + timedelta(
                            days=30 * calibration_period
                        )
                        schedule.next_due_date = next_due_date

                        schedule.save()
                        schedule_update_success = True
                        logger.info(f"Schedule {schedule.id} updated to pending_approval")

                        # ✅ Handle rescheduling for grouped equipment
                        if schedule_was_grouped and original_schedule and original_schedule.id != schedule.id:
                            # Mark original schedule as inactive since we're using grouped schedule
                            try:
                                original_schedule.refresh_from_db()
                                if reschedule_preference == 'follow_group':
                                    original_schedule.active_status = False
                                    original_schedule.status = 'grouped'
                                    original_schedule.grouped_into = schedule
                                    original_schedule.save()
                                    logger.info(f"Original schedule {original_schedule.id} marked as grouped into {schedule.id}")
                            except CalibrationSchedule.DoesNotExist:
                                original_schedule_deleted = True
                                logger.warning(f"Original schedule {original_schedule_id} was already deleted")

                    except CalibrationSchedule.DoesNotExist:
                        # Schedule was deleted during processing
                        original_schedule_deleted = True
                        logger.warning(
                            f"Schedule {schedule_id} no longer exists. Finding replacement schedule."
                        )

                        # Try to find replacement schedule
                        replacement_schedule = CalibrationSchedule.objects.filter(
                            equipment=equipment,
                            active_status=True
                        ).order_by('-scheduled_month').first()

                        if replacement_schedule:
                            logger.info(f"Found replacement schedule {replacement_schedule.id}")
                            replacement_schedule.status = 'pending_approval'
                            replacement_schedule.save()

                            # Update session to point to new schedule
                            session.schedule = replacement_schedule
                            session.save(update_fields=['schedule'])
                            schedule = replacement_schedule
                            schedule_update_success = True
                        else:
                            logger.warning(f"No replacement schedule found for equipment {equipment.id}")

                # Store historical data
                try:
                    _store_historical_data(session)
                    logger.info("Historical data stored successfully")
                except Exception as e:
                    logger.error(f"Error storing historical data: {str(e)}", exc_info=True)
                    messages.warning(request, "Calibration completed but historical data storage encountered issues.")

                # ✅ ENHANCED: Create comprehensive audit log
                try:
                    audit_schedule = schedule if schedule_update_success else None

                    # Build audit description
                    description_parts = [
                        f"Calibration completed for {equipment.description}",
                        f"(Serial: {equipment.serial_number or 'N/A'})",
                        f"Result: {'PASS' if overall_pass else 'FAIL'}",
                        f"({readings_created} readings created, {readings_failed} failed)"
                    ]

                    if schedule_was_grouped:
                        if reschedule_preference == 'follow_group':
                            description_parts.append(f"Grouped schedule used: {schedule.id}")
                        elif reschedule_preference == 'maintain_original':
                            description_parts.append(f"Original schedule maintained: {schedule.id}")
                        elif reschedule_preference == 'create_new':
                            description_parts.append(f"New schedule created: {schedule.id}")

                    if original_schedule_deleted:
                        description_parts.append("(Original schedule was deleted)")


                    audit_description = " - ".join(description_parts)

                    CalibrationAuditLog.objects.create(
                        user=request.user,
                        action='complete_calibration',
                        description=audit_description,
                        schedule=audit_schedule,
                        equipment=equipment,
                        session=session
                    )
                    logger.info("Audit log created successfully")

                except Exception as audit_error:
                    logger.error(f"Error creating audit log: {str(audit_error)}", exc_info=True)

                # Success message with details
                result_text = "PASSED" if overall_pass else "FAILED"

                schedule_status_msg = ""
                if schedule_was_grouped:
                    if reschedule_preference == 'follow_group':
                        schedule_status_msg = " Equipment grouped with existing schedule."
                    elif reschedule_preference == 'create_new':
                        schedule_status_msg = " New schedule created for next calibration."

                if original_schedule_deleted:
                    if replacement_schedule:
                        schedule_status_msg += f" Original schedule deleted, linked to schedule {replacement_schedule.id}."
                    else:
                        schedule_status_msg += " Original schedule was deleted."

                messages.success(
                    request,
                    f"Calibration session completed successfully. "
                    f"Result: {result_text} ({readings_created} readings processed). "
                    f"Certificate: {session.certificate_number}.{schedule_status_msg}",
                    extra_tags="calibration_complete"
                )

                logger.info(f"Calibration session {session.id} completed by {request.user.username} - {result_text}")
                logger.info("=== CALIBRATION END ===")

                # ✅ ENHANCED: Build return URL with filters
                if return_to == "pending":
                    params = []
                    # Use return_* param names so the pending-calibrations page
                    # can read them directly without conflicting with its own
                    # month/year/department/search filter params.
                    if return_department:
                        params.append(f"return_department={return_department}")
                    if return_month:
                        params.append(f"return_month={return_month}")
                    if return_year:
                        params.append(f"return_year={return_year}")
                    if return_search:
                        from urllib.parse import quote
                        params.append(f"return_search={quote(return_search)}")

                    # Determine which schedule ID to use for selection
                    schedule_to_select = None
                    if replacement_schedule and replacement_schedule.pk:
                        schedule_to_select = replacement_schedule.id
                    elif schedule and schedule.pk:
                        try:
                            CalibrationSchedule.objects.get(pk=schedule.pk)
                            schedule_to_select = schedule.id
                        except CalibrationSchedule.DoesNotExist:
                            pass

                    if schedule_to_select:
                        params.append(f"selected={schedule_to_select}")

                    params.append("returned_from=calibration")
                    params.append("result=" + ("pass" if overall_pass else "fail"))

                    return_url = reverse("schedule:pending_calibrations")
                    if params:
                        return_url += "?" + "&".join(params)

                    logger.info(f"Returning to pending calibrations with filters: {return_url}")
                    return redirect(return_url)
                else:
                    return redirect("schedule:pending_calibrations")

        except Exception as e:
            logger.error(
                f"Unexpected error in perform_calibration_global: {str(e)}",
                exc_info=True,
                extra={
                    'equipment_id': equipment_id if 'equipment_id' in locals() else None,
                    'procedure_id': procedure_id if 'procedure_id' in locals() else None,
                    'schedule_id': schedule_id if 'schedule_id' in locals() else None,
                    'user': request.user.username,
                }
            )
            messages.error(request, f"An unexpected error occurred during calibration: {str(e)}")
            return redirect('schedule:pending_calibrations')

    else:
        # GET request - render the calibration form
        try:
            # Handle pre-selected equipment and schedule from URL parameters
            selected_equipment = None
            selected_schedule = None
            quick_select = False

            equipment_id = request.GET.get('equipment')
            schedule_id = request.GET.get('schedule')
            quick_select = request.GET.get('quick_select') == 'true'

            # ✅ NEW: Check if equipment is part of a group
            check_grouping = request.GET.get('check_grouping', 'false') == 'true'

            # ✅ Get return navigation parameters
            # The pending-calibrations page sends these as return_* params so
            # they do not collide with other GET fields (schedule, equipment).
            # Fall back to bare names for backwards compatibility.
            return_to = request.GET.get("return_to", "dashboard")
            return_page = request.GET.get("page", "1")
            return_department = request.GET.get("return_department") or request.GET.get("department", "")
            return_month = request.GET.get("return_month") or request.GET.get("month", "")
            return_year = request.GET.get("return_year") or request.GET.get("year", "")
            return_search = request.GET.get("return_search") or request.GET.get("search", "")

            logger.info(f"Loading calibration form - Return to: {return_to}, Page: {return_page}")

            # Get selected equipment
            if equipment_id:
                try:
                    selected_equipment = Equipment.objects.select_related(
                        'description', 'department'
                    ).get(id=equipment_id)
                    logger.info(f"Selected equipment: {selected_equipment.description} (ID: {equipment_id})")
                except (Equipment.DoesNotExist, ValueError, TypeError) as e:
                    logger.error(f"Equipment with ID {equipment_id} not found: {str(e)}")
                    messages.error(request, f"Equipment with ID {equipment_id} not found")

            # Get selected schedule
            if schedule_id:
                try:
                    selected_schedule = CalibrationSchedule.objects.select_related(
                        'equipment__description',
                        'equipment__department',
                        'calibration_procedure'
                    ).get(id=schedule_id)
                    logger.info(f"Selected schedule: {selected_schedule.id} for equipment {selected_schedule.equipment.description}")

                    if not selected_equipment and selected_schedule.equipment:
                        selected_equipment = selected_schedule.equipment
                        logger.info(f"Equipment from schedule: {selected_equipment.description}")
                except (CalibrationSchedule.DoesNotExist, ValueError, TypeError) as e:
                    logger.error(f"Schedule with ID {schedule_id} not found: {str(e)}")
                    messages.error(request, f"Schedule with ID {schedule_id} not found")

            # ✅ ENHANCED: Check for grouped schedules
            grouped_schedule_info = None
            if check_grouping and selected_equipment:
                grouped_schedule = _find_grouped_schedule_for_equipment(selected_equipment)
                if grouped_schedule:
                    grouped_schedule_info = {
                        'exists': True,
                        'schedule_id': grouped_schedule.id,
                        'scheduled_month': grouped_schedule.scheduled_month,
                        'description': f"Found existing schedule for {grouped_schedule.equipment.description}"
                    }
                    logger.info(f"Grouped schedule found: {grouped_schedule.id}")
                else:
                    grouped_schedule_info = {'exists': False}
                    logger.info("No grouped schedule found")

            # Get other data for the form
            pending_schedules = CalibrationSchedule.objects.filter(
                status__in=['pending', 'pushed']
            ).select_related('equipment', 'calibration_procedure').order_by('scheduled_month')

            equipment_list = Equipment.objects.all().order_by('description')

            # Get procedures with recommendation logic
            all_procedures = CalibrationProcedure.objects.filter(active_status=True).order_by('name')
            recommended_procedure_ids = set()

            if selected_equipment and selected_equipment.description:
                sessions_with_same_description = CalibrationSession.objects.filter(
                    device_description=selected_equipment.description,
                    status='approved'
                ).select_related('procedure').values_list('procedure_id', flat=True).distinct()

                recommended_procedure_ids = set(sessions_with_same_description)
                logger.info(f"Found {len(recommended_procedure_ids)} recommended procedures for {selected_equipment.description}")

            # Separate procedures into recommended and others
            recommended_procedures = []
            other_procedures = []

            for proc in all_procedures:
                proc_dict = {
                    'id': proc.id,
                    'name': proc.name,
                    'is_recommended': proc.id in recommended_procedure_ids
                }

                if proc.id in recommended_procedure_ids:
                    recommended_procedures.append(proc_dict)
                else:
                    other_procedures.append(proc_dict)

            logger.info(f"Total procedures: {len(all_procedures)}, Recommended: {len(recommended_procedures)}, Others: {len(other_procedures)}")

            # Build context
            context = {
                'pending_schedules': pending_schedules,
                'equipment_list': equipment_list,
                'recommended_procedures': recommended_procedures,
                'other_procedures': other_procedures,
                'form': CalibrationSessionForm(),
                # Selected items
                'selected_equipment': selected_equipment,
                'selected_schedule': selected_schedule,
                'quick_select': quick_select,
                # ✅ NEW: Grouping and rescheduling
                'grouped_schedule_info': grouped_schedule_info,
                # Return navigation parameters
                'return_to': return_to,
                'return_page': return_page,
                'return_department': return_department,
                'return_month': return_month,
                'return_year': return_year,
                'return_search': return_search,
                'show_sidebar': True,
            }

            logger.info(f"Context prepared with grouping info: {grouped_schedule_info}")

            return render(request, 'Calibrition/calibration.html', context)

        except Exception as e:
            logger.error(f"Error loading calibration form: {str(e)}", exc_info=True)
            messages.error(request, f"Error loading calibration form: {str(e)}")
            return redirect('schedule:pending_calibrations')


# ✅ NEW: Helper function to find grouped schedule
def _get_grouped_schedule_if_exists(current_schedule, equipment):
    """
    Check if there's already a schedule for equipment with same description.
    Returns the grouped schedule if found, otherwise None.
    """
    try:
        # Find other equipment with same description
        same_description_equipment = Equipment.objects.filter(
            description=equipment.description,
            active_status=True
        ).exclude(id=equipment.id)

        for eq in same_description_equipment:
            # Look for active pending schedule for this equipment
            grouped_schedule = CalibrationSchedule.objects.filter(
                equipment=eq,
                status__in=['pending', 'pushed'],
                active_status=True,
                scheduled_month__gte=current_schedule.scheduled_month
            ).order_by('scheduled_month').first()

            if grouped_schedule:
                return grouped_schedule

    except Exception as e:
        logger.error(f"Error checking grouped schedule: {str(e)}")

    return None


# ✅ NEW: Helper function to create next schedule
def _create_next_schedule(equipment, procedure):
    """
    Create a new schedule for the next calibration period.
    """
    try:
        today = timezone.now().date()

        # Calculate next due date
        if procedure.interval_months:
            next_month = today + timedelta(days=30 * procedure.interval_months)
        else:
            # Default to 12 months if no interval specified
            next_month = today + timedelta(days=365)

        new_schedule = CalibrationSchedule.objects.create(
            equipment=equipment,
            calibration_procedure=procedure,
            scheduled_month=next_month,
            status='pending',
            description=f"Next calibration for {equipment.description}",
            is_grouped_schedule=True
        )

        logger.info(f"Created new schedule {new_schedule.id} for {next_month}")
        return new_schedule

    except Exception as e:
        logger.error(f"Error creating next schedule: {str(e)}")
        return None


# ✅ NEW: Helper to find grouped schedule for equipment
def _find_grouped_schedule_for_equipment(equipment):
    """
    Find if there's already a schedule for equipment with same description.
    """
    if not equipment or not equipment.description:
        return None

    try:
        # Find equipment with same description
        same_description_equipment = Equipment.objects.filter(
            description=equipment.description,
            active_status=True
        ).exclude(id=equipment.id)

        for eq in same_description_equipment:
            grouped_schedule = CalibrationSchedule.objects.filter(
                equipment=eq,
                status__in=['pending', 'pushed'],
                active_status=True
            ).order_by('scheduled_month').first()

            if grouped_schedule:
                return grouped_schedule

    except Exception as e:
        logger.error(f"Error finding grouped schedule: {str(e)}")

    return None
def calculate_statistics(self):
    """Calculate statistical values for the readings."""
    readings = []

    # Collect all non-None readings
    for i in range(1, 11):  # assuming max 10 readings
        reading = getattr(self, f'reading_{i}', None)
        if reading is not None:
            readings.append(float(reading))

    if not readings:
        # No readings available - set default values
        self.mean = Decimal('0')
        self.std_dev = Decimal('0')
        self.count = 0
        logger.warning(
            f"No readings available for CalibrationReading {self.id}")
    else:
        try:
            # Calculate basic statistics
            self.count = len(readings)
            mean_value = sum(readings) / len(readings)
            self.mean = Decimal(str(round(mean_value, 6)))

            if len(readings) > 1:
                variance = sum((x - mean_value) **
                               2 for x in readings) / (len(readings) - 1)
                self.std_dev = Decimal(str(round(math.sqrt(variance), 6)))
            else:
                self.std_dev = Decimal('0')

            # Calculate error (difference from set value)
            if self.set_value and self.set_value.value:
                self.error = self.mean - self.set_value.value
            else:
                self.error = Decimal('0')

            # Calculate uncertainties
            self._calculate_uncertainties()

            # Check tolerance
            if self.parameter and self.parameter.tolerance:
                tolerance = self.parameter.tolerance
            elif self.sub_parameter and self.sub_parameter.tolerance:
                tolerance = self.sub_parameter.tolerance
            else:
                tolerance = None

            if tolerance and self.error is not None:
                self.passes_tolerance = abs(self.error) <= tolerance
            else:
                self.passes_tolerance = True  # Default to pass if no tolerance defined

        except Exception as e:
            logger.error(
                f"Error in calculate_statistics for reading {self.id}: {str(e)}")
            # Set safe default values
            self.mean = Decimal('0')
            self.std_dev = Decimal('0')
            self.error = Decimal('0')
            self.passes_tolerance = False

    # Save the calculated values
    self.save()

# Additional helper API endpoints with better validation


def process_calibration_readings(request, session, parameters):
    """Process and validate calibration readings from POST data"""

    # Process and validate reading data
    readings_data = {}

    # Group readings by parameter and sub-parameter
    for key, value in request.POST.items():
        if key.startswith("reading_") and value.strip():
            try:
                # Parse reading key: reading_paramId_subParamId_setValueId_readingIndex
                parts = key.split("_")
                if len(parts) >= 5:
                    _, param_id, sub_param_id, set_value_id, reading_idx = parts[:5]

                    # Create unique key for this parameter/sub-parameter/set-value combination
                    reading_key = f"{param_id}_{sub_param_id}_{set_value_id}"

                    reading_value = Decimal(value.strip())
                    reading_index = int(reading_idx)

                    if reading_key not in readings_data:
                        readings_data[reading_key] = {}

                    readings_data[reading_key][reading_index] = reading_value

                    logger.debug(
                        f"Processed reading: {reading_key} index {reading_index} = {reading_value}")

            except (ValueError, InvalidOperation, IndexError) as e:
                logger.error(
                    f"Invalid reading format for key '{key}': {value} - {str(e)}")
                raise ValueError(
                    f"Invalid reading format for key '{key}': {value}")

    logger.info(
        f"Processed {len(readings_data)} reading groups from POST data")

    # Create CalibrationReading objects and process readings
    overall_pass = True
    readings_created = 0
    readings_failed = 0

    for parameter in parameters:
        sub_parameters = SubParameter.objects.filter(
            parameter=parameter).order_by('order')
        sub_parameters = sub_parameters if sub_parameters.exists() else [None]

        for sub_param in sub_parameters:
            set_values = SetValue.objects.filter(
                parameter=parameter).order_by('order')
            if sub_param:
                set_values = set_values.filter(sub_parameter=sub_param)

            for set_value in set_values:
                try:
                    # Create unique key for this combination
                    reading_key = f"{parameter.id}_{sub_param.id if sub_param else 'null'}_{set_value.id}"

                    # Get readings for this combination
                    reading_values = readings_data.get(reading_key, {})

                    if not reading_values:
                        logger.warning(f"No readings found for {reading_key}")
                        continue

                    logger.info(
                        f"Processing readings for {reading_key}: {reading_values}")

                    # Create CalibrationReading object
                    reading = CalibrationReading.objects.create(
                        session=session,
                        parameter=parameter,
                        sub_parameter=sub_param,
                        set_value=set_value,
                    )

                    # Set individual readings
                    readings_set = 0
                    for reading_idx, value in reading_values.items():
                        try:
                            if 1 <= reading_idx <= parameter.num_readings:
                                reading.set_reading(reading_idx, value)
                                readings_set += 1
                                logger.debug(
                                    f"Set reading_{reading_idx} = {value} for reading {reading.id}")
                        except Exception as e:
                            logger.error(
                                f"Error setting reading_{reading_idx} for reading {reading.id}: {str(e)}")

                    # Validate minimum readings
                    if readings_set < parameter.num_readings:
                        logger.warning(
                            f"Insufficient readings for {reading_key}: {readings_set}/{parameter.num_readings}")

                    # Calculate statistics
                    if readings_set > 0:
                        calculation_success = reading.calculate_statistics()

                        if calculation_success and reading.mean is not None:
                            logger.info(f"Statistics calculated successfully for reading {reading.id}: "
                                        f"mean={reading.mean}, std_dev={reading.standard_deviation}")

                            if not reading.passes_tolerance:
                                overall_pass = False
                                logger.info(
                                    f"Reading {reading.id} failed tolerance check")

                            readings_created += 1
                        else:
                            logger.error(
                                f"Statistics calculation failed for reading {reading.id}")
                            overall_pass = False
                            readings_failed += 1
                    else:
                        logger.error(
                            f"No readings were set for reading {reading.id}")
                        overall_pass = False
                        readings_failed += 1

                except Exception as e:
                    logger.error(
                        f"Error processing reading for {reading_key}: {str(e)}", exc_info=True)
                    readings_failed += 1
                    overall_pass = False

    logger.info(
        f"Reading processing complete: {readings_created} created, {readings_failed} failed")

    return overall_pass, readings_created, readings_failed


@require_GET
def api_schedule(request, schedule_id):
    try:
        # Validate schedule_id
        if not str(schedule_id).isdigit():
            return JsonResponse({'success': False, 'error': 'Invalid schedule ID'}, status=400)

        schedule = get_object_or_404(CalibrationSchedule, pk=int(schedule_id))
        return JsonResponse({
            'success': True,
            'equipment_id': schedule.equipment.id,
            'procedure_id': schedule.calibration_procedure.id if schedule.calibration_procedure else None,
            'equipment_description': schedule.equipment.description,
            'equipment_serial': schedule.equipment.serial_number
        })
    except (ValueError, CalibrationSchedule.DoesNotExist) as e:
        return JsonResponse({'success': False, 'error': 'Schedule not found'}, status=404)
    except Exception as e:
        logger.error(f"Error in api_schedule: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
def api_equipment_procedure(request, equipment_id):
    try:
        # Validate equipment_id
        if not str(equipment_id).isdigit():
            return JsonResponse({'success': False, 'error': 'Invalid equipment ID'}, status=400)

        mapping = EquipmentCalibrationProcedure.objects.filter(
            equipment_id=int(equipment_id), is_default=True
        ).first()

        return JsonResponse({
            'success': True,
            'procedure_id': mapping.calibration_procedure.id if mapping else None,
            'procedure_name': mapping.calibration_procedure.name if mapping else None
        })
    except (ValueError, Equipment.DoesNotExist) as e:
        return JsonResponse({'success': False, 'error': 'Equipment not found'}, status=404)
    except Exception as e:
        logger.error(f"Error in api_equipment_procedure: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

from uuid import UUID

@require_GET
def api_procedure(request, procedure_id):
    """API endpoint to fetch procedure details (supports UUIDs)."""
    try:
        # Validate UUID format
        try:
            UUID(str(procedure_id))
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid procedure ID format'}, status=400)

        procedure = CalibrationProcedure.objects.get(pk=procedure_id, active_status=True)
        parameters = procedure.parameters.all().order_by('order')

        procedure_data = {
            'id': str(procedure.id),
            'name': procedure.name,
            'description': procedure.description,
            'temperature': str(procedure.temperature) if hasattr(procedure, 'temperature') else None,
            'humidity': str(procedure.humidity) if hasattr(procedure, 'humidity') else None,
            'pressure': str(procedure.pressure) if hasattr(procedure, 'pressure') else None,
            'parameters': []
        }

        for param in parameters:
            sub_parameters = param.sub_parameters.all().order_by('order')
            param_data = {
                'id': str(param.id),
                'name': param.name,
                'unit': param.unit,
                'num_readings': param.num_readings,
                'required_readings': param.num_readings,
                'standard_reference': param.standard_reference,
                'reference_uncertainty': str(param.reference_uncertainty),
                'coverage_factor': str(param.coverage_factor),
                'tolerance': str(param.tolerance) if param.tolerance else None,
                'sub_parameters': [],
                'set_values': []
            }

            for sub_param in sub_parameters:
                sub_param_data = {
                    'id': str(sub_param.id),
                    'name': sub_param.name,
                    'tolerance': str(sub_param.tolerance) if sub_param.tolerance else None,
                    'set_values': []
                }

                sub_set_values = SetValue.objects.filter(parameter=param, sub_parameter=sub_param).order_by('order')
                for sv in sub_set_values:
                    sub_param_data['set_values'].append({
                        'id': str(sv.id),
                        'value': float(sv.value)
                    })
                param_data['sub_parameters'].append(sub_param_data)

            if not sub_parameters.exists():
                set_values = SetValue.objects.filter(parameter=param, sub_parameter=None).order_by('order')
                for sv in set_values:
                    param_data['set_values'].append({
                        'id': str(sv.id),
                        'value': float(sv.value)
                    })

            procedure_data['parameters'].append(param_data)

        return JsonResponse({'success': True, 'procedure': procedure_data})

    except CalibrationProcedure.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Procedure not found'}, status=404)
    except Exception as e:
        logger.error(f"Error in api_procedure: {str(e)}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
@require_GET
def api_schedule(request, schedule_id):
    try:
        schedule = get_object_or_404(CalibrationSchedule, pk=schedule_id)
        return JsonResponse({
            'success': True,
            'equipment_id': schedule.equipment.id,
            'procedure_id': schedule.calibration_procedure.id if schedule.calibration_procedure else None
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@login_required
def procedure_list(request):
    # Filter only active procedures
    procedures = CalibrationProcedure.objects.filter(
        active_status=True).order_by('-created_at')  # Changed here

    # Apply search filters
    search_query = request.GET.get('search', '')
    created_by = request.GET.get('created_by', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if search_query:
        procedures = procedures.filter(
            Q(name__icontains=search_query) | Q(
                description__icontains=search_query)
        )
    if created_by:
        procedures = procedures.filter(created_by_id=created_by)
    if date_from:
        procedures = procedures.filter(created_at__gte=date_from)  # Changed here
    if date_to:
        procedures = procedures.filter(created_at__lte=date_to)  # Changed here

    # Pagination
    paginator = Paginator(procedures, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'total_procedures': CalibrationProcedure.objects.filter(active_status=True).count(),
        'active_procedures': CalibrationProcedure.objects.filter(active_status=True).count(),
        'recent_procedures': CalibrationProcedure.objects.filter(
            active_status=True,
            created_at__gte=timezone.now().replace(day=1)  # Changed here
        ).count(),
        'sessions_count': CalibrationSession.objects.count(),
        'users': User.objects.all(),
        'current_filters': {
            'search': search_query,
            'created_by': created_by,
            'date_from': date_from,
            'date_to': date_to,
        },
        'show_sidebar': True,
    }
    return render(request, 'Calibrition/procedures-list.html', context)

@login_required
@require_http_methods(["GET", "POST"])
def procedure_create(request):
    if request.method == 'POST':
        form = CalibrationProcedureForm(request.POST)
        parameter_formset = ParameterFormSet(request.POST, prefix='parameters')

        if form.is_valid() and parameter_formset.is_valid():
            try:
                with transaction.atomic():
                    procedure = form.save(commit=False)
                    procedure.created_by = request.user
                    procedure.save()

                    for param_form in parameter_formset:
                        if param_form.cleaned_data and not param_form.cleaned_data.get('DELETE', False):
                            parameter = param_form.save(commit=False)
                            parameter.procedure = procedure
                            standard_id = param_form.cleaned_data.get(
                                'standard_reference')
                            if standard_id:
                                try:
                                    standard = Standard.objects.get(
                                        id=standard_id)
                                    parameter.standard_reference = standard.serial_number
                                except Standard.DoesNotExist:
                                    logger.warning(
                                        f"Standard with ID {standard_id} not found")
                                    parameter.standard_reference = ''
                            parameter.save()

                            # Get the parameter index from form prefix
                            param_prefix = param_form.prefix
                            param_index = param_prefix.split(
                                '-')[-1] if param_prefix else '0'

                            # Check if has sub-parameters
                            has_sub_parameters = request.POST.get(
                                f'parameters-{param_index}-has_sub_parameters') == 'on'

                            # Process sub-parameters if they exist
                            sub_parameters_created = []
                            if has_sub_parameters:
                                # Get sub-parameter data from POST
                                sub_param_prefix = f'parameters-{param_index}-sub_parameters'
                                total_sub_forms = request.POST.get(
                                    f'{sub_param_prefix}-TOTAL_FORMS', '0')

                                try:
                                    total_sub_forms = int(total_sub_forms)
                                    for sub_idx in range(total_sub_forms):
                                        if request.POST.get(f'{sub_param_prefix}-{sub_idx}-DELETE') != 'on':
                                            sub_name = request.POST.get(
                                                f'{sub_param_prefix}-{sub_idx}-name', '').strip()
                                            sub_tolerance = request.POST.get(
                                                f'{sub_param_prefix}-{sub_idx}-tolerance', '1.0')
                                            sub_order = request.POST.get(
                                                f'{sub_param_prefix}-{sub_idx}-order', '0')

                                            if sub_name:
                                                sub_parameter = SubParameter.objects.create(
                                                    parameter=parameter,
                                                    name=sub_name,
                                                    tolerance=Decimal(
                                                        str(sub_tolerance)),
                                                    order=int(sub_order)
                                                )
                                                sub_parameters_created.append(
                                                    (sub_idx, sub_parameter))
                                except ValueError as e:
                                    logger.error(
                                        f"Error processing sub-parameters: {str(e)}")

                            # Process set values
                            set_value_prefix = f'parameters-{param_index}-set_values'
                            total_set_forms = request.POST.get(
                                f'{set_value_prefix}-TOTAL_FORMS', '0')
                            set_values_created = 0

                            try:
                                total_set_forms = int(total_set_forms)
                                for set_idx in range(total_set_forms):
                                    if request.POST.get(f'{set_value_prefix}-{set_idx}-DELETE') != 'on':
                                        set_value = request.POST.get(
                                            f'{set_value_prefix}-{set_idx}-value', '').strip()
                                        set_order = request.POST.get(
                                            f'{set_value_prefix}-{set_idx}-order', '0')
                                        sub_param_idx = request.POST.get(
                                            f'{set_value_prefix}-{set_idx}-sub_parameter', '')

                                        if set_value:
                                            try:
                                                # Find the associated sub-parameter if specified
                                                associated_sub_param = None
                                                if sub_param_idx and sub_param_idx.isdigit():
                                                    sub_param_idx = int(
                                                        sub_param_idx)
                                                    for sub_idx, sub_param in sub_parameters_created:
                                                        if sub_idx == sub_param_idx:
                                                            associated_sub_param = sub_param
                                                            break

                                                SetValue.objects.create(
                                                    parameter=parameter,
                                                    sub_parameter=associated_sub_param,
                                                    value=Decimal(
                                                        str(set_value)),
                                                    order=int(set_order)
                                                )
                                                set_values_created += 1
                                            except (ValueError, InvalidOperation) as e:
                                                logger.error(
                                                    f"Invalid set value '{set_value}' for parameter {parameter.name}: {str(e)}")
                                                messages.error(
                                                    request, f"Invalid set value '{set_value}' for parameter {parameter.name}")
                                                return render(request, 'Calibrition/procedure_form.html', {
                                                    'form': form,
                                                    'parameter_formset': parameter_formset,
                                                    'show_sidebar': True,
                                                })
                            except ValueError as e:
                                logger.error(
                                    f"Error processing set values: {str(e)}")

                            # Ensure at least one set value was created
                            if set_values_created == 0:
                                logger.error(
                                    f"No set value data found for parameter {parameter.name}")
                                messages.error(
                                    request, f"At least one set value is required for parameter {parameter.name}")
                                return render(request, 'Calibrition/procedure_form.html', {
                                    'form': form,
                                    'parameter_formset': parameter_formset,
                                    'show_sidebar': True,
                                })

                    messages.success(
                        request, f'Procedure "{procedure.name}" created successfully.')
                    return redirect('calibration:procedure_list')
            except Exception as e:
                logger.error(
                    f"Error creating procedure: {str(e)}", exc_info=True)
                messages.error(request, f'Error creating procedure: {str(e)}')
        else:
            logger.error(f"Form errors: {json.dumps(form.errors, indent=2)}")
            logger.error(
                f"ParameterFormSet errors: {json.dumps(parameter_formset.errors, indent=2)}")
            messages.error(request, 'Please correct the errors in the form.')
    else:
        form = CalibrationProcedureForm()
        parameter_formset = ParameterFormSet(prefix='parameters')

    context = {
        'form': form,
        'parameter_formset': parameter_formset,
        'sub_param_formset_errors': [],
        'set_value_formset_errors': [],
        'show_sidebar': True,
    }
    return render(request, 'Calibrition/procedure_form.html', context)


@require_GET
def api_standard_parameters(request):
    """API endpoint to retrieve standard parameter details including uncertainty."""
    try:
        standard_id = request.GET.get('standard_id', '')
        parameter_name = request.GET.get('parameter_name', '')

        if not standard_id or not parameter_name:
            return JsonResponse({
                'success': False,
                'error': 'Both standard_id and parameter_name are required'
            }, status=400)

        # Get the standard parameter with uncertainty
        standard_parameter = StandardParameter.objects.filter(
            standard_id=standard_id,
            parameter__name=parameter_name
        ).select_related('parameter', 'standard').first()

        if standard_parameter:
            data = {
                'success': True,
                'uncertainty': str(standard_parameter.uncertainty),
                'parameter_name': standard_parameter.parameter.name,
                'standard_name': standard_parameter.standard.name,
                'standard_serial': standard_parameter.standard.serial_number
            }
        else:
            # If no specific parameter found, return default uncertainty
            data = {
                'success': True,
                'uncertainty': '0.001',  # Default uncertainty value
                'parameter_name': parameter_name,
                'standard_name': None,
                'standard_serial': None
            }

        return JsonResponse(data)

    except Exception as e:
        logger.error(
            f"Error in api_standard_parameters: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@require_GET
def api_parameters(request):
    name = request.GET.get('name', '')
    parameters = Parameter.objects.filter(active_status=True)
    if name:
        parameters = parameters.filter(name=name)
    data = {
        'parameters': [
            {
                'name': param.name,
                'unit': param.unit
            }
            for param in parameters
        ]
    }
    return JsonResponse(data)


@require_GET
@require_GET
def api_standards(request):
    parameter_name = request.GET.get('parameter_name', '')
    standard_id = request.GET.get('standard_id', '')
    standards = Standard.objects.filter(
        standardparameter__parameter__name=parameter_name).distinct()
    if standard_id:
        standards = standards.filter(id=standard_id)
    data = {
        'standards': [
            {
                'id': standard.id,
                'serial_number': standard.serial_number,
                'name': standard.name
            }
            for standard in standards
        ]
    }
    return JsonResponse(data)


def procedure_detail(request, pk):
    """Render the procedure detail page with all necessary data."""
    try:
        procedure = get_object_or_404(CalibrationProcedure, pk=pk)

        # Get recent sessions - make sure to use the correct related_name
        recent_sessions = getattr(procedure, 'sessions', getattr(
            procedure, 'calibrationsession_set', None))
        if recent_sessions:
            recent_sessions = recent_sessions.all().order_by('-timestamp')[:10]
        else:
            recent_sessions = []

        # Prepare parameters with prefetch_related for better performance
        parameters = []
        for param in procedure.parameters.all().prefetch_related('set_values', 'sub_parameters__set_values'):
            # Main set values (where sub_parameter is None)
            main_set_values = param.set_values.filter(sub_parameter=None)

            param_data = {
                'id': param.id,
                'name': param.name,
                'unit': param.unit,
                'num_readings': param.num_readings,
                'standard_reference': param.standard_reference,
                'reference_uncertainty': param.reference_uncertainty,
                'coverage_factor': param.coverage_factor,
                'tolerance': param.tolerance,
                'main_set_values': main_set_values,
                'sub_parameters': []
            }

            # Process sub-parameters
            for sub_param in param.sub_parameters.all():
                sub_param_data = {
                    'id': sub_param.id,
                    'name': sub_param.name,
                    'tolerance': sub_param.tolerance,
                    'order': sub_param.order,
                    'set_values': sub_param.set_values.all()  # If you have this relation
                }
                param_data['sub_parameters'].append(sub_param_data)

            parameters.append(param_data)

        context = {
            'procedure': procedure,
            'recent_sessions': recent_sessions,
            'parameters': parameters,
            'show_sidebar': True,
        }

        return render(request, 'Calibrition/procedure_detail.html', context)

    except Exception as e:
        logger.error(f"Error in procedure_detail: {str(e)}", exc_info=True)

        return render(request, 'Calibrition/procedure_detail.html', {'error': str(e), 'show_sidebar': True}, status=500)


@login_required
@require_http_methods(["GET", "POST"])
def procedure_edit(request, pk):
    """Edit an existing calibration procedure."""
    procedure = get_object_or_404(CalibrationProcedure, pk=pk, active_status=True)

    if request.method == 'POST':
        form = CalibrationProcedureForm(request.POST, instance=procedure)
        parameter_formset = ParameterFormSet(
            request.POST, prefix='parameters', instance=procedure)

        if form.is_valid() and parameter_formset.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    parameters = parameter_formset.save(commit=False)

                    for param in parameters:
                        param.procedure = procedure
                        param.save()

                        # Handle deleted parameters
                        for param_to_delete in parameter_formset.deleted_objects:
                            param_to_delete.delete()

                    messages.success(
                        request, f'Procedure "{procedure.name}" updated successfully.')
                    return redirect('calibration:procedure_detail', pk=procedure.pk)
            except Exception as e:
                messages.error(request, f'Error updating procedure: {str(e)}')
        else:
            messages.error(request, 'Please correct the errors in the form.')
    else:
        form = CalibrationProcedureForm(instance=procedure)
        parameter_formset = ParameterFormSet(
            prefix='parameters', instance=procedure)

        # Pre-populate the form with existing data
        for param_form in parameter_formset:
            param_instance = param_form.instance
            if param_instance.pk:
                # Only try to access related fields if the parameter exists in DB
                param_form.sub_parameters = SubParameter.objects.filter(
                    parameter=param_instance)
                param_form.set_values = SetValue.objects.filter(
                    Q(parameter=param_instance) & Q(sub_parameter=None)
                )

    context = {
        'form': form,
        'parameter_formset': parameter_formset,
        'procedure': procedure,
        'show_sidebar': True,
    }

    return render(request, 'Calibrition/procedure_edit.html', context)


@login_required
@require_http_methods(["POST"])
def procedure_delete(request, pk):
    """Delete a calibration procedure with pending delete logic"""
    try:
        procedure = get_object_or_404(CalibrationProcedure, pk=pk)
        procedure_name = procedure.name

        # Check if procedure has pending_delete field
        if hasattr(procedure, 'pending_delete'):
            # Mark for deletion (pending sync to HQ)
            procedure.pending_delete = True
            procedure.updated_at = timezone.now()
            procedure.save(update_fields=['pending_delete', 'updated_at'])

            CalibrationAuditLog.objects.create(
                user=request.user,
                action='procedure_pending_delete',
                description=f"Procedure '{procedure_name}' marked for deletion (pending sync to HQ) by {request.user.username}"
            )
            messages.success(
                request,
                f"Procedure '{procedure_name}' marked for deletion. It will be removed after syncing with HQ.",
                extra_tags="procedure delete pending success"
            )
        else:
            # Fallback to original soft delete if pending_delete field doesn't exist
            procedure.is_active = False
            procedure.modified_date = timezone.now()
            procedure.save(update_fields=['is_active', 'modified_date'])

            CalibrationAuditLog.objects.create(
                user=request.user,
                action='procedure_soft_delete',
                description=f"Procedure '{procedure_name}' soft-deleted (marked inactive) by {request.user.username}"
            )
            messages.success(
                request,
                f"Procedure '{procedure_name}' deleted successfully.",
                extra_tags="procedure delete success",
            )

        return redirect('calibration:procedure_list')

    except Exception as e:
        logger.error(f"Error deleting procedure {pk} for user {request.user.username}: {e}")
        messages.error(
            request,
            f"Error deleting procedure: {str(e)}",
            extra_tags="procedure delete error",
        )
        return redirect('calibration:procedure_list')


@login_required
def session_detail(request, pk):
    """Display detailed results of a calibration session with enhanced Department handling."""
    # Fix: Use the correct related names from the Equipment model
    session = get_object_or_404(
        CalibrationSession.objects.select_related(
            'procedure', 'performed_by'),  # Remove 'Department' from here since it's not a direct field
        pk=pk
    )
    readings = session.readings.select_related(
        'parameter', 'sub_parameter', 'set_value'
    ).order_by('parameter__order', 'sub_parameter__order', 'set_value__order')

    # Get Department information
    Department_info = {
        'name': "Unknown Department",
        'workshop': "Unknown Workshop"
    }

    # First try to get from session's Department field
    if session.Department:
        Department_info = {
            'name': session.Department.name,
            'workshop': session.Department.workshop.name if session.Department.workshop else "Unknown Workshop"
        }
    # Fallback to equipment lookup if Department is None
    elif session.device_serial:
        try:
            # Fix: Use 'department' (lowercase) for the Equipment model
            equipment = Equipment.objects.select_related(
                'department', 'department__workshop'  # Changed to lowercase 'department'
            ).get(serial_number=session.device_serial)

            if equipment.department:  # Changed to lowercase 'department'
                Department_info = {
                    'name': equipment.department.name,  # Changed to lowercase 'department'
                    'workshop': equipment.department.workshop.name if equipment.department.workshop else "Unknown Workshop"
                }
                # Update session with Department info if missing
                if not session.Department:
                    session.Department = equipment.department  # Changed to lowercase 'department'
                    session.save()
        except Equipment.DoesNotExist:
            pass

    # Organize readings by parameter
    readings_by_parameter = {}
    parameter_stats = {}
    for reading in readings:
        param_name = reading.parameter.name
        sub_param_name = reading.sub_parameter.name if reading.sub_parameter else 'Default'
        key = f"{param_name}_{sub_param_name}"

        if key not in readings_by_parameter:
            readings_by_parameter[key] = []
            parameter_stats[key] = {
                'parameter': reading.parameter,
                'sub_parameter': reading.sub_parameter,
                'unit': reading.parameter.unit,
                'readings_count': 0,
                'passed_count': 0,
                'failed_count': 0,
                'max_error': None,
                'avg_uncertainty': None,
                'error_values': []
            }

        readings_by_parameter[key].append(reading)
        parameter_stats[key]['readings_count'] += 1

        if reading.passes_tolerance:
            parameter_stats[key]['passed_count'] += 1
        else:
            parameter_stats[key]['failed_count'] += 1

        if reading.error is not None:
            parameter_stats[key]['error_values'].append(float(reading.error))
            current_max = abs(float(reading.error))
            if (parameter_stats[key]['max_error'] is None or
                    current_max > parameter_stats[key]['max_error']):
                parameter_stats[key]['max_error'] = current_max

    # Calculate average uncertainty for each parameter
    for key in parameter_stats:
        uncertainties = [float(r.expanded_uncertainty) for r in readings_by_parameter[key]
                         if r.expanded_uncertainty is not None]
        if uncertainties:
            parameter_stats[key]['avg_uncertainty'] = sum(
                uncertainties) / len(uncertainties)

    # Calculate linearity analysis
    linearity_analysis = _calculate_linearity_analysis(readings_by_parameter)

    # Enhanced drift analysis
    drift_analysis = None
    if session.device_serial:
        drift_data = DriftAnalyzer.analyze_drift(
            session.device_serial, session.timestamp)
        if drift_data:
            drift_analysis = {
                'trend': drift_data.get('trend', 'No significant drift detected'),
                'recommendation': drift_data.get('recommendation', 'No action required'),
                'parameters': [],
                'stability_index': drift_data.get('stability_index', None),
                'confidence_level': drift_data.get('confidence_level', None),
                'historical_data_points': drift_data.get('historical_data_points', 0)
            }

            # Add parameter-specific drift info
            for param_data in drift_data.get('parameter_drifts', []):
                param_key = f"{param_data['parameter_name']}_{param_data.get('sub_parameter_name', 'Default')}"
                if param_key in parameter_stats:
                    drift_analysis['parameters'].append({
                        'name': param_data['parameter_name'],
                        'sub_parameter': param_data.get('sub_parameter_name'),
                        'drift_rate': param_data.get('drift_rate'),
                        'drift_direction': param_data.get('drift_direction'),
                        'confidence': param_data.get('confidence'),
                        'historical_readings': param_data.get('historical_readings', 0)
                    })

    # Get standards used in this procedure
    standards_used = []
    if session.procedure:
        standards_used = session.procedure.get_standards_used()

    # Prepare context
    context = {
        'session': session,
        'Department': Department_info,
        'readings_by_parameter': readings_by_parameter,
        'parameter_stats': parameter_stats,
        'linearity_analysis': linearity_analysis,
        'drift_analysis': drift_analysis,
        'standards_used': standards_used,
        'device_description': session.device_description,
        'related_equipment': Equipment.objects.filter(serial_number=session.device_serial).first(),
        'show_sidebar': True,
    }

    return render(request, 'Calibrition/session_detail.html', context)
@login_required
def assign_procedure_to_schedule(request, schedule_id):
    """Assign a calibration procedure to a specific schedule."""
    schedule = get_object_or_404(CalibrationSchedule, pk=schedule_id)

    if request.method == 'POST':
        form = CalibrationScheduleForm(request.POST, instance=schedule)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    procedure_name = schedule.calibration_procedure.name if schedule.calibration_procedure else 'None'
                    CalibrationAuditLog.objects.create(
                        user=request.user,
                        action='assign_procedure',
                        description=f'Assigned procedure {procedure_name} to schedule {schedule.id}',
                        schedule=schedule,

                    )

                    messages.success(
                        request, f'Procedure assigned to schedule successfully.')
                    return redirect('calibration:cal-dashboard')
            except Exception as e:
                messages.error(request, f'Error assigning procedure: {str(e)}')
    else:
        form = CalibrationScheduleForm(instance=schedule)

    context = {
        'schedule': schedule,
        'form': form,
        'show_sidebar': True,
    }

    return render(request, 'Calibration/assign_procedure.html', context)


@login_required
def auto_assign_procedures(request):
    """Automatically assign procedures to pending schedules based on equipment descriptions."""
    if request.method == 'POST':
        try:
            with transaction.atomic():
                schedules = CalibrationSchedule.objects.filter(
                    status='pending', calibration_procedure__isnull=True)
                assigned_count = 0

                for schedule in schedules:
                    default_procedure = EquipmentCalibrationProcedure.objects.filter(
                        equipment=schedule.equipment,
                        is_default=True
                    ).first()

                    if default_procedure:
                        schedule.calibration_procedure = default_procedure.calibration_procedure
                        schedule.estimated_duration = default_procedure.estimated_duration
                        schedule.save()
                        assigned_count += 1

                        CalibrationAuditLog.objects.create(
                            user=request.user,
                            action='auto_assign_procedure',
                            description=f'Auto-assigned procedure {default_procedure.calibration_procedure.name} to schedule {schedule.id} ({schedule.description})',
                            schedule=schedule,

                        )

                messages.success(
                    request, f'Successfully assigned procedures to {assigned_count} schedules.')
                return redirect('calibration:cal-dashboard')
        except Exception as e:
            messages.error(request, f'Error during auto-assignment: {str(e)}')

    context = {
        'pending_schedules_count': CalibrationSchedule.objects.filter(status='pending', calibration_procedure__isnull=True).count(),
        'show_sidebar': True,
    }

    return render(request, 'Calibration/auto_assign_procedures.html', context)


@login_required
def equipment_procedure_mapping(request):
    """Manage mappings between equipment and calibration procedures."""
    mappings = EquipmentCalibrationProcedure.objects.select_related(
        'equipment', 'calibration_procedure').all()

    if request.method == 'POST':
        form = EquipmentProcedureMappingForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    mapping = form.save(commit=False)
                    mapping.created_by = request.user
                    mapping.save()
                    form.save_m2m()

                    CalibrationAuditLog.objects.create(
                        user=request.user,
                        action='create_equipment_procedure_mapping',
                        description=f'Created mapping for {mapping.equipment.description} with procedure {mapping.calibration_procedure.name}',
                        schedule=None,

                    )

                    messages.success(
                        request, 'Equipment-procedure mapping created successfully.')
                    return redirect('calibration:equipment_procedure_mapping')
            except Exception as e:
                messages.error(request, f'Error creating mapping: {str(e)}')
    else:
        form = EquipmentProcedureMappingForm()

    context = {
        'mappings': mappings,
        'form': form,
        'show_sidebar': True,
    }

    return render(request, 'Calibration/equipment_procedure_mapping.html', context)


@login_required
def calibration_workflow_view(request, schedule_id):
    """Display and manage the workflow steps for a calibration schedule."""
    schedule = get_object_or_404(CalibrationSchedule, pk=schedule_id)
    workflow_steps = CalibrationWorkflow.objects.filter(
        schedule=schedule).order_by('step_order')

    context = {
        'schedule': schedule,
        'workflow_steps': workflow_steps,
        'show_sidebar': True,
    }

    return render(request, 'Calibration/workflow_view.html', context)


@login_required
@require_http_methods(["POST"])
def update_workflow_step(request, step_id):
    """Update the status of a calibration workflow step."""
    workflow_step = get_object_or_404(CalibrationWorkflow, pk=step_id)

    try:
        with transaction.atomic():
            status = request.POST.get('status')
            valid_statuses = [
                choice[0] for choice in CalibrationWorkflow._meta.get_field('status').choices]
            if status in valid_statuses:
                workflow_step.status = status
                if status == 'in_progress' and not workflow_step.started_at:
                    workflow_step.started_at = timezone.now()
                elif status in ['completed', 'skipped', 'failed'] and not workflow_step.completed_at:
                    workflow_step.completed_at = timezone.now()
                workflow_step.notes = request.POST.get('notes', '')
                workflow_step.assigned_to = request.user
                workflow_step.save()

                CalibrationAuditLog.objects.create(
                    user=request.user,
                    action='update_workflow_step',
                    description=f'Updated workflow step {workflow_step.step_name} to status {status} for schedule {workflow_step.schedule.id}',
                    schedule=workflow_step.schedule,

                )

                messages.success(
                    request, f'Workflow step {workflow_step.step_name} updated successfully.')
                return redirect('calibration:calibration_workflow_view', schedule_id=workflow_step.schedule.id)
            else:
                messages.error(request, 'Invalid status provided.')
    except Exception as e:
        messages.error(request, f'Error updating workflow step: {str(e)}')

    return redirect('calibration:calibration_workflow_view', schedule_id=workflow_step.schedule.id)


@login_required
@require_http_methods(["GET"])
def api_dashboard_data(request):
    """API endpoint to provide data for the CalSoft dashboard."""
    try:
        current_date = timezone.now().date()
        first_day_of_month = current_date.replace(day=1)
        last_day_of_month = first_day_of_month + \
            relativedelta(months=1) - relativedelta(days=1)

        pending_count = CalibrationSchedule.objects.filter(
            status__in=['pending', 'pushed'],).count()
        overdue_count = CalibrationSchedule.objects.filter(
            status__in=['pending', 'pushed'],
            scheduled_month__range=(first_day_of_month, last_day_of_month)
        ).count()
        in_progress_count = CalibrationSchedule.objects.filter(
            status='in_progress').count()
        completed_count = CalibrationSchedule.objects.filter(
            status='completed',
            scheduled_month__range=(first_day_of_month, last_day_of_month)
        ).count()

        recent_notifications = CalibrationNotification.objects.filter(
            recipient=request.user,
            is_read=False
        ).values('id', 'notification_type', 'title', 'message', 'created_at', 'action_url')[:5]

        recent_sessions = CalibrationSession.objects.select_related(
            'procedure', 'performed_by'
        ).order_by('-timestamp')[:5]

        session_data = []
        for session in recent_sessions:
            session_data.append({
                'id': session.id,
                'certificate_number': session.certificate_number,
                'device_model': session.device_model,
                'device_serial': session.device_serial,
                'procedure_name': session.procedure.name if session.procedure else 'Unknown',
                'performed_by': session.performed_by.username if session.performed_by else 'Unknown',
                'timestamp': session.timestamp.isoformat(),
                'overall_pass': session.overall_pass,
                'status': 'Completed' if session.overall_pass else 'Failed'
            })

        data = {
            'success': True,
            'dashboard': {
                'schedule_counts': {
                    'pending': pending_count,
                    'overdue': overdue_count,
                    'in_progress': in_progress_count,
                    'completed': completed_count,
                },
                'recent_notifications': [
                    {
                        'id': n['id'],
                        'type': n['notification_type'],
                        'title': n['title'],
                        'message': n['message'],
                        'created_at': n['created_at'].isoformat() if n['created_at'] else None,
                        'action_url': n['action_url'],
                    } for n in recent_notifications
                ],
                'recent_sessions': session_data,
            }
        }
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_equipment_status(request):
    """API endpoint to provide equipment calibration status."""
    try:
        current_date = timezone.now().date()
        equipment_status = []

        equipments = Equipment.objects.all()

        for equipment in equipments:
            latest_schedule = CalibrationSchedule.objects.filter(
                equipment=equipment
            ).order_by('-scheduled_month').first()

            status = {
                'equipment_id': equipment.id,
                'description': equipment.description,
                'serial_number': equipment.serial_number,
                'model': equipment.model,
                'calibration_status': 'unknown',
                'next_due_date': None,
                'days_until_due': None,
            }

            if latest_schedule:
                status['calibration_status'] = latest_schedule.status
                status['next_due_date'] = latest_schedule.scheduled_month.isoformat()

                if latest_schedule.scheduled_month >= current_date:
                    days_diff = (
                        latest_schedule.scheduled_month - current_date).days
                    status['days_until_due'] = days_diff
                else:
                    status['days_until_due'] = - \
                        (current_date - latest_schedule.scheduled_month).days

            equipment_status.append(status)

        return JsonResponse({
            'success': True,
            'equipment': equipment_status,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
@require_GET
def api_procedure_detail(request, pk):
    """API endpoint to retrieve details of a calibration procedure."""
    try:
        procedure = get_object_or_404(CalibrationProcedure, pk=pk)
        parameters = [{
            'id': param.id,
            'name': param.name,
            'unit': param.unit,
            'num_readings': param.num_readings,
            'standard_reference': param.standard_reference,
            'reference_uncertainty': str(param.reference_uncertainty),
            'coverage_factor': str(param.coverage_factor),
            'tolerance': str(param.tolerance) if param.tolerance else None,
            'order': param.order,
            'sub_parameters': [{
                'id': sub.id,
                'name': sub.name,
                'tolerance': str(sub.tolerance) if sub.tolerance else None,
                'order': sub.order
            } for sub in param.sub_parameters.all()]
        } for param in procedure.parameters.all()]

        return JsonResponse({
            'success': True,
            'procedure': {
                'id': procedure.id,
                'name': procedure.name,
                'description': procedure.description,
                'temperature': str(procedure.temperature) if procedure.temperature else None,
                'humidity': str(procedure.humidity) if procedure.humidity else None,
                'pressure': str(procedure.pressure) if procedure.pressure else None,
                'parameters': parameters
            }
        })
    except Exception as e:
        logger.error(f"Error in api_procedure_detail: {str(e)}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
def api_set_values(request):
    """API endpoint to retrieve set values for a given parameter."""
    try:
        parameter_id = request.GET.get('parameter')

        # Check if parameter_id exists
        if not parameter_id:
            return JsonResponse({
                'success': False,
                'error': 'Valid Parameter ID is required'
            }, status=400)

        # Validate UUID format
        try:
            uuid.UUID(parameter_id)
        except (ValueError, AttributeError, TypeError):
            return JsonResponse({
                'success': False,
                'error': 'Invalid UUID format for Parameter ID'
            }, status=400)

        # Query set values
        set_values = SetValue.objects.filter(
            parameter_id=parameter_id
        ).select_related('sub_parameter').order_by('order', 'value')

        # Build response data
        set_values_data = [{
            'id': str(sv.id),  # Convert UUID to string for JSON
            'value': str(sv.value),
            'sub_parameter': str(sv.sub_parameter_id) if sv.sub_parameter_id else None,
            'sub_parameter_name': sv.sub_parameter.name if sv.sub_parameter else None
        } for sv in set_values]

        return JsonResponse(set_values_data, safe=False)

    except Exception as e:
        logger.error(f"Error in api_set_values: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_validate_readings(request):
    """API endpoint to validate calibration readings."""
    try:
        data = json.loads(request.body)
        readings = [Decimal(str(v))
                    for v in data.get('readings', []) if v is not None]
        parameter_id = data.get('parameter_id')
        sub_parameter_id = data.get('sub_parameter_id')

        parameter = get_object_or_404(CalibrationParameter, pk=parameter_id)
        sub_parameter = get_object_or_404(
            SubParameter, pk=sub_parameter_id) if sub_parameter_id else None

        issues = QualityAssurance.validate_readings(
            readings,
            sub_parameter if sub_parameter else parameter
        )

        return JsonResponse({
            'success': True,
            'issues': issues
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_calculate_uncertainty(request):
    """API endpoint to calculate uncertainty for a set of readings."""
    try:
        data = json.loads(request.body)
        readings = [Decimal(str(v))
                    for v in data.get('readings', []) if v is not None]
        resolution = Decimal(str(data.get('resolution', 0.001)))
        reference_uncertainty = Decimal(
            str(data.get('reference_uncertainty', 0.001)))
        coverage_factor = Decimal(str(data.get('coverage_factor', 2.0)))

        stats = CalibrationCalculator.calculate_statistics(readings)
        if stats:
            type_a = CalibrationCalculator.calculate_type_a_uncertainty(
                stats['std_dev'], stats['count'])
            type_b = CalibrationCalculator.calculate_type_b_uncertainty(
                resolution)
            combined = CalibrationCalculator.calculate_combined_uncertainty(
                type_a, type_b, float(
                    reference_uncertainty) / float(coverage_factor)
            )
            expanded = CalibrationCalculator.calculate_expanded_uncertainty(
                combined, coverage_factor)

            return JsonResponse({
                'success': True,
                'statistics': {
                    'mean': float(stats['mean']),
                    'std_dev': float(stats['std_dev']),
                    'count': stats['count']
                },
                'uncertainty': {
                    'type_a': float(type_a),
                    'type_b': float(type_b),
                    'reference': float(reference_uncertainty) / float(coverage_factor),
                    'combined': float(combined),
                    'expanded': float(expanded)
                }
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'Insufficient valid readings'
            }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@login_required
def analytics_dashboard(request):
    """Display analytics and trends dashboard."""
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)
    one_year_ago = now - timedelta(days=365)

    total_sessions = CalibrationSession.objects.count()
    sessions_30_days = CalibrationSession.objects.filter(
        timestamp__gte=thirty_days_ago
    ).count()

    passed_sessions = CalibrationSession.objects.filter(
        overall_pass=True).count()
    pass_rate = (passed_sessions / total_sessions *
                 100) if total_sessions > 0 else 0

    top_equipment = CalibrationSession.objects.values(
        'device_model', 'device_manufacturer'
    ).annotate(
        count=models.Count('id')
    ).order_by('-count')[:10]

    top_procedures = CalibrationSession.objects.values(
        'procedure__name'
    ).annotate(
        count=models.Count('id')
    ).order_by('-count')[:10]

    monthly_data = []
    for i in range(12):
        month_start = (now.replace(day=1) -
                       timedelta(days=30*i)).replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)

        month_sessions = CalibrationSession.objects.filter(
            timestamp__gte=month_start,
            timestamp__lt=next_month
        ).count()

        monthly_data.append({
            'month': month_start.strftime('%Y-%m'),
            'sessions': month_sessions
        })

    monthly_data.reverse()

    context = {
        'total_sessions': total_sessions,
        'sessions_30_days': sessions_30_days,
        'pass_rate': round(pass_rate, 1),
        'top_equipment': top_equipment,
        'top_procedures': top_procedures,
        'monthly_data': monthly_data,
        'show_sidebar': True,
    }

    return render(request, 'Calibration/analytics_dashboard.html', context)


@login_required
def import_procedures(request):
    """Import calibration procedures from a file."""
    if request.method == 'POST':
        form = TemplateImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                template_file = form.cleaned_data['template_file']
                import_options = form.cleaned_data['import_options']
                overwrite = form.cleaned_data['overwrite_existing']

                if template_file.name.endswith('.json'):
                    data = json.loads(template_file.read())
                    with transaction.atomic():
                        for proc_data in data.get('procedures', []):
                            if overwrite:
                                CalibrationProcedure.objects.filter(
                                    name=proc_data['name']).delete()

                            procedure = CalibrationProcedure.objects.create(
                                name=proc_data['name'],
                                description=proc_data.get('description', ''),
                                created_by=request.user,
                                temperature=Decimal(
                                    str(proc_data.get('temperature', 23.0))),
                                humidity=Decimal(
                                    str(proc_data.get('humidity', 50.0))),
                                pressure=Decimal(
                                    str(proc_data.get('pressure', 101.325))),
                            )

                            if 'parameters' in import_options:
                                for param_data in proc_data.get('parameters', []):
                                    parameter = CalibrationParameter.objects.create(
                                        procedure=procedure,
                                        name=param_data['name'],
                                        unit=param_data.get('unit', ''),
                                        num_readings=param_data.get(
                                            'num_readings', 5),
                                        resolution=Decimal(
                                            str(param_data.get('resolution', 0.001))),
                                        standard_reference=param_data.get(
                                            'standard_reference', ''),
                                        reference_uncertainty=Decimal(
                                            str(param_data.get('reference_uncertainty', 0.001))),
                                        coverage_factor=Decimal(
                                            str(param_data.get('coverage_factor', 2.0))),
                                        tolerance=Decimal(str(param_data.get('tolerance', 1.0))) if param_data.get(
                                            'tolerance') else None,
                                        order=param_data.get('order', 0)
                                    )

                                    if 'sub_parameters' in import_options:
                                        for sub_param_data in param_data.get('sub_parameters', []):
                                            sub_parameter = SubParameter.objects.create(
                                                parameter=parameter,
                                                name=sub_param_data['name'],
                                                resolution=Decimal(
                                                    str(sub_param_data.get('resolution', 0.001))),
                                                reference_uncertainty=Decimal(
                                                    str(sub_param_data.get('reference_uncertainty', 0.001))),
                                                coverage_factor=Decimal(
                                                    str(sub_param_data.get('coverage_factor', 2.0))),
                                                tolerance=Decimal(
                                                    str(sub_param_data.get('tolerance', 1.0))),
                                                order=sub_param_data.get(
                                                    'order', 0)
                                            )

                                            if 'set_values' in import_options:
                                                for i, value in enumerate(sub_param_data.get('set_values', [])):
                                                    SetValue.objects.create(
                                                        parameter=parameter,
                                                        sub_parameter=sub_parameter,
                                                        value=Decimal(
                                                            str(value)),
                                                        order=i
                                                    )

                                    if 'set_values' in import_options:
                                        for i, value in enumerate(param_data.get('set_values', [])):
                                            SetValue.objects.create(
                                                parameter=parameter,
                                                value=Decimal(str(value)),
                                                order=i
                                            )

                messages.success(request, 'Procedures imported successfully.')
                return redirect('calibration:procedure_list')
            except Exception as e:
                messages.error(
                    request, f'Error importing procedures: {str(e)}')
    else:
        form = TemplateImportForm()

    context = {
        'form': form,
        'show_sidebar': True,
    }

    return render(request, 'Calibrition/procedure_form.html', context)


@login_required
def standard_create(request):
    """Create a new calibration standard with associated parameters."""
    if request.method == 'POST':
        form = StandardForm(request.POST)
        formset = StandardParameterFormSet(request.POST, prefix='parameters')
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                standard = form.save(commit=False)
                standard.created_by = request.user
                standard.save()
                formset.instance = standard
                formset.save()
                messages.success(request, 'Standard created successfully.')
                return redirect('calibration:standard_create')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = StandardForm()
        formset = StandardParameterFormSet(prefix='parameters')

    parameters = Parameter.objects.all()
    context = {
        'form': form,
        'formset': formset,
        'parameters': parameters,
        'show_sidebar': True,
    }
    return render(request, 'Calibrition/standard_form.html', context)


@login_required
def parameter_create(request):
    """Create a new parameter, supporting both AJAX and non-AJAX requests."""
    if request.method == 'POST':
        form = ParameterForm(request.POST)
        if form.is_valid():
            parameter = form.save(commit=False)
            parameter.created_by = request.user
            parameter.save()
            if is_ajax(request):
                return JsonResponse({
                    'success': True,
                    'parameter': {
                        'id': parameter.pk,
                        'name': parameter.name
                    }
                })
            return redirect('calibration:standard_create')
        else:
            if is_ajax(request):
                return JsonResponse({
                    'success': False,
                    'errors': form.errors.as_json()
                }, status=400)
            messages.error(request, 'Please correct the errors below.')
    else:
        form = ParameterForm()

    context = {'form': form, 'show_sidebar': True}
    return render(request, 'Calibrition/parameter_form.html', context)


@login_required
def StandardsParameters_lists(request):
    """Display combined standards and parameters lists."""
    standards = Standard.objects.all().order_by('name')
    parameters = Parameter.objects.all().order_by('name')
    context = {
        'standards': standards,
        'parameters': parameters,
        'show_sidebar': True,
    }
    return render(request, 'Calibrition/stand-parameters-list.html', context)


@login_required
@require_http_methods(["GET", "POST"])
def standard_edit(request, pk):
    """Update an existing calibration standard."""
    standard = get_object_or_404(Standard, pk=pk)

    if request.method == 'POST':
        form = StandardForm(request.POST, instance=standard)
        formset = StandardParameterFormSet(request.POST, instance=standard, prefix='parameters')

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                standard = form.save()
                formset.save()

                # Log update
                CalibrationAuditLog.objects.create(
                    user=request.user,
                    action='update_standard',
                    description=f"Updated standard: {standard.name}"
                )

                if is_ajax(request):
                    return JsonResponse({
                        'success': True,
                        'standard': {
                            'id': str(standard.pk),
                            'name': standard.name,
                            'model_number': standard.model_number,
                            'serial_number': standard.serial_number,
                            'manufacturer': standard.manufacturer,
                            'calibration_date': standard.calibration_date.strftime('%Y-%m-%d') if standard.calibration_date else None,
                            'calibration_due_date': standard.calibration_due_date.strftime('%Y-%m-%d') if standard.calibration_due_date else None
                        }
                    })

                messages.success(request, 'Standard updated successfully.')
                return redirect('calibration:StandardsParameters_lists')
        else:
            # Convert errors to JSON-serializable format
            errors = {}
            if form.errors:
                errors['form'] = {field: [str(e) for e in errors] for field, errors in form.errors.items()}
            if formset.errors:
                errors['formset'] = []
                for idx, error_dict in enumerate(formset.errors):
                    if error_dict:
                        errors['formset'].append({
                            'index': idx,
                            'errors': {field: [str(e) for e in errors] for field, errors in error_dict.items()}
                        })

            if is_ajax(request):
                return JsonResponse({'success': False, 'errors': errors}, status=400)

            messages.error(request, 'Please correct the errors.')
    else:
        form = StandardForm(instance=standard)
        formset = StandardParameterFormSet(instance=standard, prefix='parameters')

    parameters = Parameter.objects.filter(active_status=True)
    context = {'form': form, 'formset': formset, 'parameters': parameters, 'standard': standard, 'show_sidebar': True}

    if is_ajax(request):
        return render(request, 'Calibrition/standard_form.html', context)

    return render(request, 'Calibrition/standard_form.html', context)

@login_required
@require_GET
def api_standard_parameters_detail(request, standard_id):
    """API endpoint to get standard parameter details with IDs."""
    try:
        standard = get_object_or_404(Standard, pk=standard_id)
        parameters = StandardParameter.objects.filter(standard=standard).select_related('parameter')

        data = {
            'success': True,
            'parameters': [{
                'id': str(sp.id),  # StandardParameter ID
                'parameter_id': str(sp.parameter.id),  # Parameter ID
                'parameter_name': sp.parameter.name,
                'uncertainty': str(sp.uncertainty)
            } for sp in parameters]
        }

        return JsonResponse(data)
    except Exception as e:
        logger.error(f"Error fetching standard parameters: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@login_required
def standards_list(request):
    """List all calibration standards."""
    try:
        standards = Standard.objects.all().order_by('name')
        paginator = Paginator(standards, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'page_obj': page_obj,
            'total_standards': standards.count(),
            'show_sidebar': True,
        }
        return render(request, 'Calibration/standards_list.html', context)
    except Exception as e:
        logger.error(f"Error loading standards list: {str(e)}")
        messages.error(request, f"Error loading standards: {str(e)}")
        return redirect('calibration:calsoft_dashboard')


@login_required
def session_list(request):
    """List all calibration sessions with filtering and pagination."""
    sessions = CalibrationSession.objects.select_related(
        'procedure', 'performed_by'
    ).order_by('-timestamp')

    search_form = SessionSearchForm(request.GET)
    if search_form.is_valid():
        search = search_form.cleaned_data.get('search', '')
        procedure = search_form.cleaned_data.get('procedure')
        performed_by = search_form.cleaned_data.get('performed_by')
        pass_status = search_form.cleaned_data.get('pass_status')
        date_from = search_form.cleaned_data.get('date_from')
        date_to = search_form.cleaned_data.get('date_to')

        if search:
            sessions = sessions.filter(
                Q(device_serial__icontains=search) |
                Q(device_model__icontains=search) |
                Q(certificate_number__icontains=search) |
                Q(device_description__icontains=search)
            )
        if procedure:
            sessions = sessions.filter(procedure=procedure)
        if performed_by:
            sessions = sessions.filter(performed_by=performed_by)
        if pass_status:
            sessions = sessions.filter(overall_pass=(pass_status == 'pass'))
        if date_from:
            sessions = sessions.filter(timestamp__gte=date_from)
        if date_to:
            sessions = sessions.filter(timestamp__lte=date_to)

    paginator = Paginator(sessions, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    total_sessions = sessions.count()
    passed_sessions = sessions.filter(overall_pass=True).count()
    pass_rate = (passed_sessions / total_sessions *
                 100) if total_sessions > 0 else 0

    context = {
        'page_obj': page_obj,
        'search_form': search_form,
        'total_sessions': total_sessions,
        'passed_sessions': passed_sessions,
        'pass_rate': round(pass_rate, 1),
        'show_sidebar': True,
    }

    return render(request, 'calibration/session_list.html', context)


@login_required
def standard_delete(request, pk):
    """Delete a calibration standard with pending delete logic."""
    if request.method == 'POST':
        try:
            standard = get_object_or_404(Standard, pk=pk)

            # Check if standard has pending_delete field
            if hasattr(standard, 'pending_delete'):
                # Mark for deletion (pending sync to HQ)
                standard.pending_delete = True
                standard.updated_at = timezone.now()
                standard.save(update_fields=['pending_delete', 'updated_at'])

                return JsonResponse({
                    'success': True,
                    'message': 'Standard marked for deletion. It will be removed after syncing with HQ.'
                })
            else:
                # Fallback to immediate delete if pending_delete field doesn't exist
                standard.delete()
                return JsonResponse({'success': True})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)

@login_required
def parameter_edit(request, pk):
    """Edit an existing parameter."""
    parameter = get_object_or_404(Parameter, pk=pk)
    if request.method == 'POST':
        form = ParameterForm(request.POST, instance=parameter)
        if form.is_valid():
            parameter = form.save()
            if is_ajax(request):
                return JsonResponse({
                    'success': True,
                    'parameter': {
                        'id': parameter.pk,
                        'name': parameter.name,
                        'symbol': parameter.symbol,
                        'unit': parameter.unit
                    }
                })
            messages.success(request, 'Parameter updated successfully.')
            return redirect('calibration:calibration_lists')
        else:
            if is_ajax(request):
                return JsonResponse({
                    'success': False,
                    'errors': form.errors.as_json()
                }, status=400)
            messages.error(request, 'Please correct the errors below.')
    else:
        if is_ajax(request):
            return JsonResponse({
                'name': parameter.name,
                'symbol': parameter.symbol,
                'unit': parameter.unit
            })
        form = ParameterForm(instance=parameter)
        context = {'form': form, 'show_sidebar': True}
        return render(request, 'calibration/parameter_form.html', context)


@login_required
def parameter_delete(request, pk):
    """Delete a parameter with pending delete logic."""
    if request.method == 'POST':
        try:
            parameter = get_object_or_404(Parameter, pk=pk)

            # Check if parameter has pending_delete field
            if hasattr(parameter, 'pending_delete'):
                # Mark for deletion (pending sync to HQ)
                parameter.pending_delete = True
                parameter.updated_at = timezone.now()
                parameter.save(update_fields=['pending_delete', 'updated_at'])

                return JsonResponse({
                    'success': True,
                    'message': 'Parameter marked for deletion. It will be removed after syncing with HQ.'
                })
            else:
                # Fallback to immediate delete if pending_delete field doesn't exist
                parameter.delete()
                return JsonResponse({'success': True})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)

@login_required
def trend_analysis(request):
    """Display trend analysis for calibration data."""
    context = {
        'trends': [],
        'show_sidebar': True,
    }
    return render(request, 'calibration/trend_analysis.html', context)


@login_required
def performance_analysis(request):
    """Display performance analysis for calibration activities."""
    context = {
        'performance_metrics': {},
        'show_sidebar': True,
    }
    return render(request, 'calibration/performance_analysis.html', context)


@login_required
def reports_dashboard(request):
    """Display dashboard for generating and viewing reports."""
    context = {
        'reports': [],
        'show_sidebar': True,
    }
    return render(request, 'Calibration/reports_dashboard.html', context)


@login_required
@require_http_methods(["GET"])
def api_schedule_status(request):
    """API endpoint to provide schedule status."""
    try:
        current_date = timezone.now().date()
        schedules = CalibrationSchedule.objects.select_related(
            'equipment', 'calibration_procedure').all()

        schedule_data = []
        for schedule in schedules:
            days_until_due = None
            if schedule.scheduled_month:
                if schedule.scheduled_month >= current_date:
                    days_until_due = (
                        schedule.scheduled_month - current_date).days
                else:
                    days_until_due = - \
                        (current_date - schedule.scheduled_month).days

            schedule_data.append({
                'id': schedule.id,
                'equipment_description': schedule.equipment.description,
                'serial_number': schedule.equipment.serial_number,
                'status': schedule.status,
                'scheduled_month': schedule.scheduled_month.isoformat() if schedule.scheduled_month else None,
                'days_until_due': days_until_due,
                'procedure_name': schedule.calibration_procedure.name if schedule.calibration_procedure else None,
            })

        return JsonResponse({
            'success': True,
            'schedules': schedule_data,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def backup_calibration_data(request):
    """Backup calibration data."""
    messages.success(request, 'Data backup initiated successfully.')

    return redirect('calibration:calsoft_dashboard')


def require_certificate_access(view_func):
    """
    Decorator to ensure user has the required role and permissions for certificate access.
    - Tech: must have a workshop
    - NIC: can access even without a workshop (they are tied to Departments)
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            user_profile = request.user.userprofile
            role = user_profile.role

            if role not in ["Tech", "NIC"]:
                messages.error(
                    request,
                    "Access denied. Only Technologists and In-Charge personnel can access this page."
                )
                return HttpResponseForbidden("Insufficient permissions")

            # Techs must have a workshop
            if role == "Tech" and not user_profile.workshop:
                messages.error(
                    request,
                    "No workshop assigned. Please contact your administrator."
                )
                return HttpResponseForbidden("No workshop assigned")

            # NICs don't need a workshop → they are linked to Departments
            # so no workshop validation here

        except AttributeError:
            messages.error(request, "User profile not configured properly.")
            return HttpResponseForbidden("Profile configuration error")

        return view_func(request, *args, **kwargs)

    return wrapper




# Replace your existing certificate_list function with this updated version
@login_required
@require_certificate_access
def certificate_list(request):
    """
    List all calibration certificates with filtering options and role-based access control.
    Certificates are ordered by latest first (highest certificate number).
    """
    user_profile = request.user.userprofile
    user_role = user_profile.role

    # Base queryset - NO ORDERING YET
    base_sessions = CalibrationSession.objects.select_related(
        'procedure', 'performed_by', 'device_description', 'device_manufacturer'
    ).filter(status='approved')

    # ----------------------
    # Role-based filtering
    # ----------------------
    if user_role == 'Tech':
        user_workshop = user_profile.workshop
        if not user_workshop:
            messages.error(request, "No workshop assigned to your profile. Contact admin.")
            return HttpResponseForbidden("No workshop assigned.")

        if user_workshop.category == 'calibration_center':
            sessions = base_sessions
            accessible_equipment = Equipment.objects.all()
        elif user_workshop.category == 'maintenance':
            workshop_equipment = Equipment.objects.filter(department__workshop=user_workshop)
            sessions = base_sessions.filter(
                device_serial__in=workshop_equipment.values_list('serial_number', flat=True)
            )
            accessible_equipment = workshop_equipment
        else:
            messages.error(request, f"Unknown workshop category: {user_workshop.category}")
            return HttpResponseForbidden("Invalid workshop category.")

        user_departments_qs = None

    elif user_role == 'NIC':
        if not user_profile.department:
            messages.error(request, "No department assigned to your account. Contact admin.")
            return HttpResponseForbidden("No department assigned.")

        user_departments_qs = Department.objects.filter(id=user_profile.department.id)
        accessible_equipment = Equipment.objects.filter(department=user_profile.department)
        sessions = base_sessions.filter(
            device_serial__in=accessible_equipment.values_list('serial_number', flat=True)
        )
        user_workshop = None

    else:
        messages.error(request, f"Unknown user role: {user_role}. Contact admin.")
        return HttpResponseForbidden("Invalid user role.")

    # ----------------------
    # Filters & search
    # ----------------------
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    equipment_filter = request.GET.get("equipment")
    status = request.GET.get("status")
    department_name = request.GET.get("department")

    if date_from:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d")
            sessions = sessions.filter(timestamp__date__gte=start)
        except ValueError:
            messages.warning(request, "Invalid start date format.")

    if date_to:
        try:
            end = datetime.strptime(date_to, "%Y-%m-%d")
            sessions = sessions.filter(timestamp__date__lte=end)
        except ValueError:
            messages.warning(request, "Invalid end date format.")

    if equipment_filter:
        sessions = sessions.filter(
            Q(device_description__name__icontains=equipment_filter) |
            Q(device_serial__icontains=equipment_filter) |
            Q(device_model__icontains=equipment_filter) |
            Q(device_manufacturer__name__icontains=equipment_filter) |
            Q(certificate_number__icontains=equipment_filter)
        )

    if status in ["pass", "fail"]:
        sessions = sessions.filter(overall_pass=(status == "pass"))

    if department_name:
        accessible_serials = accessible_equipment.filter(
            department__name__icontains=department_name
        ).values_list('serial_number', flat=True)
        sessions = sessions.filter(device_serial__in=accessible_serials)

    # ================================
    # APPLY ORDERING - OPTION 2: Extract numeric part from "BNH-####"
    # This handles your certificate format correctly
    # ================================

    sessions = sessions.annotate(
        # Handle null certificate numbers
        has_cert=Case(
            When(certificate_number__isnull=False, then=Value(1)),
            When(certificate_number='', then=Value(0)),
            default=Value(1),
            output_field=IntegerField()
        ),
        # Extract numeric part after "BNH-" (position 5 onwards)
        # For "BNH-0001", this extracts "0001" and converts to integer 1
        cert_numeric=Case(
            When(
                certificate_number__isnull=False,
                certificate_number__regex=r'^BNH-\d+$',
                then=Cast(
                    Substr('certificate_number', 5),  # Skip "BNH-" (4 chars + 1)
                    output_field=IntegerField()
                )
            ),
            default=Value(0),
            output_field=IntegerField()
        )
    ).order_by(
        '-has_cert',      # Sessions with certificates first
        '-cert_numeric',  # Then by certificate number (highest first)
        '-timestamp',     # Then by timestamp (newest first)
        '-id'            # Finally by ID (newest first)
    )

    # ----------------------
    # Prepare department list
    # ----------------------
    departments = accessible_equipment.select_related('department').values_list(
        'department__name', flat=True
    ).distinct().order_by('department__name')
    departments = [dept for dept in departments if dept]

    # ----------------------
    # Enhance sessions with department and due date info
    # ----------------------
    enhanced_sessions = []
    for session in sessions:
        if not hasattr(session, 'department_name') and session.device_serial:
            try:
                equipment = accessible_equipment.select_related('department').get(
                    serial_number=session.device_serial
                )
                session.department_name = equipment.department.name if equipment.department else "Unknown"
                session.workshop_name = (
                    equipment.department.workshop.name
                    if hasattr(equipment.department, 'workshop') and equipment.department.workshop
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
        else:
            session.cal_due_date = None
            session.days_until_due = None
            session.is_overdue = False

        enhanced_sessions.append(session)

    # Pagination
    paginator = Paginator(enhanced_sessions, 10 if user_role == 'NIC' else 50)
    page_number = request.GET.get('page')
    certificates_page = paginator.get_page(page_number)

    # Statistics
    total_certificates = len(enhanced_sessions)
    overdue_count = sum(1 for s in enhanced_sessions if s.is_overdue)
    due_soon_count = sum(1 for s in enhanced_sessions if s.days_until_due is not None and 0 <= s.days_until_due <= 30)
    total_equipment = accessible_equipment.count()
    equipment_with_cert = accessible_equipment.filter(
        serial_number__in=[s.device_serial for s in enhanced_sessions]
    ).distinct().count()

    today = timezone.now().date()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    context = {
        'certificates': certificates_page,
        'page_obj': certificates_page,
        'departments': departments,
        'current_filters': {
            'department': department_name,
            'date_from': date_from,
            'date_to': date_to,
            'equipment': equipment_filter or '',
            'status': status,
            'week_start': start_of_week,
            'week_end': end_of_week,
        },
        'user_workshop': user_workshop,
        'user_role': user_role,
        'access_level': 'full' if (user_role == 'Tech' and user_workshop and user_workshop.category == 'calibration_center') else 'limited',
        'user_departments': user_departments_qs if user_role == 'NIC' else None,
        'certificate_stats': {
            'total': total_certificates,
            'overdue': overdue_count,
            'due_soon': due_soon_count,
            'current': total_certificates - overdue_count - due_soon_count,
        },
        'stats': {
            'total_equipment': total_equipment,
            'total_certificates': total_certificates,
            'equipment_with_certificates': equipment_with_cert,
            'equipment_without_certificates': total_equipment - equipment_with_cert,
            'coverage_percentage': round(equipment_with_cert / total_equipment * 100, 1) if total_equipment else 0,
        },
        'show_sidebar': True,
    }

    template_name = (
        'Calibrition/department_certificates.html' if user_role == 'NIC'
        else 'Calibrition/certificate_list.html'
    )

    return render(request, template_name, context)

def get_accessible_equipment(user_profile):
    """
    Get equipment accessible to the user based on their workshop category.

    Args:
        user_profile: UserProfile object

    Returns:
        QuerySet: Equipment objects accessible to the user
    """
    user_workshop = user_profile.workshop

    if user_workshop.category == 'calibration_center':
        return Equipment.objects.all()
    elif user_workshop.category == 'maintenance':
        return Equipment.objects.filter(Department__workshop=user_workshop)
    else:
        return Equipment.objects.none()


def get_accessible_certificates(user_profile):
    """
    Get certificates accessible to the user based on their workshop category.

    Args:
        user_profile: UserProfile object

    Returns:
        QuerySet: CalibrationSession objects accessible to the user
    """
    accessible_equipment = get_accessible_equipment(user_profile)

    return CalibrationSession.objects.filter(
        status='approved',
        device_serial__in=accessible_equipment.values_list(
            'serial_number', flat=True)
    ).select_related('procedure', 'performed_by')


def get_accessible_sessions(user, date_from=None, date_to=None):
    """
    Returns CalibrationSessions the user is allowed to access
    based on role and workshop/department rules.

    Roles:
      - Tech:
          - Calibration Center → all approved sessions
          - Maintenance → only sessions for equipment in their workshop
      - NIC:
          - Only sessions for their own department
            (both direct department link + equipment-based sessions)
    """
    user_profile = user.userprofile
    user_workshop = user_profile.workshop

    base_sessions = CalibrationSession.objects.select_related(
        "procedure", "performed_by"
    ).filter(status="approved")

    # Date filter
    if date_from and date_to:
        base_sessions = base_sessions.filter(
            approved_at__date__range=(date_from, date_to))

    # ---- Tech rules ----
    if user_profile.role == "Tech":
        if not user_workshop:
            raise PermissionError("No workshop assigned to Tech profile.")

        if user_workshop.category == "calibration_center":
            sessions = base_sessions
            accessible_equipment = Equipment.objects.all()

        elif user_workshop.category == "maintenance":
            # Use lowercase 'department' field
            workshop_equipment = Equipment.objects.filter(
                department__workshop=user_workshop
            )
            sessions = base_sessions.filter(
                device_serial__in=workshop_equipment.values_list(
                    "serial_number", flat=True)
            )
            accessible_equipment = workshop_equipment

        else:
            raise PermissionError(
                f"Unknown workshop category: {user_workshop.category}")

    # ---- NIC rules ----
    elif user_profile.role == "NIC":
        # Use lowercase 'department' attribute
        user_department = getattr(user_profile, "department", None)
        if not user_department:
            raise PermissionError("No department assigned to NIC profile.")

        # Sessions explicitly tied to NIC's department (lowercase field)
        sessions = base_sessions.filter(department=user_department)

        # Sessions without department but linked via equipment in NIC's department
        nic_equipment = Equipment.objects.filter(department=user_department)
        extra_sessions = base_sessions.filter(
            device_serial__in=nic_equipment.values_list(
                "serial_number", flat=True)
        )

        # Combine both sets
        sessions = sessions.union(extra_sessions)
        accessible_equipment = nic_equipment

    else:
        raise PermissionError(
            f"Unknown role: {user_profile.role}. Only Tech and NIC roles can access certificates.")

    return sessions, accessible_equipment

@login_required
def audit_log(request):
    """Display audit log for calibration activities."""
    logs = CalibrationAuditLog.objects.select_related(
        'user', 'schedule').order_by('-timestamp')
    paginator = Paginator(logs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'total_logs': logs.count(),
        'show_sidebar': True,
    }
    return render(request, 'Calibration/audit_log.html', context)

@login_required
@require_http_methods(["GET", "POST"])
def generate_comprehensive_certificate(request, session_pk):
    """Generate a comprehensive calibration certificate with department location."""
    try:
        # Get session with related department
        session = get_object_or_404(
            CalibrationSession.objects.select_related('Department'),
            pk=session_pk
        )

        # Initialize location info
        department_name = "Unknown Department"
        workshop_name = "Unknown Workshop"

        # First try to get from session's department
        if hasattr(session, 'department') and session.department:
            department_name = session.department.name
            # Try to get workshop from department if the relationship exists
            if hasattr(session.department, 'workshop') and session.department.workshop:
                workshop_name = session.department.workshop.name

        # Fallback to equipment if no department on session
        elif session.device_serial:
            try:
                equipment = Equipment.objects.select_related('department').get(
                    serial_number=session.device_serial
                )
                if equipment.department:
                    department_name = equipment.department.name
                    if hasattr(equipment.department, 'workshop') and equipment.department.workshop:
                        workshop_name = equipment.department.workshop.name
            except Equipment.DoesNotExist:
                pass

        # Prepare context for certificate generation
        context = {
            'certificate_number': session.certificate_number or
            f"CAL-{session.pk}-{timezone.now().strftime('%Y%m%d')}",
            'location': department_name,
            'workshop': workshop_name,
            'generation_timestamp': timezone.now(),
            'generated_by': request.user.get_full_name() or request.user.username,
        }

        # Handle POST data for custom fields
        if request.method == 'POST':
            context.update({
                'reviewer_name': request.POST.get('reviewer_name', ''),
                'approver_name': request.POST.get('approver_name', ''),
                'custom_notes': request.POST.get('custom_notes', ''),
                'laboratory_info': request.POST.get('laboratory_info', ''),
            })

        # Generate the PDF
        pdf_buffer = generate_btwelve_certificate(session, context)

        # Create the response
        response = HttpResponse(pdf_buffer.getvalue(),
                                content_type='application/pdf')
        filename = f"certificate_{session.certificate_number or session.id}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    except CalibrationSession.DoesNotExist:
        logger.error(f"Session {session_pk} not found")
        messages.error(request, f"Calibration session {session_pk} not found.")
        return redirect('calibration:session_list')

    except Exception as e:
        logger.error(
            f"Error generating certificate for session {session_pk}: {str(e)}", exc_info=True)
        messages.error(request, f"Error generating certificate: {str(e)}")
        return redirect('calibration:session_detail', pk=session_pk)

@login_required
def certificate_validation(request, certificate_number):
    """Validate a certificate by its number."""
    try:
        session = CalibrationSession.objects.get(
            certificate_number=certificate_number)

        validation_data = {
            'valid': True,
            'certificate_number': certificate_number,
            'device_info': {
                'model': getattr(session, 'device_model', None),
                'serial': getattr(session, 'device_serial', None),
                'manufacturer': getattr(session, 'device_manufacturer', None)
            },
            'calibration_date': session.timestamp.isoformat() if session.timestamp else None,
            'performed_by': session.performed_by.get_full_name() if session.performed_by else None,
            'status': 'PASS' if getattr(session, 'overall_pass', False) else 'FAIL',
            'validation_timestamp': timezone.now().isoformat()
        }

        return JsonResponse(validation_data)

    except CalibrationSession.DoesNotExist:
        return JsonResponse({
            'valid': False,
            'error': 'Certificate not found',
            'certificate_number': certificate_number,
            'validation_timestamp': timezone.now().isoformat()
        }, status=404)

    except Exception as e:
        logger.error(
            f"Error validating certificate {certificate_number}: {str(e)}")
        return JsonResponse({
            'valid': False,
            'error': str(e),
            'certificate_number': certificate_number,
            'validation_timestamp': timezone.now().isoformat()
        }, status=500)


@login_required
def sessions_pending_approval(request):
    from django.core.paginator import Paginator

    active_tab = request.GET.get("tab", "pending")
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    three_days_ago = now - timedelta(days=3)

    # ── Global counts (always computed, used by both tab badges + stat cards) ──
    pending_count = CalibrationSession.objects.filter(
        status="pending_review"
    ).count()
    declined_count = CalibrationSession.objects.filter(
        status="rejected",
        rejected_at__gte=thirty_days_ago
    ).count()
    approved_today_count = CalibrationSession.objects.filter(
        status="approved",
        approved_at__date=now.date()
    ).count()
    high_priority_count = CalibrationSession.objects.filter(
        status="pending_review",
        overall_pass=False
    ).count()
    can_review_count = CalibrationSession.objects.filter(
        status="rejected",
        rejected_at__gte=thirty_days_ago,
        rejected_at__lte=three_days_ago
    ).count()
    approval_rate = round(
        (approved_today_count / (approved_today_count + pending_count) * 100), 1
    ) if (approved_today_count + pending_count) else 100

    # ── Filters (shared by both branches) ──
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    equipment = request.GET.get("equipment")

    if active_tab == "declined":
        sessions = CalibrationSession.objects.filter(
            status="rejected",
            rejected_at__gte=thirty_days_ago
        ).select_related("procedure", "performed_by", "rejected_by")

        if date_from:
            sessions = sessions.filter(rejected_at__date__gte=date_from)
        if date_to:
            sessions = sessions.filter(rejected_at__date__lte=date_to)
        if equipment:
            sessions = sessions.filter(
                device_description__name__icontains=equipment
            )

        paginator = Paginator(sessions, 10)
        page_obj = paginator.get_page(request.GET.get("page"))

        # Annotate paginated sessions with can_restore
        for session in page_obj:
            session.can_restore = (
                session.rejected_at and session.rejected_at <= three_days_ago
            )

    else:
        sessions = CalibrationSession.objects.filter(
            status="pending_review"
        ).select_related("procedure", "performed_by")

        if date_from:
            sessions = sessions.filter(timestamp__date__gte=date_from)
        if date_to:
            sessions = sessions.filter(timestamp__date__lte=date_to)
        if equipment:
            sessions = sessions.filter(
                device_description__name__icontains=equipment
            )

        paginator = Paginator(sessions, 10)
        page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "active_tab": active_tab,
        # Tab badges
        "pending_count": pending_count,
        "declined_count": declined_count,
        # Stat cards
        "approved_count": approved_today_count,
        "high_priority_count": high_priority_count,
        "can_review_count": can_review_count,
        "approval_rate": approval_rate,
        # Filters
        "current_filters": {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "equipment": equipment or "",
        },
        "show_sidebar": True,
    }

    return render(request, "Calibrition/sessions_pending_approval.html", context)
@login_required
@require_http_methods(["POST"])
def approve_calibration_session_ajax(request, pk):
    """
    ✅ FIXED: Enhanced approval with proper status handling
    - Only generates certificates for APPROVED sessions
    - Properly updates pending_certificates status
    - Handles both online and offline modes
    """
    try:
        # ✅ CRITICAL: Only allow approval if status is pending_review
        session = get_object_or_404(CalibrationSession, pk=pk, status="pending_review")

        def is_hq_online():
            """Check if HQ server is reachable"""
            try:
                from django.conf import settings
                import requests
                sync_url = getattr(settings, 'SYNC_API_URL', 'http://192.168.10.50:5000')
                response = requests.get(f"{sync_url}/api/sync/health", timeout=5)
                return response.status_code == 200
            except:
                return False

        def get_machine_identifier():
            """Get machine identifier matching sync_agent logic"""
            try:
                import hashlib
                mac_int = uuid.getnode()
                mac_hex = ":".join(f"{(mac_int >> ele) & 0xff:02x}" for ele in range(40, -1, -8))
                mac_hash = hashlib.sha1(mac_hex.encode()).hexdigest()[:12]
                return f"mac-{mac_hash}"
            except:
                return f"machine-{str(uuid.uuid4())[:8]}"

        with transaction.atomic():
            # Lock the session to prevent race conditions
            locked_session = CalibrationSession.objects.select_for_update().get(pk=pk)

            # ✅ VERIFICATION: Double-check status hasn't changed
            if locked_session.status != "pending_review":
                return JsonResponse({
                    "success": False,
                    "error": f"Session status is '{locked_session.status}', cannot approve"
                }, status=400)

            now = timezone.now()

            if is_hq_online():
                # ==========================================
                # ONLINE MODE - Generate certificate immediately
                # ==========================================
                try:
                    certificate_number = CalibrationSession.generate_certificate_number()

                    if not certificate_number:
                        raise ValueError("Certificate number generation failed - returned empty/None")

                    logger.info(f"📄 Generated certificate number: {certificate_number}")
                except Exception as cert_error:
                    logger.error(
                        f"Certificate generation error: {cert_error}",
                        exc_info=True
                    )
                    return JsonResponse({
                        "success": False,
                        "error": f"Failed to generate certificate number: {str(cert_error)}"
                    }, status=500)

                # Validate certificate number length
                if len(certificate_number) > 50:
                    logger.error(
                        f"Certificate number too long: {certificate_number} "
                        f"(length: {len(certificate_number)})"
                    )
                    return JsonResponse({
                        "success": False,
                        "error": f"Generated certificate number is too long: {certificate_number}"
                    }, status=500)

                # ✅ CRITICAL: Update to APPROVED status with certificate
                locked_session.certificate_number = certificate_number
                locked_session.status = "approved"  # ✅ MUST be 'approved'
                locked_session.approved_by = request.user
                locked_session.approved_at = now
                locked_session.updated_at = now
                locked_session.save()

                # Complete schedule AND lock it
                if locked_session.schedule:
                    locked_session.schedule.status = "completed"
                    locked_session.schedule.completed_date = now.date()

                    # ✅ LOCK THE SCHEDULE to prevent modification
                    if hasattr(locked_session.schedule, 'is_locked'):
                        locked_session.schedule.is_locked = True
                        logger.info(
                            f"🔒 Locked schedule {locked_session.schedule.id} "
                            f"for equipment {locked_session.schedule.equipment.id}"
                        )

                    locked_session.schedule.updated_at = now
                    locked_session.schedule.save()

                    logger.info(
                        f"✅ Schedule {locked_session.schedule.id} completed and locked "
                        f"for equipment {locked_session.schedule.equipment.description}"
                    )

                    # ✅ TRIGGER GROUP-AWARE RESCHEDULING CHECK
                    # Check if this schedule's group is now fully complete
                    try:
                        from calSchedules.locker import _basic_group_completion_check

                        planning_logic = locked_session.schedule.planning_logic or 'department'

                        if planning_logic == 'department':
                            group_id = locked_session.schedule.equipment.department_id
                        else:
                            group_id = locked_session.schedule.equipment.description_id

                        group_status = _basic_group_completion_check(
                            group_id,
                            locked_session.schedule.scheduled_month,
                            planning_logic
                        )

                        logger.info(
                            f"📊 Group status check: {group_status['completed']}/"
                            f"{group_status['total']} complete in "
                            f"{locked_session.schedule.scheduled_month.strftime('%B %Y')}"
                        )

                        if group_status['all_completed']:
                            logger.info(
                                f"🎉 ENTIRE GROUP COMPLETE! "
                                f"{group_status['total']} equipment ready for rescheduling. "
                                f"Run lock_and_reschedule_completed task to reschedule this group."
                            )
                    except Exception as group_check_error:
                        logger.warning(
                            f"Could not check group completion status: {group_check_error}"
                        )

                # ✅ IMPORTANT: Update any pending_certificates records for this session
                PendingCertificate.objects.filter(
                    session=locked_session,
                    sync_status='pending'
                ).update(
                    sync_status='completed',
                    processed_at=now,
                    updated_at=now
                )

                logger.info(f"✅ Online approval: Session {locked_session.id} approved with certificate {certificate_number}")

                return JsonResponse({
                    "success": True,
                    "certificate_number": certificate_number,
                    "mode": "online",
                    "session_id": str(locked_session.id),
                    "status": "approved",  # ✅ Confirm approved status
                    "updated_at": now.isoformat()
                })

            else:
                # ==========================================
                # OFFLINE MODE - Queue for certificate generation
                # ==========================================

                # ✅ CRITICAL: Set status to approved_pending_certificate
                # This is NOT 'pending_review' - it's a transition state
                locked_session.status = "approved_pending_certificate"
                locked_session.approved_by = request.user
                locked_session.approved_at = now
                locked_session.certificate_number = None  # Will be generated later
                locked_session.updated_at = now
                locked_session.save()

                # Create pending certificate record
                machine_id = get_machine_identifier()
                pending_cert = PendingCertificate.objects.create(
                    session=locked_session,
                    machine_id=machine_id,
                    sync_status='pending',
                    created_at=now,
                    updated_at=now
                )

                # Update schedule status
                if locked_session.schedule:
                    locked_session.schedule.status = "pending_certificate"

                    # ✅ LOCK THE SCHEDULE even in offline mode
                    # The calibration is approved, schedule should not be modified
                    if hasattr(locked_session.schedule, 'is_locked'):
                        locked_session.schedule.is_locked = True
                        logger.info(
                            f"🔒 Locked schedule {locked_session.schedule.id} "
                            f"(offline approval, pending certificate)"
                        )

                    locked_session.schedule.updated_at = now
                    locked_session.schedule.save()

                    logger.info(
                        f"⏳ Schedule {locked_session.schedule.id} marked pending_certificate "
                        f"and locked (offline mode)"
                    )

                logger.info(f"⏳ Offline approval: Session {locked_session.id} approved, queued for certificate generation")

                return JsonResponse({
                    "success": True,
                    "certificate_number": None,
                    "mode": "offline",
                    "session_id": str(locked_session.id),
                    "status": "approved_pending_certificate",  # ✅ Clear status
                    "pending_cert_id": str(pending_cert.id),
                    "updated_at": now.isoformat(),
                    "message": "Approved offline. Certificate will be generated when connection is restored."
                })

    except CalibrationSession.DoesNotExist:
        return JsonResponse({
            "success": False,
            "error": "Session not found or not in pending_review status"
        }, status=404)
    except Exception as e:
        logger.error(f"Error approving session {pk}: {str(e)}", exc_info=True)
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)


@login_required
def reject_calibration_session(request, pk):
    from django.http import Http404

    try:
        session = get_object_or_404(
            CalibrationSession, pk=pk, status="pending_review")

        if request.method == "POST":
            rejection_reason = request.POST.get("rejection_reason")
            rejection_comments = request.POST.get("rejection_comments")

            session.status = "rejected"
            session.rejection_reason = rejection_reason
            session.rejection_comments = rejection_comments
            session.rejected_by = request.user
            session.rejected_at = timezone.now()
            session.save(update_fields=["status", "rejection_reason", "rejection_comments", "rejected_by", "rejected_at"])

            # Update linked schedule → pending
            if session.schedule:
                session.schedule.status = "pending"
                session.schedule.completed_date = None
                session.schedule.save(update_fields=["status", "completed_date"])

            messages.warning(
                request, f"Session {session.certificate_number or session.id} has been rejected and schedule reset to pending.")

            if is_ajax(request):
                return JsonResponse({
                    "success": True,
                    "message": "Session rejected successfully"
                })

            return redirect("calibration:sessions_pending_approval")

        # For GET, return JSON for modal
        session_data = {
            "id": str(session.id),
            "certificate_number": session.certificate_number,
            "device_model": session.device_model,
            "device_serial": session.device_serial,
            "device_description": str(session.device_description) if session.device_description else None,
        }
        return JsonResponse(session_data)

    except Exception as e:
        logger.error(f"Error rejecting session {pk}: {str(e)}", exc_info=True)
        if is_ajax(request):
            return JsonResponse({"success": False, "error": str(e)}, status=500)
        raise


@login_required
def api_session_details(request, session_id):
    session = get_object_or_404(CalibrationSession, id=session_id)

    # Build session metadata
    session_data = {
        "id": session.id,
        "status": session.status,
        "procedure": session.procedure.name,
        "performed_by": session.performed_by.get_full_name() or session.performed_by.username,
        "timestamp": session.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "device": {
            "model": session.device_model,
            "serial": session.device_serial,
            "manufacturer": str(session.device_manufacturer) if session.device_manufacturer else None,
            "description": str(session.device_description) if session.device_description else None,
        },
        "environment": {
            "temperature": str(session.actual_temperature),
            "humidity": str(session.actual_humidity),
            "pressure": str(session.actual_pressure),
        },
        "overall_pass": session.overall_pass,
        "certificate_number": session.certificate_number,
        "notes": session.notes,
        "readings": [],
    }

    # Collect readings
    readings = CalibrationReading.objects.filter(session=session).select_related(
        "parameter", "sub_parameter", "set_value"
    )

    for r in readings:
        session_data["readings"].append({
            "parameter": r.parameter.name,
            "sub_parameter": r.sub_parameter.name if r.sub_parameter else None,
            "unit": r.parameter.unit,
            "set_value": float(r.set_value.value),
            "readings": r.get_readings_list(),
            "mean": float(r.mean) if r.mean is not None else None,
            "std_dev": float(r.standard_deviation) if r.standard_deviation is not None else None,
            "error": float(r.error) if r.error is not None else None,
            "type_a_unc": float(r.type_a_uncertainty) if r.type_a_uncertainty else None,
            "type_b_unc": float(r.type_b_uncertainty) if r.type_b_uncertainty else None,
            "ref_unc_comp": float(r.reference_uncertainty_component) if r.reference_uncertainty_component else None,
            "combined_unc": float(r.combined_uncertainty) if r.combined_uncertainty else None,
            "expanded_unc": float(r.expanded_uncertainty) if r.expanded_uncertainty else None,
            "passes_tolerance": r.passes_tolerance,
        })

    return JsonResponse(session_data, safe=False)


logger = logging.getLogger(__name__)


logger = logging.getLogger(__name__)
@login_required
@require_GET
def bulk_certificates_download(request):
    """
    Stream calibration certificates in bulk for a date range,
    with full role + workshop/department access control.
    Returns JSON if no certificates exist to allow frontend overlay.
    """
    today = timezone.now().date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    # Default: current week if no dates given
    date_from = parse_date(request.GET.get("date_from")) or week_start
    date_to = parse_date(request.GET.get("date_to")) or week_end

    # Optional "check_only" param to just check if certs exist
    check_only = request.GET.get("check_only") == "1"

    try:
        sessions, accessible_equipment = get_accessible_sessions(
            request.user, date_from, date_to
        )
    except PermissionError as e:
        logger.error(f"Permission error for user {request.user.username}: {str(e)}")
        return HttpResponseForbidden(str(e))
    except Exception as e:
        logger.error(f"Error getting accessible sessions: {str(e)}", exc_info=True)
        return JsonResponse({
            "error": f"Error retrieving sessions: {str(e)}"
        }, status=500)

    if not sessions.exists():
        if check_only:
            return JsonResponse({"count": 0})
        else:
            return JsonResponse({
                "error": "No approved sessions found in this date range."
            }, status=200)

    if check_only:
        return JsonResponse({"count": sessions.count()})

    # Stream ZIP file
    try:
        z = zipstream.ZipFile(mode="w", compression=zipstream.ZIP_STORED)

        for session in sessions:
            try:
                # ---- Department/workshop resolution ----
                department_name = "Unknown Department"
                workshop_name = "Unknown Workshop"

                # Use lowercase 'department' field consistently
                if hasattr(session, 'department') and session.department:
                    department_name = session.department.name
                    if hasattr(session.department, "workshop") and session.department.workshop:
                        workshop_name = session.department.workshop.name
                elif session.device_serial:
                    try:
                        equipment = accessible_equipment.select_related("department").get(
                            serial_number=session.device_serial
                        )
                        if equipment.department:
                            department_name = equipment.department.name
                            if hasattr(equipment.department, "workshop") and equipment.department.workshop:
                                workshop_name = equipment.department.workshop.name
                    except Equipment.DoesNotExist:
                        logger.warning(f"Equipment not found for serial: {session.device_serial}")
                        pass

                # ---- Context for PDF ----
                context = {
                    "certificate_number": session.certificate_number
                    or f"CAL-{session.pk}-{timezone.now().strftime('%Y%m%d')}",
                    "location": department_name,
                    "workshop": workshop_name,
                    "generation_timestamp": timezone.now(),
                    "generated_by": request.user.get_full_name() or request.user.username or "System",
                    "procedure": session.procedure.name if session.procedure else "Unknown Procedure",
                    "device_model": session.device_model or "",
                    "device_serial": session.device_serial or "",
                    "device_description": str(session.device_description) if session.device_description else "",
                }

                # Generate certificate PDF
                pdf_buffer = generate_btwelve_certificate(session, context)
                filename = f"certificate_{session.certificate_number or session.id}.pdf"

                # Add to streaming ZIP
                z.writestr(filename, pdf_buffer.getvalue())

            except Exception as e:
                logger.error(
                    f"PDF generation failed for session {session.id}: {str(e)}",
                    exc_info=True
                )
                # Continue with next session instead of failing entire download
                continue

        response = StreamingHttpResponse(z, content_type="application/zip")
        response["Content-Disposition"] = (
            f'attachment; filename="certificates_{date_from}_to_{date_to}.zip"'
        )
        return response

    except Exception as e:
        logger.error(f"Error creating ZIP file: {str(e)}", exc_info=True)
        return JsonResponse({
            "error": f"Error creating certificate bundle: {str(e)}"
        }, status=500)

def get_sankey_data(schedule_counts):
    """Generate Sankey chart data from schedule counts"""
    pending = schedule_counts.get('pending', 0)
    overdue = schedule_counts.get('overdue', 0)
    in_progress = schedule_counts.get('in_progress', 0)
    completed = schedule_counts.get('completed', 0)

    # This is a simplified example - adjust based on your actual workflow
    sankey_data = [
        {'from': 'Scheduled', 'to': 'In Progress', 'flow': min(in_progress, pending)},
        {'from': 'Scheduled', 'to': 'Overdue', 'flow': min(overdue, pending)},
        {'from': 'Scheduled', 'to': 'Completed', 'flow': min(completed, pending)},
        {'from': 'In Progress', 'to': 'Completed', 'flow': int(in_progress * 0.7)},
        {'from': 'Overdue', 'to': 'Completed', 'flow': int(overdue * 0.4)},
    ]

    return [item for item in sankey_data if item['flow'] > 0]


@login_required
@require_http_methods(["POST"])
def restore_rejected_session(request, pk):
    """
    Restore a rejected session back to pending_review status for re-calibration.
    Can only be restored after 3 days from rejection.
    """
    session = get_object_or_404(CalibrationSession, pk=pk)

    if session.status != "rejected":
        return JsonResponse({
            "success": False,
            "error": f"Session status is '{session.status}', can only restore rejected sessions"
        }, status=400)

    if not session.rejected_at:
        return JsonResponse({
            "success": False,
            "error": "Session has no rejection timestamp"
        }, status=400)

    # Check if 3 days have passed since rejection
    three_days_ago = timezone.now() - timedelta(days=3)
    if session.rejected_at > three_days_ago:
        days_remaining = (session.rejected_at + timedelta(days=3) - timezone.now()).days
        return JsonResponse({
            "success": False,
            "error": f"Cannot restore yet. {days_remaining} day(s) remaining before review allowed."
        }, status=400)

    # Restore to pending_review
    session.status = "pending_review"
    session.rejection_reason = None
    session.rejection_comments = None
    session.rejected_by = None
    session.rejected_at = None
    session.approved_by = None
    session.approved_at = None
    session.save(update_fields=["status", "rejection_reason", "rejection_comments", "rejected_by", "rejected_at", "approved_by", "approved_at"])

    # Reset linked schedule to pending if exists
    if session.schedule:
        session.schedule.status = "pending"
        session.schedule.completed_date = None
        session.schedule.save(update_fields=["status", "completed_date"])

    logger.info(f"Session {session.id} restored from rejected to pending_review")

    return JsonResponse({
        "success": True,
        "message": "Session restored to pending review status"
    })


@login_required
def download_declined_certificate(request, pk):
    """
    Generate and download a PDF certificate for a declined/rejected session.
    Shows the readings just like a regular certificate but marks as rejected.
    """
    session = get_object_or_404(CalibrationSession, pk=pk, status="rejected")

    # Build context similar to regular certificate
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
        "rejection_reason": session.get_rejection_reason_display() if session.rejection_reason else "Not specified",
        "rejection_comments": session.rejection_comments or "No comments provided",
        "rejected_by": session.rejected_by.get_full_name() or session.rejected_by.username if session.rejected_by else "Unknown",
        "rejected_at": session.rejected_at.strftime("%Y-%m-%d %H:%M") if session.rejected_at else "Unknown",
    }

    # Generate PDF
    pdf_buffer = generate_btwelve_certificate(session, context)

    filename = f"declined_certificate_{session.certificate_number or session.id}.pdf"

    response = HttpResponse(pdf_buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    return response
