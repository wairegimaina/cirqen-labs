"""calSchedules views — AJAX schedule listing endpoints and their filter helpers."""
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
from .helpers import get_user_access_context


def _no_access_response():
    """JSON answer for a user with no workshop or department to scope schedules to
    (for example an HOD, or an In-Charge whose department was deleted)."""
    return JsonResponse(
        {'error': 'Your account has no workshop or department assigned.'}, status=403
    )


def _apply_ajax_common_filters(qs, request, access_context):
    search = request.GET.get('search', '').strip()
    month = request.GET.get('month', '').strip()
    year = request.GET.get('year', '').strip()
    show_all = request.GET.get('show_all') == 'true'
    department = request.GET.get('department', '').strip()

    if access_context['access_type'] == 'department':
        qs = qs.filter(equipment__department_id=access_context['department_id'])
    elif department:
        qs = qs.filter(equipment__department_id=department)

    if search:
        qs = qs.filter(
            Q(equipment__description__name__icontains=search) |
            Q(equipment__model__icontains=search) |
            Q(equipment__serial_number__icontains=search) |
            Q(equipment__department__name__icontains=search)
        )

    if not show_all:
        if month and year:
            qs = qs.filter(scheduled_month__month=int(month), scheduled_month__year=int(year))
        elif month:
            qs = qs.filter(scheduled_month__month=int(month))
        elif year:
            qs = qs.filter(scheduled_month__year=int(year))
        else:
            today = datetime.today().replace(day=1)
            qs = qs.filter(
                scheduled_month__gte=today - timedelta(days=90),
                scheduled_month__lte=today + timedelta(days=90),
            )

    return qs


def _ajax_schedule_to_dict(schedule, is_overdue=False, is_warning=False, waiting_status=None):
    eq = schedule.equipment
    return {
        'schedule': {
            'id': schedule.id,
            'status': schedule.status,
            'planning_logic': schedule.planning_logic or '',
            'is_locked': schedule.is_locked,
            'logic_change_warning': schedule.logic_change_warning or '',
            'scheduled_month_display': schedule.scheduled_month.strftime('%B %Y') if schedule.scheduled_month else '',
            'completed_date_display': schedule.completed_date.strftime('%d %b %Y') if schedule.completed_date else '',
            'overdue_since': '',
            'equipment_description': eq.description.name if eq.description else '',
            'serial_number': eq.serial_number or '',
            'model': eq.model or '',
            'department_name': eq.department.name if eq.department else '',
        },
        'is_overdue': is_overdue,
        'is_warning': is_warning,
        'waiting_status': waiting_status or {'is_waiting': False},
    }


@login_required
def ajax_schedules(request):
    today = datetime.today().date()
    access_context = get_user_access_context(request)
    if access_context is None:
        return _no_access_response()

    qs = CalibrationSchedule.objects.select_related(
        'equipment__department', 'equipment__description'
    ).filter(
        status__in=['pending', 'pushed', 'in_progress'],
        equipment__active_status=True,
    )
    qs = _apply_ajax_common_filters(qs, request, access_context)
    qs = qs.order_by('scheduled_month', 'equipment__department__name')

    paginator = Paginator(qs, 25)
    page = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    results = []
    for s in page_obj:
        m = s.scheduled_month
        is_overdue = m and m < today.replace(day=1)
        is_warning = m and (today.replace(day=1) <= m <= today.replace(day=1) + timedelta(days=30))
        results.append(_ajax_schedule_to_dict(s, is_overdue=is_overdue, is_warning=is_warning))

    meta = {
        'count': paginator.count,
        'num_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'overdue_count': qs.filter(scheduled_month__lt=today.replace(day=1)).count(),
    }

    return JsonResponse({'results': results, 'meta': meta})


@login_required
def ajax_completed_schedules(request):
    access_context = get_user_access_context(request)
    if access_context is None:
        return _no_access_response()
    thirty_days_ago = datetime.today().date() - timedelta(days=30)

    qs = CalibrationSchedule.objects.select_related(
        'equipment__department', 'equipment__description'
    ).filter(
        status='completed',
        completed_date__gte=thirty_days_ago,
        equipment__active_status=True,
    )

    if access_context['access_type'] == 'department':
        qs = qs.filter(equipment__department_id=access_context['department_id'])
    else:
        dept = request.GET.get('department', '').strip()
        if dept:
            qs = qs.filter(equipment__department_id=dept)

    search = request.GET.get('search', '').strip()
    if search:
        qs = qs.filter(
            Q(equipment__description__name__icontains=search) |
            Q(equipment__model__icontains=search) |
            Q(equipment__serial_number__icontains=search) |
            Q(equipment__department__name__icontains=search)
        )

    qs = qs.order_by('-completed_date', 'equipment__department__name')

    paginator = Paginator(qs, 25)
    page = request.GET.get('cpage', 1)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    results = [_ajax_schedule_to_dict(s) for s in page_obj]

    meta = {
        'count': paginator.count,
        'num_pages': paginator.num_pages,
        'current_page': page_obj.number,
    }

    return JsonResponse({'results': results, 'meta': meta})


@login_required
def ajax_unscheduled_equipment(request):
    access_context = get_user_access_context(request)
    if access_context is None:
        return _no_access_response()

    scheduled_ids = CalibrationSchedule.objects.filter(
        equipment__active_status=True,
        status__in=['pending', 'pushed', 'in_progress', 'overdue'],
    ).values_list('equipment_id', flat=True)

    qs = Equipment.objects.filter(active_status=True).exclude(id__in=scheduled_ids).select_related('department', 'description')

    if access_context['access_type'] == 'department':
        qs = qs.filter(department_id=access_context['department_id'])
    else:
        dept = request.GET.get('department', '').strip()
        if dept:
            qs = qs.filter(department_id=dept)

    search = request.GET.get('search', '').strip()
    if search:
        qs = qs.filter(
            Q(description__name__icontains=search) |
            Q(model__icontains=search) |
            Q(serial_number__icontains=search) |
            Q(department__name__icontains=search)
        )

    qs = qs.order_by('department__name', 'description__name')

    paginator = Paginator(qs, 25)
    page = request.GET.get('upage', 1)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    results = [
        {
            'id': eq.id,
            'description': eq.description.name if eq.description else '',
            'serial_number': eq.serial_number or '',
            'model': eq.model or '',
            'department_name': eq.department.name if eq.department else '',
        }
        for eq in page_obj
    ]

    meta = {
        'count': paginator.count,
        'num_pages': paginator.num_pages,
        'current_page': page_obj.number,
    }

    return JsonResponse({'results': results, 'meta': meta})


@login_required
def ajax_schedule_stats(request):
    access_context = get_user_access_context(request)
    if access_context is None:
        return _no_access_response()
    today = datetime.today().date()

    base = CalibrationSchedule.objects.filter(equipment__active_status=True)
    if access_context['access_type'] == 'department':
        base = base.filter(equipment__department_id=access_context['department_id'])
    elif request.GET.get('department'):
        base = base.filter(equipment__department_id=request.GET['department'])

    thirty_ago = today - timedelta(days=30)

    return JsonResponse({
        'total': base.exclude(status='completed').count(),
        'pending': base.filter(status__in=['pending', 'pushed', 'in_progress']).count(),
        'overdue': base.filter(status='overdue').count(),
        'completed': base.filter(status='completed', completed_date__gte=thirty_ago).count(),
        'warnings': base.exclude(logic_change_warning='').exclude(logic_change_warning__isnull=True).count(),
    })
