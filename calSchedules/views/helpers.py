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
from ..tasks import initialize_calibration_schedule_with_logic
from openpyxl import Workbook
from CalSoft.models import CalibrationSession
from django.db import transaction
import logging
from django.db.models.functions import TruncMonth
from django.db.models import Q, Case, When, IntegerField, Count
logger = logging.getLogger(__name__)
from django.utils import timezone
from django.contrib.auth import get_user_model
from ..tasks import initialize_calibration_schedule_with_logic, auto_advance_completed_calibrations, normalize_existing_schedules, smart_reorganize_on_logic_change
import uuid
User = get_user_model()
from ..calibration_pdf_generator import create_calibration_pdf_response


def get_current_month_year():
    """
    Returns current month and year as integers
    """
    today = datetime.today()
    return today.month, today.year


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


def check_group_waiting_status(schedule):
    """
    Check if schedule is waiting for group completion
    """
    if schedule.status != 'completed':
        return {'is_waiting': False}

    try:
        from ..instant_reconciliation import check_group_completion_status

        planning_logic = schedule.planning_logic or 'department'

        status = check_group_completion_status(
            schedule.equipment,
            schedule.scheduled_month,
            planning_logic
        )

        # Get pending equipment details
        pending_equipment = []
        if not status['all_completed']:
            pending_members = status['members'].filter(
                status__in=['pending', 'pushed', 'in_progress']
            ).select_related('equipment__description')

            for member in pending_members:
                pending_equipment.append({
                    'id': member.equipment.id,
                    'description': member.equipment.description.name if member.equipment.description else 'N/A',
                    'serial_number': member.equipment.serial_number,
                    'status': member.status
                })

        return {
            'is_waiting': not status['all_completed'],
            'completed_count': status['completed'],
            'total_count': status['total'],
            'group_name': status['group_name'],
            'pending_equipment': pending_equipment
        }
    except ImportError:
        return {'is_waiting': False}
    except Exception as e:
        logger.error(f"Error checking group waiting status: {e}")
        return {'is_waiting': False}


def _get_logic_change_context(schedules_queryset):
    """
    Return template context variables for the logic-change warning banner.

    Computes:
      - current_planning_logic  – the dominant planning_logic in use
      - logic_change_warnings_count – number of schedules with a non-empty
                                      logic_change_warning field
      - logic_change_sample     – up to 5 of those schedules, each as a
                                  dict with keys: equipment_name,
                                  scheduled_month, warning_snippet

    Safe to call with any CalibrationSchedule queryset (filtered or not).
    Always returns a dict — never raises.
    """
    try:
        warned_qs = schedules_queryset.exclude(logic_change_warning='')
        count = warned_qs.count()

        # Determine the dominant current planning logic across ALL schedules
        # (not just warned ones, so we reflect the current state of the system).
        from django.db.models import Count as _Count
        logic_row = (
            schedules_queryset
            .exclude(planning_logic='')
            .values('planning_logic')
            .annotate(n=_Count('id'))
            .order_by('-n')
            .first()
        )
        current_logic = logic_row['planning_logic'] if logic_row else ''

        sample = []
        for sched in warned_qs.select_related('equipment__description')[:5]:
            warning_text = sched.logic_change_warning or ''
            # Produce a short snippet – first 80 chars of the warning
            snippet = warning_text[:80] + ('…' if len(warning_text) > 80 else '')
            sample.append({
                'equipment_name': (
                    sched.equipment.description.name
                    if sched.equipment and sched.equipment.description
                    else 'N/A'
                ),
                'scheduled_month': (
                    sched.scheduled_month.strftime('%B %Y')
                    if sched.scheduled_month else 'N/A'
                ),
                'warning_snippet': snippet,
            })

        return {
            'current_planning_logic': current_logic,
            'logic_change_warnings_count': count,
            'logic_change_sample': sample,
        }
    except Exception as exc:
        logger.error(f"[_get_logic_change_context] Error computing logic-change context: {exc}")
        return {
            'current_planning_logic': '',
            'logic_change_warnings_count': 0,
            'logic_change_sample': [],
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
                    'can_edit': True,
                    'can_schedule': True
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
                    'can_edit': True,
                    'can_schedule': True
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
