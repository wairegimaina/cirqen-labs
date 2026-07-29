"""ppms.views — per-department PPM schedule view."""
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
from ..tasks import initialize_ppm_schedule_with_logic, normalize_ppm_schedules, smart_reorganize_ppm_schedules
from openpyxl import Workbook
from workshop.models import Workshop
import logging
from django.utils import timezone
from ..ppm_pdf_generator import create_ppm_pdf_response
from celery.result import AsyncResult
logger = logging.getLogger(__name__)

# sibling modules in this package
from .helpers import calculate_ppm_statistics, get_current_month_year, get_user_access_context


@login_required
def ppm_by_department(request, dept_id):
    """
    Department-specific PPM view with transfer support and AJAX compatibility
    Handles both regular page loads and AJAX requests for smooth filtering
    """
    # ==================== ACCESS CONTROL ====================
    access_context = get_user_access_context(request)

    if not access_context:
        logger.warning(f"No access context for user {request.user.username}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'error': 'No access context found'}, status=403)
        messages.error(request, "No workshop or department access found. Please contact administrator.")
        return redirect('custom_login')

    # Log access attempt
    logger.info(
        f"User {request.user.username} accessing department {dept_id} "
        f"(access_type: {access_context['access_type']}, "
        f"workshop: {access_context.get('workshop_id')}, "
        f"department: {access_context.get('department_id')})"
    )

    # ==================== DEPARTMENT ACCESS VALIDATION ====================
    try:
        selected_department = get_object_or_404(Department, id=dept_id, active_status=True)
    except Exception as e:
        logger.error(f"Department {dept_id} not found or inactive: {e}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'error': 'Department not found'}, status=404)
        messages.error(request, "Department not found or is inactive.")
        return redirect('ppm_dashboard')

    # Verify user has access to this department
    if access_context['access_type'] == 'department':
        # Department-level users can only see their own department
        if str(access_context['department_id']) != str(dept_id):
            logger.warning(
                f"Access denied: User {request.user.username} tried to access "
                f"department {dept_id} but only has access to {access_context['department_id']}"
            )
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'error': 'Access denied to this department'}, status=403)
            messages.error(request, "You don't have access to this department.")
            return redirect('ppm_dashboard')

        departments = Department.objects.filter(
            id=dept_id,
            active_status=True
        )
    else:
        # Workshop-level users can see all departments in their workshop
        if selected_department.workshop_id != access_context['workshop_id']:
            logger.warning(
                f"Access denied: User {request.user.username} tried to access "
                f"department {dept_id} (workshop {selected_department.workshop_id}) "
                f"but only has access to workshop {access_context['workshop_id']}"
            )
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'error': 'Department belongs to different workshop'}, status=403)
            messages.error(request, "You don't have access to this department.")
            return redirect('ppm_dashboard')

        departments = Department.objects.filter(
            workshop_id=access_context['workshop_id'],
            active_status=True
        )

    # ==================== GET FILTER PARAMETERS ====================
    current_month, current_year = get_current_month_year()
    month_filter = request.GET.get('month', str(current_month))
    year_filter = request.GET.get('year', str(current_year))
    week_filter = request.GET.get('week', '')  # '' = all weeks

    # Build ISO week list for the selected month/year
    try:
        _m, _y = int(month_filter), int(year_filter)
        from calendar import monthrange as _mr
        _first = datetime(_y, _m, 1)
        _last_day = _mr(_y, _m)[1]
        _last = datetime(_y, _m, _last_day)
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

    # Validate filter parameters
    try:
        month_filter_int = int(month_filter)
        year_filter_int = int(year_filter)

        if not (1 <= month_filter_int <= 12):
            month_filter = str(current_month)
            month_filter_int = current_month

        if not (2000 <= year_filter_int <= 2100):
            year_filter = str(current_year)
            year_filter_int = current_year

    except (ValueError, TypeError):
        logger.warning(f"Invalid filter parameters: month={month_filter}, year={year_filter}")
        month_filter = str(current_month)
        year_filter = str(current_year)
        month_filter_int = current_month
        year_filter_int = current_year

    # ==================== QUERY SCHEDULES ====================
    try:
        # Base queryset for all schedules in this department
        # ✅ FIX: Use equipment__department_id instead of department_id
        schedules_base = PPMSchedule.objects.select_related(
            'equipment__department',
            'equipment__description'
        ).filter(
            equipment__department_id=dept_id,  # ✅ Through equipment relationship
            equipment__department__workshop_id=access_context['workshop_id'],
            workshop_id=access_context['workshop_id'],
            equipment__active_status=True
        )

        # Equipment queryset for statistics
        equipment_queryset = Equipment.objects.filter(
            department_id=dept_id,
            department__workshop_id=access_context['workshop_id'],
            active_status=True
        )

        # Calculate statistics BEFORE filtering by month/year
        statistics = calculate_ppm_statistics(schedules_base, equipment_queryset)

        # NOW apply month/year filters for display
        schedules_filtered = schedules_base.filter(
            scheduled_month__month=month_filter_int,
            scheduled_month__year=year_filter_int
        ).order_by('scheduled_month', 'equipment__description__name')

        # Apply week filter (ISO week within selected month/year)
        if week_filter:
            try:
                _target_week = int(week_filter)
                _week_ids = [
                    s.id for s in schedules_filtered
                    if s.scheduled_month
                    and s.scheduled_month.isocalendar()[1] == _target_week
                    and s.scheduled_month.isocalendar()[0] == year_filter_int
                ]
                schedules_filtered = schedules_filtered.filter(id__in=_week_ids)
            except (ValueError, TypeError):
                pass

        logger.info(
            f"Department {selected_department.name}: "
            f"Total schedules: {schedules_base.count()}, "
            f"Filtered ({month_filter}/{year_filter}): {schedules_filtered.count()}"
        )

    except Exception as e:
        logger.error(f"Error querying schedules for department {dept_id}: {e}", exc_info=True)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'error': f'Database error: {str(e)}'}, status=500)
        messages.error(request, "Error loading schedule data. Please try again.")
        return redirect('ppm_dashboard')

    # ==================== GET UNSCHEDULED EQUIPMENT ====================
    try:
        # Get IDs of all scheduled equipment (not just filtered ones)
        scheduled_equipment_ids = schedules_base.values_list('equipment_id', flat=True).distinct()

        unscheduled_equipment = Equipment.objects.filter(
            department_id=dept_id,
            department__workshop_id=access_context['workshop_id'],
            active_status=True
        ).exclude(
            id__in=scheduled_equipment_ids
        ).select_related('department', 'description').order_by('description__name')

        logger.info(f"Unscheduled equipment in {selected_department.name}: {unscheduled_equipment.count()}")

    except Exception as e:
        logger.error(f"Error querying unscheduled equipment: {e}", exc_info=True)
        unscheduled_equipment = Equipment.objects.none()

    # ==================== GET EQUIPMENT DESCRIPTIONS ====================
    try:
        equipment_descriptions = EquipmentDescription.objects.filter(
            equipment__department_id=dept_id,
            equipment__department__workshop_id=access_context['workshop_id'],
            equipment__active_status=True
        ).distinct().order_by('name')
    except Exception as e:
        logger.error(f"Error querying equipment descriptions: {e}", exc_info=True)
        equipment_descriptions = EquipmentDescription.objects.none()

    # ==================== HANDLE AJAX REQUESTS ====================
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        logger.info(f"Returning JSON response for AJAX request to department {dept_id}")

        try:
            # Prepare schedule data for JSON
            schedules_data = []
            for schedule in schedules_filtered:
                schedules_data.append({
                    'id': str(schedule.id),
                    'equipment_name': schedule.equipment.description.name if schedule.equipment.description else 'N/A',
                    'equipment_model': schedule.equipment.model or 'N/A',
                    'equipment_serial': schedule.equipment.serial_number or 'N/A',
                    'status': schedule.status,
                    'scheduled_month': schedule.scheduled_month.strftime('%B %Y') if schedule.scheduled_month else 'N/A',
                    'maintenance_period': schedule.maintenance_period,
                })

            # Prepare unscheduled equipment data
            unscheduled_data = []
            for equipment in unscheduled_equipment:
                unscheduled_data.append({
                    'id': str(equipment.id),
                    'name': equipment.description.name if equipment.description else 'N/A',
                    'model': equipment.model or 'N/A',
                    'serial_number': equipment.serial_number or 'N/A',
                })

            return JsonResponse({
                'success': True,
                'department': {
                    'id': str(selected_department.id),
                    'name': selected_department.name,
                },
                'filters': {
                    'month': month_filter,
                    'year': year_filter,
                },
                'statistics': statistics,
                'schedules': schedules_data,
                'unscheduled': unscheduled_data,
                'counts': {
                    'total_schedules': schedules_base.count(),
                    'filtered_schedules': schedules_filtered.count(),
                    'unscheduled_equipment': unscheduled_equipment.count(),
                    'total_equipment': equipment_queryset.count(),
                }
            })
        except Exception as e:
            logger.error(f"Error preparing JSON response: {e}", exc_info=True)
            return JsonResponse({
                'success': False,
                'error': f'Error preparing data: {str(e)}'
            }, status=500)

    # ==================== RENDER HTML TEMPLATE ====================
    try:
        context = {
            'schedules': schedules_filtered,
            'departments': departments,
            'selected_department': selected_department,
            'selected_department_id': selected_department.id,
            'unscheduled_equipment': unscheduled_equipment,
            'equipment_descriptions': equipment_descriptions,
            'title': f"Equipment in {selected_department.name}",
            'access_context': {
                'workshop_id': str(access_context['workshop_id']) if access_context.get('workshop_id') else None,
                'department_id': str(access_context['department_id']) if access_context.get('department_id') else None,
                'access_type': access_context['access_type'],
                'can_edit': access_context.get('can_edit', False),
                'can_schedule': access_context.get('can_schedule', False),
            },
            'user': request.user,
            'selected_month': month_filter,
            'selected_year': year_filter,
            'selected_week': week_filter,
            'weeks_in_month': weeks_in_month,
            'current_month': current_month,
            'current_year': current_year,
            'statistics': statistics,
            'show_sidebar': True,  # Added sidebar context
        }

        logger.info(f"Rendering template for department {selected_department.name}")
        return render(request, 'PPM/ppm.html', context)

    except Exception as e:
        logger.error(f"Error rendering template for department {dept_id}: {e}", exc_info=True)
        messages.error(request, "Error displaying page. Please try again.")
        return redirect('ppm_dashboard')
