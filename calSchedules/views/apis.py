"""calSchedules views — overdue/waiting-group/group/task status JSON endpoints."""
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

# sibling modules in this package
from .helpers import check_group_waiting_status, check_overdue_schedules, get_user_access_context


@login_required
def get_overdue_status(request):
    """
    ✅ NEW API ENDPOINT
    API endpoint to get overdue schedule information
    """
    try:
        access_context = get_user_access_context(request)
        if not access_context:
            return JsonResponse({'error': 'Access denied'}, status=403)

        # Get schedules
        if access_context['access_type'] == 'department':
            schedules = CalibrationSchedule.objects.filter(
                equipment__department_id=access_context['department_id'],
                equipment__active_status=True
            )
        else:
            schedules = CalibrationSchedule.objects.filter(
                equipment__active_status=True
            )

        overdue_info = check_overdue_schedules(schedules)

        return JsonResponse({
            'overdue_count': overdue_info['overdue_count'],
            'warning_count': overdue_info['warning_count'],
            'total': schedules.count()
        })

    except Exception as e:
        logger.error(f"Error getting overdue status: {str(e)}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def get_waiting_groups(request):
    """
    ✅ NEW API ENDPOINT
    API endpoint to get schedules waiting for group completion
    """
    try:
        access_context = get_user_access_context(request)
        if not access_context:
            return JsonResponse({'error': 'Access denied'}, status=403)

        # Get completed schedules
        if access_context['access_type'] == 'department':
            completed = CalibrationSchedule.objects.filter(
                equipment__department_id=access_context['department_id'],
                status='completed'
            )
        else:
            completed = CalibrationSchedule.objects.filter(
                status='completed'
            )

        # Filter for those that have is_locked=False
        if hasattr(CalibrationSchedule, 'is_locked'):
            completed = completed.filter(is_locked=False)

        waiting_groups = []
        for schedule in completed:
            waiting_status = check_group_waiting_status(schedule)
            if waiting_status['is_waiting']:
                waiting_groups.append({
                    'schedule_id': str(schedule.id),
                    'group_name': waiting_status['group_name'],
                    'completed': waiting_status['completed_count'],
                    'total': waiting_status['total_count'],
                    'pending_equipment': waiting_status['pending_equipment']
                })

        return JsonResponse({
            'waiting_count': len(waiting_groups),
            'waiting_groups': waiting_groups
        })

    except Exception as e:
        logger.error(f"Error getting waiting groups: {str(e)}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def get_group_status(request, schedule_id):
    """
    API endpoint to get group completion status for a specific schedule
    """
    try:
        schedule = get_object_or_404(CalibrationSchedule, id=schedule_id)
        waiting_status = check_group_waiting_status(schedule)

        return JsonResponse({
            'is_waiting': waiting_status['is_waiting'],
            'completed_count': waiting_status.get('completed_count', 0),
            'total_count': waiting_status.get('total_count', 0),
            'group_name': waiting_status.get('group_name', ''),
            'pending_equipment': waiting_status.get('pending_equipment', [])
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def get_calibration_task_status(request, task_id):
    """
    Return JSON status of a calibration initialization task.
    Used by front-end polling to trigger table refresh on completion.
    """
    from celery.result import AsyncResult

    result = AsyncResult(task_id)
    done = result.ready()
    state = result.state
    info = result.info if done else None

    return JsonResponse({
        'task_id': task_id,
        'ready': done,
        'state': state,
        'info': info
    })
