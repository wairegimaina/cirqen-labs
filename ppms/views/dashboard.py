"""ppms.views — main PPM dashboard and department-filter clearing."""
from calendar import monthrange
from django.db.models import Count
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.timezone import now
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

# sibling modules in this package
from .helpers import scheduling_context, calculate_ppm_statistics, get_current_month_year, get_user_access_context


@login_required
def ppm_dashboard(request):
    """
    Enhanced PPM Dashboard with real statistics
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"No access context for user {request.user.username}")
        messages.error(request, "No workshop or department access found. Please contact administrator.")
        return redirect('custom_login')

    if access_context['workshop_id']:
        request.session['workshop_id'] = str(access_context['workshop_id'])
    if access_context['department_id']:
        request.session['department_id'] = str(access_context['department_id'])
    request.session.modified = True

    # Get filter parameters
    current_month, current_year = get_current_month_year()
    month_filter = request.GET.get('month', str(current_month))
    year_filter = request.GET.get('year', str(current_year))
    week_filter = request.GET.get('week', '')  # '' = all weeks (month view)

    # Build a list of ISO weeks for the selected month/year (for the UI picker)
    try:
        _m, _y = int(month_filter), int(year_filter)
        from calendar import monthrange as _mr
        _first = datetime(_y, _m, 1)
        _last_day = _mr(_y, _m)[1]
        _last = datetime(_y, _m, _last_day)
        # collect unique ISO week numbers that overlap this month
        _week_numbers = []
        _cur = _first
        while _cur <= _last:
            _wn = _cur.isocalendar()[1]
            if _wn not in _week_numbers:
                _week_numbers.append(_wn)
            _cur += timedelta(days=7 - _cur.weekday())
        weeks_in_month = _week_numbers
    except (ValueError, TypeError):
        weeks_in_month = []

    # Create months list with numbers and names
    months_list = [
        {'number': i, 'name': datetime(2000, i, 1).strftime('%B')}
        for i in range(1, 13)
    ]

    if access_context['access_type'] == 'department':
        departments = Department.objects.filter(
            id=access_context['department_id'],
            active_status=True
        )

        schedules = PPMSchedule.objects.select_related(
            'equipment__department',
            'equipment__description'
        ).filter(
            equipment__department_id=access_context['department_id'],
            equipment__department__workshop_id=access_context['workshop_id'],
            equipment__active_status=True,
            workshop_id=access_context['workshop_id']
        ).order_by('scheduled_month', 'equipment__description__name')

        equipment_queryset = Equipment.objects.filter(
            department_id=access_context['department_id'],
            department__workshop_id=access_context['workshop_id'],
            active_status=True
        )

    else:  # Workshop-level access
        workshop_id = access_context['workshop_id']
        departments = Department.objects.filter(
            workshop_id=workshop_id,
            active_status=True
        )

        schedules = PPMSchedule.objects.select_related(
            'equipment__department',
            'equipment__description'
        ).filter(
            workshop_id=workshop_id,
            equipment__department__workshop_id=workshop_id,
            equipment__active_status=True
        ).order_by('equipment__department__name', 'scheduled_month', 'equipment__description__name')

        equipment_queryset = Equipment.objects.filter(
            department__workshop_id=workshop_id,
            active_status=True
        )

    # Calculate statistics BEFORE filtering by month/year for overall stats
    all_schedules_stats = calculate_ppm_statistics(schedules, equipment_queryset)

    # Now apply month/year filters for display
    filtered_schedules = schedules
    if month_filter:
        filtered_schedules = filtered_schedules.filter(scheduled_month__month=int(month_filter))
    if year_filter:
        filtered_schedules = filtered_schedules.filter(scheduled_month__year=int(year_filter))

    # Apply week filter (ISO week number within the selected month/year)
    if week_filter:
        try:
            _target_week = int(week_filter)
            _target_year = int(year_filter)
            # Collect IDs whose scheduled_month falls in that ISO week
            _week_ids = [
                s.id for s in filtered_schedules
                if s.scheduled_month and s.scheduled_month.isocalendar()[1] == _target_week
                and s.scheduled_month.isocalendar()[0] == _target_year
            ]
            filtered_schedules = filtered_schedules.filter(id__in=_week_ids)
        except (ValueError, TypeError):
            pass  # ignore invalid week values

    selected_department_id = request.GET.get('department')
    show_all = request.GET.get('show_all') == 'true'
    selected_department = None

    if selected_department_id and not show_all and access_context['access_type'] == 'workshop':
        selected_department = get_object_or_404(
            Department,
            id=selected_department_id,
            workshop_id=access_context['workshop_id']
        )
        filtered_schedules = filtered_schedules.filter(
            equipment__department_id=selected_department_id,
            department_id=selected_department_id
        )

    # Get unscheduled equipment
    scheduled_equipment_ids = schedules.values_list('equipment_id', flat=True)
    unscheduled_equipment = equipment_queryset.exclude(
        id__in=scheduled_equipment_ids
    ).select_related('department', 'description')

    if selected_department:
        unscheduled_equipment = unscheduled_equipment.filter(
            department_id=selected_department_id
        )

    equipment_descriptions = EquipmentDescription.objects.filter(
        equipment__in=equipment_queryset
    ).distinct()

    access_context_serializable = {
        'workshop_id': str(access_context['workshop_id']) if access_context.get('workshop_id') else None,
        'department_id': str(access_context['department_id']) if access_context.get('department_id') else None,
        'access_type': access_context['access_type']
    }

    # Get selected month name
    try:
        selected_month_name = datetime(2000, int(month_filter), 1).strftime('%B')
    except (ValueError, TypeError):
        selected_month_name = datetime(2000, current_month, 1).strftime('%B')

    return render(request, 'PPM/ppm.html', {
        **scheduling_context(request, access_context),
        'schedules': filtered_schedules,
        'departments': departments,
        'selected_department': selected_department,
        'selected_department_id': selected_department_id if not show_all else None,
        'unscheduled_equipment': unscheduled_equipment,
        'equipment_descriptions': equipment_descriptions,
        'access_context': access_context_serializable,
        'user': request.user,
        'selected_month': month_filter,
        'selected_year': year_filter,
        'selected_week': week_filter,
        'weeks_in_month': weeks_in_month,
        'current_month': current_month,
        'current_year': current_year,
        'statistics': all_schedules_stats,  # Pass overall statistics
        'months_list': months_list,
        'selected_month_name': selected_month_name,
        'show_sidebar': True,  # Added sidebar context
    })


@login_required
def clear_department_filter(request):
    return redirect('ppm_dashboard')
