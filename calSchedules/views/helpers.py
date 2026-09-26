"""calSchedules views — shared user-access context, month, overdue/waiting-group helpers."""
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from datetime import datetime, timedelta, date
import calendar
from dateutil.relativedelta import relativedelta
from workshop.models import Workshop
from ..models import CalibrationSchedule
from Inventory.models import Equipment, Department, EquipmentDescription
from openpyxl import Workbook
from CalSoft.models import CalibrationSession
from django.db import transaction
import logging
from django.db.models.functions import TruncMonth
from django.db.models import Q, Case, When, IntegerField, Count
logger = logging.getLogger(__name__)
from django.utils import timezone
from django.contrib.auth import get_user_model
import uuid
User = get_user_model()
from ..calibration_pdf_generator import create_calibration_pdf_response


def get_current_month_year():
    """
    Returns current month and year as integers
    """
    from core.eat import today_eat
    today = today_eat()
    return today.month, today.year


def selected_period(request):
    """The month the schedules page shows, as (month, year).

    ?month= and ?year= choose it and it defaults to the current month;
    ?period=all shows every month and returns None.
    """
    if request.GET.get("period") == "all":
        return None
    current_month, current_year = get_current_month_year()
    try:
        month = int(request.GET.get("month") or current_month)
        if not 1 <= month <= 12:
            month = current_month
    except (TypeError, ValueError):
        month = current_month
    try:
        year = int(request.GET.get("year") or current_year)
    except (TypeError, ValueError):
        year = current_year
    return month, year


def in_period(schedules, period):
    """Narrow schedules to the selected month (all months when period is None)."""
    if period is None:
        return schedules
    month, year = period
    return schedules.filter(scheduled_month__month=month, scheduled_month__year=year)


def check_overdue_schedules(schedules_queryset):
    """
    Check for overdue schedules
    """
    today = date.today()
    current_month = today.replace(day=1)

    # Overdue: scheduled month has passed
    overdue = schedules_queryset.filter(
        scheduled_month__lt=current_month,
        status__in=['pending', 'pushed']
    )

    # Warning: due this month
    warning = schedules_queryset.filter(
        scheduled_month=current_month,
        status__in=['pending', 'pushed']
    )

    return {
        'overdue_count': overdue.count(),
        'overdue_schedules': overdue,
        'warning_count': warning.count(),
        'warning_schedules': warning
    }


def get_user_access_context(request):
    """
    Determine user's access level and return appropriate context based on UserProfile model.
    Returns: dict with 'access_type', 'workshop_id', 'department_id', 'workshop', 'department', 'role', 'level'.
    """
    try:
        profile = request.user.userprofile
        role = profile.role
        level = profile.level

        if role == 'Tech':
            if level == 'Engineer Incharge' and profile.workshop:
                return {
                    'access_type': 'workshop',
                    'workshop_id': profile.workshop.id,
                    'department_id': None,
                    'workshop': profile.workshop,
                    'department': None,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': True,
                    'can_edit': True,
                    'can_schedule': True
                }
            elif level == 'Engineer' and profile.department:
                return {
                    'access_type': 'department',
                    'workshop_id': profile.department.workshop_id if profile.department.workshop else None,
                    'department_id': profile.department.id,
                    'workshop': profile.department.workshop if profile.department.workshop else None,
                    'department': profile.department,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': False,
                    'can_edit': True,
                    'can_schedule': True
                }
            elif profile.workshop:
                return {
                    'access_type': 'workshop',
                    'workshop_id': profile.workshop.id,
                    'department_id': None,
                    'workshop': profile.workshop,
                    'department': None,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': True,
                    'can_edit': True,
                    'can_schedule': True
                }
        elif role == 'NIC' and profile.department:
            return {
                'access_type': 'department',
                'workshop_id': profile.department.workshop_id if profile.department.workshop else None,
                'department_id': profile.department.id,
                'workshop': profile.department.workshop if profile.department.workshop else None,
                'department': profile.department,
                'role': role,
                'level': level,
                'can_manage_all_departments': False,
                'can_edit': False,  # NIC cannot edit
                'can_schedule': False  # NIC cannot schedule
            }
        elif role == 'HOD':
            # HODs oversee: they see every schedule but neither plan nor
            # schedule, push, complete or delete (the workshops do).
            if profile.workshop:
                return {
                    'access_type': 'workshop',
                    'workshop_id': profile.workshop.id,
                    'department_id': None,
                    'workshop': profile.workshop,
                    'department': None,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': True,
                    'can_edit': False,
                    'can_schedule': False
                }
            elif profile.department:
                return {
                    'access_type': 'department',
                    'workshop_id': profile.department.workshop_id if profile.department.workshop else None,
                    'department_id': profile.department.id,
                    'workshop': profile.department.workshop if profile.department.workshop else None,
                    'department': profile.department,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': False,
                    'can_edit': False,
                    'can_schedule': False
                }

        if role == 'HOD':
            # An HOD belongs to no workshop: open on the calibration center
            # (it calibrates the whole hospital), read only.
            from scheduling.planner import calibration_center
            center = calibration_center() or Workshop.objects.order_by('name').first()
            if center:
                return {
                    'access_type': 'workshop',
                    'workshop_id': center.id,
                    'department_id': None,
                    'workshop': center,
                    'department': None,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': True,
                    'can_edit': False,
                    'can_schedule': False
                }

        workshop_id = request.session.get('workshop_id')
        if workshop_id:
            try:
                workshop = Workshop.objects.get(id=workshop_id)
                return {
                    'access_type': 'workshop',
                    'workshop_id': workshop_id,
                    'department_id': None,
                    'workshop': workshop,
                    'department': None,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': True,
                    'can_edit': True,
                    'can_schedule': True
                }
            except Workshop.DoesNotExist:
                logger.warning(f"Workshop ID {workshop_id} not found in session for user {request.user.username}")
                pass
    except AttributeError as e:
        logger.warning(f"User {request.user.username} profile access error: {e}")

    return None
