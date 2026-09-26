"""ppms.views — shared user-access context and PPM statistics helpers."""
from calendar import monthrange
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.timezone import localdate
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from ..models import PPMSchedule
from Inventory.models import Equipment, Department, EquipmentDescription
from openpyxl import Workbook
from workshop.models import Workshop
import logging
from django.utils import timezone
from ..ppm_pdf_generator import create_ppm_pdf_response
from celery.result import AsyncResult
logger = logging.getLogger(__name__)


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
                    'can_edit': False,
                    'can_schedule': False
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

            # An HOD is attached to neither a workshop nor a department, so both
            # branches above miss and the user used to fall through to a None
            # context — which sent the head of department to the login page.
            # Fall back to whichever workshop they picked elsewhere in the app,
            # otherwise the first one, so the module opens and the workshop
            # filter takes it from there.
            hod_workshop = None
            selected_id = (request.session.get('selected_workshop_id')
                           or request.session.get('workshop_id'))
            if selected_id:
                hod_workshop = Workshop.objects.filter(id=selected_id).first()
            if hod_workshop is None:
                hod_workshop = Workshop.objects.order_by('name').first()
            if hod_workshop:
                return {
                    'access_type': 'workshop',
                    'workshop_id': hod_workshop.id,
                    'department_id': None,
                    'workshop': hod_workshop,
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


def get_current_month_year():
    """Returns current month and year as integers"""
    today = localdate()
    return today.month, today.year


def calculate_ppm_statistics(schedules, equipment_queryset):
    """
    Calculate real PPM statistics based on actual data
    Returns dict with all necessary statistics
    """
    today = localdate()
    total_equipment = equipment_queryset.count()
    total_scheduled = schedules.count()

    # Status breakdown
    pending_count = schedules.filter(status='pending').count()
    completed_count = schedules.filter(status='completed').count()
    pushed_count = schedules.filter(status='pushed').count()

    # Calculate percentages
    pending_percentage = (pending_count / total_scheduled * 100) if total_scheduled > 0 else 0
    completed_percentage = (completed_count / total_scheduled * 100) if total_scheduled > 0 else 0
    pushed_percentage = (pushed_count / total_scheduled * 100) if total_scheduled > 0 else 0

    # Calculate completion rate (completed on time vs total due)
    completed_on_time = 0
    total_due = 0

    for schedule in schedules:
        if schedule.scheduled_month:
            # Get the LAST DAY of the scheduled month
            last_day = monthrange(schedule.scheduled_month.year, schedule.scheduled_month.month)[1]
            month_end_date = schedule.scheduled_month.replace(day=last_day)

            # If the last day of scheduled month has passed or is current date
            if month_end_date <= today:
                total_due += 1
                if schedule.status == 'completed':
                    # Check if completed on time (before or on the last day of scheduled month)
                    if schedule.updated_at and timezone.localdate(schedule.updated_at) <= month_end_date:
                        completed_on_time += 1

    completion_rate = (completed_on_time / total_due * 100) if total_due > 0 else 0

    # Calculate overdue count - schedules that are NOT completed and past the last day of their scheduled month
    overdue_count = 0
    for schedule in schedules:
        if schedule.scheduled_month and schedule.status != 'completed':
            # Get the LAST DAY of the scheduled month
            last_day = monthrange(schedule.scheduled_month.year, schedule.scheduled_month.month)[1]
            month_end_date = schedule.scheduled_month.replace(day=last_day)

            # Schedule is overdue if today is AFTER the last day of the scheduled month
            if today > month_end_date:
                overdue_count += 1

    return {
        'total_equipment': total_equipment,
        'total_scheduled': total_scheduled,
        'unscheduled': total_equipment - len(set(schedules.values_list('equipment_id', flat=True))),
        'pending_count': pending_count,
        'completed_count': completed_count,
        'pushed_count': pushed_count,
        'overdue_count': overdue_count,
        'pending_percentage': round(pending_percentage, 1),
        'completed_percentage': round(completed_percentage, 1),
        'pushed_percentage': round(pushed_percentage, 1),
        'completion_rate': round(completion_rate, 1),
    }


def filtered_statistics(schedules, equipment_queryset, unscheduled):
    """Summary counts for exactly the schedules the page shows (the selected
    month, week and department). Unscheduled is equipment with no schedule
    at all, which no month narrows."""
    statistics = calculate_ppm_statistics(schedules, equipment_queryset)
    statistics['unscheduled'] = unscheduled.count()
    return statistics


def department_breakdown(schedules, equipment_queryset):
    """Per-department counts of the shown schedules, for the Summary tab."""
    counts = {
        row['equipment__department_id']: row
        for row in schedules.order_by().values('equipment__department_id').annotate(
            scheduled=Count('id'),
            pending=Count('id', filter=Q(status='pending')),
            completed=Count('id', filter=Q(status='completed')),
        )
    }
    rows = []
    for dept in (equipment_queryset.order_by().values('department_id', 'department__name')
                 .annotate(equipment=Count('id')).order_by('department__name')):
        c = counts.get(dept['department_id'], {})
        scheduled = c.get('scheduled', 0)
        completed = c.get('completed', 0)
        rows.append({
            'name': dept['department__name'],
            'equipment': dept['equipment'],
            'scheduled': scheduled,
            'pending': c.get('pending', 0),
            'completed': completed,
            'completion_rate': round(completed / scheduled * 100, 1) if scheduled else 0,
        })
    return rows


def scheduling_context(request, access_context):
    """The Scheduling panel on the PPM page: this workshop's PPM plan."""
    from scheduling.reports import panels
    from scheduling.views import can_manage
    workshop_id = (access_context or {}).get('workshop_id')
    workshops = Workshop.objects.filter(id=workshop_id) if workshop_id else Workshop.objects.none()
    return {
        'scheduling_panels': panels(workshops, 'ppm'),
        'scheduling_can_manage': can_manage(request.user),
    }
