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
from .models import PPMSchedule
from Inventory.models import Equipment, Department, EquipmentDescription
from .tasks import initialize_ppm_schedule_with_logic, normalize_ppm_schedules, smart_reorganize_ppm_schedules
from openpyxl import Workbook
from workshop.models import Workshop
import logging
from django.utils import timezone
from .ppm_pdf_generator import create_ppm_pdf_response
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


def get_current_month_year():
    """Returns current month and year as integers"""
    today = datetime.today()
    return today.month, today.year

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
def get_analytics_data(request):
    """
    API endpoint to fetch PPM analytics data
    Returns comprehensive statistics for dashboard visualization
    """
    access_context = get_user_access_context(request)
    if not access_context:
        return JsonResponse({'error': 'Access denied'}, status=403)

    today = now().date()
    current_month_start = today.replace(day=1)

    # Base queryset based on access level
    if access_context['access_type'] == 'department':
        base_schedules = PPMSchedule.objects.filter(
            equipment__department_id=access_context['department_id'],
            equipment__department__workshop_id=access_context['workshop_id'],
            workshop_id=access_context['workshop_id'],
            equipment__active_status=True
        )
        total_equipment = Equipment.objects.filter(
            department_id=access_context['department_id'],
            active_status=True
        ).count()
    else:
        base_schedules = PPMSchedule.objects.filter(
            workshop_id=access_context['workshop_id'],
            equipment__department__workshop_id=access_context['workshop_id'],
            equipment__active_status=True
        )
        total_equipment = Equipment.objects.filter(
            department__workshop_id=access_context['workshop_id'],
            active_status=True
        ).count()

    # Calculate overdue schedules - NOT completed and past the LAST DAY of scheduled month
    overdue_schedules = []
    pending_schedules = []

    for schedule in base_schedules.select_related('equipment', 'equipment__department', 'equipment__description'):
        if schedule.scheduled_month:
            # Get the LAST DAY of the scheduled month
            last_day = monthrange(schedule.scheduled_month.year, schedule.scheduled_month.month)[1]
            month_end_date = schedule.scheduled_month.replace(day=last_day)

            if schedule.status != 'completed':
                # Overdue if today is AFTER the last day of the scheduled month
                if today > month_end_date:
                    overdue_schedules.append(schedule)
                elif schedule.status == 'pending':
                    pending_schedules.append(schedule)

    # Status breakdown
    status_counts = base_schedules.values('status').annotate(
        count=Count('id')
    ).order_by('status')

    # Monthly distribution (next 12 months)
    monthly_data = []
    for i in range(12):
        month_date = current_month_start + relativedelta(months=i)
        month_schedules = base_schedules.filter(
            scheduled_month__year=month_date.year,
            scheduled_month__month=month_date.month
        )

        # Count overdue for this specific month
        month_overdue = 0
        for s in month_schedules:
            if s.scheduled_month and s.status != 'completed':
                last_day = monthrange(s.scheduled_month.year, s.scheduled_month.month)[1]
                month_end_date = s.scheduled_month.replace(day=last_day)
                if today > month_end_date:
                    month_overdue += 1

        monthly_data.append({
            'month': month_date.strftime('%b %Y'),
            'total': month_schedules.count(),
            'completed': month_schedules.filter(status='completed').count(),
            'pending': month_schedules.filter(status='pending').count(),
            'pushed': month_schedules.filter(status='pushed').count(),
            'overdue': month_overdue
        })

    # Department breakdown (for workshop-level users)
    department_data = []
    if access_context['access_type'] == 'workshop':
        departments = Department.objects.filter(
            workshop_id=access_context['workshop_id'],
            active_status=True
        )

        for dept in departments:
            dept_schedules = base_schedules.filter(equipment__department=dept)
            dept_equipment = Equipment.objects.filter(
                department=dept,
                active_status=True
            ).count()

            # Count overdue for this department
            dept_overdue = 0
            for s in dept_schedules:
                if s.scheduled_month and s.status != 'completed':
                    last_day = monthrange(s.scheduled_month.year, s.scheduled_month.month)[1]
                    month_end_date = s.scheduled_month.replace(day=last_day)
                    if today > month_end_date:
                        dept_overdue += 1

            department_data.append({
                'name': dept.name,
                'total_equipment': dept_equipment,
                'scheduled': dept_schedules.count(),
                'completed': dept_schedules.filter(status='completed').count(),
                'pending': dept_schedules.filter(status='pending').count(),
                'overdue': dept_overdue
            })

    # Equipment type breakdown
    equipment_type_data = []
    descriptions = EquipmentDescription.objects.filter(
        equipment__department__workshop_id=access_context['workshop_id'],
        equipment__active_status=True
    ).distinct()

    for desc in descriptions[:10]:  # Top 10 equipment types
        type_schedules = base_schedules.filter(equipment__description=desc)
        equipment_type_data.append({
            'name': desc.name,
            'scheduled': type_schedules.count(),
            'completed': type_schedules.filter(status='completed').count(),
            'pending': type_schedules.filter(status='pending').count()
        })

    # Compliance rate calculation
    total_scheduled = base_schedules.count()
    completed_on_time = 0

    for schedule in base_schedules.filter(status='completed'):
        if schedule.scheduled_month:
            last_day = monthrange(schedule.scheduled_month.year, schedule.scheduled_month.month)[1]
            month_end_date = schedule.scheduled_month.replace(day=last_day)
            # Completed on time if done before or on the last day of scheduled month
            if schedule.updated_at and schedule.updated_at.date() <= month_end_date:
                completed_on_time += 1

    compliance_rate = (completed_on_time / total_scheduled * 100) if total_scheduled > 0 else 0

    # Upcoming maintenance (current month and not completed)
    upcoming_schedules = []

    for schedule in base_schedules.filter(status='pending').select_related('equipment', 'equipment__description', 'equipment__department'):
        if schedule.scheduled_month:
            # Include if scheduled for current month
            if schedule.scheduled_month.year == today.year and schedule.scheduled_month.month == today.month:
                last_day = monthrange(schedule.scheduled_month.year, schedule.scheduled_month.month)[1]
                month_end_date = schedule.scheduled_month.replace(day=last_day)
                days_remaining = (month_end_date - today).days

                upcoming_schedules.append({
                    'id': schedule.id,
                    'equipment': schedule.equipment.description.name if schedule.equipment.description else 'N/A',
                    'department': schedule.equipment.department.name if schedule.equipment.department else 'N/A',
                    'scheduled_date': schedule.scheduled_month.strftime('%Y-%m-%d'),
                    'days_remaining': days_remaining
                })

    response_data = {
        'summary': {
            'total_equipment': total_equipment,
            'total_scheduled': total_scheduled,
            'unscheduled': total_equipment - len(set(base_schedules.values_list('equipment_id', flat=True))),
            'completed': base_schedules.filter(status='completed').count(),
            'pending': len(pending_schedules),
            'overdue': len(overdue_schedules),
            'compliance_rate': round(compliance_rate, 2)
        },
        'status_distribution': list(status_counts),
        'monthly_trend': monthly_data,
        'department_breakdown': department_data,
        'equipment_types': equipment_type_data,
        'upcoming_maintenance': sorted(upcoming_schedules, key=lambda x: x['days_remaining'])[:10],
        'overdue_list': [{
            'id': s.id,
            'equipment': s.equipment.description.name if s.equipment.description else 'N/A',
            'department': s.equipment.department.name if s.equipment.department else 'N/A',
            'scheduled_date': s.scheduled_month.strftime('%Y-%m-%d'),
            'days_overdue': (today - s.scheduled_month.replace(day=monthrange(s.scheduled_month.year, s.scheduled_month.month)[1])).days,
            'status': s.status
        } for s in overdue_schedules[:10]]
    }

    return JsonResponse(response_data, safe=False)

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

def calculate_ppm_statistics(schedules, equipment_queryset):
    """
    Calculate real PPM statistics based on actual data
    Returns dict with all necessary statistics
    """
    today = now().date()
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
                    if schedule.updated_at and schedule.updated_at.date() <= month_end_date:
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

@login_required
def get_ppm_summary_api(request):
    """
    API endpoint for PPM summary statistics (similar to inventory summary)
    """
    access_context = get_user_access_context(request)
    if not access_context:
        return JsonResponse({'error': 'Access denied'}, status=403)

    # Get base queryset based on access level
    if access_context['access_type'] == 'department':
        schedules = PPMSchedule.objects.filter(
            equipment__department_id=access_context['department_id'],
            equipment__active_status=True,
            workshop_id=access_context['workshop_id']
        )
        equipment_queryset = Equipment.objects.filter(
            department_id=access_context['department_id'],
            active_status=True
        )
    else:
        schedules = PPMSchedule.objects.filter(
            workshop_id=access_context['workshop_id'],
            equipment__active_status=True
        )
        equipment_queryset = Equipment.objects.filter(
            department__workshop_id=access_context['workshop_id'],
            active_status=True
        )

    # Calculate statistics
    statistics = calculate_ppm_statistics(schedules, equipment_queryset)

    # Get department breakdown
    department_breakdown = []
    departments = Department.objects.filter(
        workshop_id=access_context['workshop_id'],
        active_status=True
    ) if access_context['access_type'] == 'workshop' else Department.objects.filter(
        id=access_context['department_id'],
        active_status=True
    )

    for dept in departments:
        dept_schedules = schedules.filter(equipment__department=dept)
        dept_equipment = equipment_queryset.filter(department=dept).count()

        dept_pending = dept_schedules.filter(status='pending').count()
        dept_completed = dept_schedules.filter(status='completed').count()
        dept_pushed = dept_schedules.filter(status='pushed').count()
        dept_total = dept_schedules.count()

        department_breakdown.append({
            'department': dept.name,
            'total_equipment': dept_equipment,
            'scheduled': dept_total,
            'pending': dept_pending,
            'completed': dept_completed,
            'pushed': dept_pushed,
            'completion_percentage': round((dept_completed / dept_total * 100) if dept_total > 0 else 0, 1)
        })

    return JsonResponse({
        'statistics': statistics,
        'department_breakdown': department_breakdown
    })


# ==================== PPM TRIGGER ====================
@login_required
def trigger_initialization(request):
    """Initialize PPM schedules with comprehensive error handling and logging"""
    # Generate unique request ID for tracking
    import uuid
    request_id = str(uuid.uuid4())[:8]

    logger.info(f"{'='*80}")
    logger.info(f"[REQ-{request_id}] PPM INITIALIZATION REQUEST STARTED")
    logger.info(f"[REQ-{request_id}] User: {request.user.username} (ID: {request.user.id})")
    logger.info(f"[REQ-{request_id}] Method: {request.method}")
    logger.info(f"[REQ-{request_id}] Path: {request.path}")
    logger.info(f"[REQ-{request_id}] IP: {request.META.get('REMOTE_ADDR', 'Unknown')}")
    logger.info(f"{'='*80}")

    access_context = get_user_access_context(request)

    if not access_context or not access_context['workshop_id'] or not access_context.get('can_schedule', False):
        logger.warning(
            f"[REQ-{request_id}] ❌ ACCESS DENIED: "
            f"user={request.user.username}, "
            f"has_context={access_context is not None}, "
            f"workshop_id={access_context.get('workshop_id') if access_context else None}, "
            f"can_schedule={access_context.get('can_schedule') if access_context else False}"
        )
        messages.error(request, "You don't have permission to initialize schedules.")
        return redirect('ppm_dashboard')

    workshop_id = access_context['workshop_id']
    request.session['workshop_id'] = str(workshop_id)
    request.session.modified = True

    logger.info(f"[REQ-{request_id}] ✅ ACCESS GRANTED")
    logger.info(f"[REQ-{request_id}] Workshop ID: {workshop_id}")
    logger.info(f"[REQ-{request_id}] Access Type: {access_context['access_type']}")
    logger.info(f"[REQ-{request_id}] Department ID: {access_context.get('department_id', 'N/A')}")
    logger.info(f"[REQ-{request_id}] Role: {access_context.get('role', 'N/A')}")
    logger.info(f"[REQ-{request_id}] Level: {access_context.get('level', 'N/A')}")

    if request.method == 'POST':
        logger.info(f"[REQ-{request_id}] {'─'*80}")
        logger.info(f"[REQ-{request_id}] PHASE 1: CELERY HEALTH CHECK")
        logger.info(f"[REQ-{request_id}] {'─'*80}")

        # ✅ CELERY HEALTH CHECK - Before processing any form data
        from celery import current_app
        from celery.app.control import Inspect
        import time

        health_check_start = time.time()

        try:
            logger.info(f"[REQ-{request_id}] Connecting to Celery broker...")
            logger.debug(f"[REQ-{request_id}] Broker URL: {current_app.conf.broker_url}")
            logger.debug(f"[REQ-{request_id}] Result Backend: {current_app.conf.result_backend}")

            # ✅ Check if Celery is properly installed
            try:
                import celery
                logger.debug(f"[REQ-{request_id}] Celery version: {celery.__version__}")
            except Exception as version_error:
                logger.warning(f"[REQ-{request_id}] Could not determine Celery version: {version_error}")

            # ✅ Test basic Celery connection first
            try:
                logger.info(f"[REQ-{request_id}] Testing Celery app connection...")
                # Simple connection test
                with current_app.connection_or_acquire() as conn:
                    conn.ensure_connection(max_retries=3, timeout=2)
                logger.info(f"[REQ-{request_id}] ✅ Broker connection successful")
            except Exception as conn_error:
                logger.error(f"[REQ-{request_id}] ❌ BROKER CONNECTION FAILED")
                logger.error(f"[REQ-{request_id}] Error: {str(conn_error)}")
                messages.error(
                    request,
                    f"Cannot connect to message broker: {str(conn_error)}. "
                    f"Please ensure Redis/RabbitMQ is running and accessible.",
                    extra_tags="celery broker"
                )
                return redirect('ppm_dashboard')

            i = Inspect(app=current_app)

            logger.info(f"[REQ-{request_id}] Checking worker stats...")

            # ✅ FIX: Remove timeout parameter - use default timeout
            stats = i.stats()

            health_check_duration = time.time() - health_check_start
            logger.info(f"[REQ-{request_id}] Health check completed in {health_check_duration:.2f}s")

            if not stats:
                logger.error(f"[REQ-{request_id}] ❌ CELERY HEALTH CHECK FAILED: No workers available")

                # Try to get more diagnostic info
                try:
                    active = i.active()
                    registered = i.registered()
                    logger.error(f"[REQ-{request_id}] Active tasks: {active}")
                    logger.error(f"[REQ-{request_id}] Registered tasks: {list(registered.keys()) if registered else 'None'}")
                except Exception as diag_error:
                    logger.error(f"[REQ-{request_id}] Could not retrieve diagnostic info: {diag_error}")

                messages.error(
                    request,
                    "No Celery workers are running. Please start a Celery worker with: "
                    "celery -A your_project worker -l info",
                    extra_tags="celery worker"
                )
                return redirect('ppm_dashboard')

            # Log detailed worker information
            logger.info(f"[REQ-{request_id}] ✅ CELERY HEALTH CHECK PASSED")
            logger.info(f"[REQ-{request_id}] Active workers: {len(stats)}")

            for worker_name, worker_stats in stats.items():
                logger.info(f"[REQ-{request_id}]   Worker: {worker_name}")
                logger.debug(f"[REQ-{request_id}]     Pool: {worker_stats.get('pool', {}).get('implementation', 'N/A')}")
                logger.debug(f"[REQ-{request_id}]     Max concurrency: {worker_stats.get('pool', {}).get('max-concurrency', 'N/A')}")
                logger.debug(f"[REQ-{request_id}]     Total tasks: {worker_stats.get('total', {})}")

            # Check active queues and registered tasks
            try:
                active_queues = i.active_queues()
                if active_queues:
                    logger.info(f"[REQ-{request_id}] Active queues detected:")
                    for worker, queues in active_queues.items():
                        logger.info(f"[REQ-{request_id}]   {worker}: {[q['name'] for q in queues]}")

                registered = i.registered()
                if registered:
                    logger.debug(f"[REQ-{request_id}] Registered tasks: {sum(len(tasks) for tasks in registered.values())} total")
            except Exception as queue_error:
                logger.debug(f"[REQ-{request_id}] Could not retrieve queue info: {queue_error}")

        except Exception as e:
            health_check_duration = time.time() - health_check_start
            logger.error(f"[REQ-{request_id}] ❌ CELERY CONNECTION ERROR (after {health_check_duration:.2f}s)")
            logger.error(f"[REQ-{request_id}] Error type: {type(e).__name__}")
            logger.error(f"[REQ-{request_id}] Error message: {str(e)}")
            logger.exception(f"[REQ-{request_id}] Full traceback:")

            # ✅ Provide specific guidance based on error type
            if "ModuleNotFoundError" in str(type(e).__name__) or "No module named" in str(e):
                error_msg = (
                    f"Celery installation is incomplete or corrupted: {str(e)}. "
                    f"Please reinstall Celery with: pip install --force-reinstall celery"
                )
            elif "Connection" in str(type(e).__name__) or "connection" in str(e).lower():
                error_msg = (
                    f"Cannot connect to message broker: {str(e)}. "
                    f"Please ensure Redis/RabbitMQ is running and accessible."
                )
            else:
                error_msg = f"Celery system error: {str(e)}. Please contact administrator."

            messages.error(request, error_msg, extra_tags="celery error")
            return redirect('ppm_dashboard')

        logger.info(f"[REQ-{request_id}] {'─'*80}")
        logger.info(f"[REQ-{request_id}] PHASE 2: FORM DATA PARSING")
        logger.info(f"[REQ-{request_id}] {'─'*80}")

        try:
            # Parse form data with validation
            try:
                planning_logic = request.POST.get('planning_logic', 'department')
                maintenance_period = int(request.POST.get('maintenance_period', 6))
                base_month = int(request.POST.get('base_month', 1))
                base_year = int(request.POST.get('base_year', datetime.today().year))
                max_departments = int(request.POST.get('max_departments', 100))
                max_descriptions = int(request.POST.get('max_descriptions', 100))
                preserve_existing = request.POST.get('preserve_existing') == 'true'
                selected_descriptions = request.POST.getlist('selected_descriptions')

                logger.info(f"[REQ-{request_id}] ✅ Form data parsed successfully")
                logger.info(f"[REQ-{request_id}]   Planning Logic: {planning_logic}")
                logger.info(f"[REQ-{request_id}]   Maintenance Period: {maintenance_period} months")
                logger.info(f"[REQ-{request_id}]   Base Date: {base_month}/{base_year}")
                logger.info(f"[REQ-{request_id}]   Max Departments: {max_departments}")
                logger.info(f"[REQ-{request_id}]   Max Descriptions: {max_descriptions}")
                logger.info(f"[REQ-{request_id}]   Preserve Existing: {preserve_existing}")
                logger.info(f"[REQ-{request_id}]   Selected Descriptions: {len(selected_descriptions)} items")

                if selected_descriptions:
                    logger.debug(f"[REQ-{request_id}]   Description IDs: {selected_descriptions[:10]}{'...' if len(selected_descriptions) > 10 else ''}")

            except (ValueError, TypeError) as e:
                logger.error(f"[REQ-{request_id}] ❌ FORM DATA VALIDATION ERROR")
                logger.error(f"[REQ-{request_id}] Error type: {type(e).__name__}")
                logger.error(f"[REQ-{request_id}] Error message: {str(e)}")
                logger.error(f"[REQ-{request_id}] POST data: {dict(request.POST)}")
                messages.error(request, f"Invalid input data: {str(e)}")
                return redirect('ppm_dashboard')

            logger.info(f"[REQ-{request_id}] {'─'*80}")
            logger.info(f"[REQ-{request_id}] PHASE 3: WORKSHOP & EQUIPMENT VALIDATION")
            logger.info(f"[REQ-{request_id}] {'─'*80}")

            # Verify workshop exists
            try:
                workshop = Workshop.objects.get(id=workshop_id)
                logger.info(f"[REQ-{request_id}] ✅ Workshop verified: '{workshop.name}' (ID: {workshop.id})")
            except Workshop.DoesNotExist:
                logger.error(f"[REQ-{request_id}] ❌ WORKSHOP NOT FOUND: {workshop_id}")
                messages.error(request, "Workshop not found. Please contact administrator.")
                return redirect('ppm_dashboard')

            # Get equipment filter for department-level users
            equipment_filter = None
            if access_context['access_type'] == 'department':
                logger.info(f"[REQ-{request_id}] Applying department-level filter...")
                equipment_filter = list(Equipment.objects.filter(
                    department_id=access_context['department_id'],
                    active_status=True
                ).values_list('id', flat=True))
                logger.info(f"[REQ-{request_id}] ✅ Department filter applied: {len(equipment_filter)} equipment items")
            else:
                logger.info(f"[REQ-{request_id}] Workshop-level access - no department filter")

            # Count active equipment
            if equipment_filter is not None:
                equipment_count = len(equipment_filter)
            else:
                equipment_count = Equipment.objects.filter(
                    workshop_id=workshop_id,
                    active_status=True
                ).count()

            logger.info(f"[REQ-{request_id}] Total active equipment: {equipment_count}")

            if equipment_count == 0:
                logger.warning(f"[REQ-{request_id}] ⚠️  NO ACTIVE EQUIPMENT FOUND")
                messages.warning(request, "No active equipment found to schedule.")
                return redirect('ppm_dashboard')

            # Check if equipment needs scheduling
            if preserve_existing:
                logger.info(f"[REQ-{request_id}] Checking existing schedules (preserve_existing=True)...")

                already_scheduled = PPMSchedule.objects.filter(
                    workshop_id=workshop_id,
                    equipment__active_status=True
                ).count()

                needs_scheduling = equipment_count - already_scheduled

                logger.info(f"[REQ-{request_id}]   Already scheduled: {already_scheduled}")
                logger.info(f"[REQ-{request_id}]   Needs scheduling: {needs_scheduling}")
                logger.info(f"[REQ-{request_id}]   Coverage: {(already_scheduled/equipment_count*100):.1f}%")

                if needs_scheduling <= 0:
                    logger.info(f"[REQ-{request_id}] ✅ All equipment already scheduled - nothing to do")
                    messages.info(request, "All active equipment is already scheduled.")
                    return redirect('ppm_dashboard')

            logger.info(f"[REQ-{request_id}] {'─'*80}")
            logger.info(f"[REQ-{request_id}] PHASE 4: CELERY TASK QUEUING")
            logger.info(f"[REQ-{request_id}] {'─'*80}")

            # Queue the Celery task
            try:
                task_queue_start = time.time()

                task_args = {
                    'workshop_id': str(workshop_id),
                    'planning_logic': planning_logic,
                    'maintenance_period': maintenance_period,
                    'base_month': base_month,
                    'base_year': base_year,
                    'max_departments': max_departments,
                    'max_descriptions': max_descriptions,
                    'selected_descriptions': selected_descriptions,
                    'preserve_existing': preserve_existing,
                    'equipment_filter': equipment_filter
                }

                logger.debug(f"[REQ-{request_id}] Task arguments prepared")
                logger.info(f"[REQ-{request_id}] Queuing task to Celery...")

                task = initialize_ppm_schedule_with_logic.delay(
                    str(workshop_id),
                    planning_logic,
                    maintenance_period,
                    base_month,
                    base_year,
                    max_departments,
                    max_descriptions,
                    selected_descriptions,
                    preserve_existing,
                )

                task_queue_duration = time.time() - task_queue_start
                logger.info(f"[REQ-{request_id}] ✅ TASK QUEUED in {task_queue_duration:.2f}s (ID: {task.id})")

                logger.info(f"[REQ-{request_id}] {'─'*80}")
                logger.info(f"[REQ-{request_id}] REQUEST COMPLETE - Redirecting user to dashboard")
                logger.info(f"[REQ-{request_id}] {'─'*80}")

                messages.success(
                    request,
                    f"PPM schedule initialization started for {workshop.name}! Task ID: {task.id}. "
                    f"The system will create {equipment_count} schedule(s) using {planning_logic} logic. "
                    f"You will receive a notification when the process completes.",
                    extra_tags="schedule success task"
                )

                return redirect('ppm_dashboard')

            except Exception as e:
                logger.error(f"[REQ-{request_id}] ❌ Failed to queue task: {str(e)}", exc_info=True)
                messages.error(
                    request,
                    f"Failed to queue schedule initialization: {str(e)}. "
                    f"Please check that a Celery worker is running.",
                    extra_tags="celery error"
                )
                return redirect('ppm_dashboard')

        except (ValueError, TypeError) as e:
            logger.error(f"[REQ-{request_id}] ❌ FORM DATA VALIDATION ERROR: {e}")
            messages.error(request, f"Invalid input data: {str(e)}")
            return redirect('ppm_dashboard')

    # GET request - just redirect to dashboard
    return redirect('ppm_dashboard')


# ==================== PPM SMART REORGANIZER ====================

@login_required
def trigger_smart_reorganize_ppm(request):
    """
    Handle the PPM Smart Reorganizer form submission.
    """
    import uuid as _uuid
    request_id = str(_uuid.uuid4())[:8]
    logger.info(f"[REQ-{request_id}] PPM Smart-reorganize request from user: {request.user.username}")

    if request.method != "POST":
        return redirect("ppm_dashboard")

    access_context = get_user_access_context(request)
    if not access_context or not access_context.get("can_schedule", False):
        messages.error(request, "You don't have permission to reorganize PPM schedules.", extra_tags="permission")
        return redirect("ppm_dashboard")

    try:
        new_planning_logic = request.POST.get("new_planning_logic", "department")
        base_month = int(request.POST.get("base_month", 1))
        max_departments = int(request.POST.get("max_departments", 100))
        max_descriptions = int(request.POST.get("max_descriptions", 100))
        dry_run = request.POST.get("dry_run", "").lower() == "true"

        base_month = max(1, min(12, base_month))
        max_departments = max(1, min(100, max_departments))
        max_descriptions = max(1, min(100, max_descriptions))

        workshop_id = access_context.get("workshop_id")

        logger.info(
            f"[REQ-{request_id}] Smart-reorg params: logic={new_planning_logic}, "
            f"base_month={base_month}, dry_run={dry_run}"
        )
    except (ValueError, TypeError) as exc:
        logger.error(f"[REQ-{request_id}] Invalid form data: {exc}")
        messages.error(request, f"Invalid input data: {exc}", extra_tags="validation")
        return redirect("ppm_dashboard")

    try:
        task = smart_reorganize_ppm_schedules.delay(
            new_planning_logic=new_planning_logic,
            base_month=base_month,
            max_departments=max_departments,
            max_descriptions=max_descriptions,
            workshop_id=str(workshop_id) if workshop_id else None,
            dry_run=dry_run,
        )

        if dry_run:
            messages.info(
                request,
                f"PPM Smart Reorganizer dry run started (Task ID: {task.id}). "
                f"Check logs to preview changes.",
                extra_tags="smart-reorg dry-run",
            )
        else:
            messages.success(
                request,
                f"PPM Smart Reorganizer started (Task ID: {task.id}). "
                f"Schedules are being realigned to '{new_planning_logic}' logic. "
                f"Completed schedules are protected.",
                extra_tags="smart-reorg",
            )
    except Exception as exc:
        logger.error(f"[REQ-{request_id}] Failed to queue smart-reorg task: {exc}")
        messages.error(
            request,
            f"Failed to start Smart Reorganizer: {exc}. Please ensure Celery is running.",
            extra_tags="smart-reorg error",
        )

    return redirect("ppm_dashboard")


# ==================== PPM NORMALIZATION TRIGGER ====================

def _normalize_ppm_helper(request, request_id):
    # This helper is kept for any future common logic
    pass


# ==================== DEPRECATED ORPHANED CODE (DO NOT EDIT) ====================

    # GET request - redirect to dashboard
    logger.info(f"[REQ-{request_id}] GET request - redirecting to dashboard")
    return redirect('ppm_dashboard')
@login_required
def trigger_sync_initialization(request):
    """
    Synchronous initialization - bypasses Celery for testing
    ONLY use this for debugging!
    """
    if not request.user.is_staff:
        messages.error(request, "Only staff can use sync initialization")
        return redirect('ppm_dashboard')

    access_context = get_user_access_context(request)

    if not access_context or not access_context['workshop_id']:
        messages.error(request, "No workshop access")
        return redirect('ppm_dashboard')

    workshop_id = access_context['workshop_id']

    if request.method == 'POST':
        try:
            planning_logic = request.POST.get('planning_logic', 'department')
            maintenance_period = int(request.POST.get('maintenance_period', 6))
            base_month = int(request.POST.get('base_month', 1))
            base_year = int(request.POST.get('base_year', datetime.today().year))
            max_departments = int(request.POST.get('max_departments', 100))
            max_descriptions = int(request.POST.get('max_descriptions', 100))
            preserve_existing = request.POST.get('preserve_existing') == 'true'

            logger.info(f"=== SYNC PPM Initialization Started ===")
            logger.info(f"User: {request.user.username}")
            logger.info(f"Workshop ID: {workshop_id}")

            # Import the actual task function
            from ppms.tasks import initialize_ppm_schedule_with_logic

            # Create a mock self object for bind parameter
            class MockSelf:
                def update_state(self, state, meta):
                    logger.info(f"Progress: {meta.get('percent', 0)}% - {meta.get('status', '')}")

            # Call directly without Celery
            result = initialize_ppm_schedule_with_logic(
                MockSelf(),
                str(workshop_id),
                planning_logic,
                maintenance_period,
                base_month,
                base_year,
                max_departments,
                max_descriptions,
                [],
                preserve_existing,
                None
            )

            logger.info(f"Sync initialization completed: {result}")

            if result.get('status') == 'success':
                messages.success(request, f"✓ {result.get('message')}")
            else:
                messages.error(request, f"✗ {result.get('message')}")

        except Exception as e:
            logger.error(f"Sync initialization error: {e}", exc_info=True)
            messages.error(request, f"Error: {str(e)}")

        return redirect('ppm_dashboard')

    return redirect('ppm_dashboard')


@login_required
def test_celery_connection(request):
    """Test endpoint to verify Celery is working properly"""
    results = {
        'timestamp': datetime.now().isoformat(),
        'celery_status': 'unknown',
        'broker_connection': 'unknown',
        'test_task': 'unknown',
        'ppm_task_import': 'unknown',
        'errors': [],
        'workshop_info': {}
    }

    try:
        # Test 1: Celery app configuration
        from Equiper.celery import app as celery_app
        results['celery_status'] = 'configured'
        results['broker_url'] = str(celery_app.conf.broker_url)
        results['result_backend'] = str(celery_app.conf.result_backend)

        # Test 2: Broker connection
        try:
            conn = celery_app.connection()
            conn.ensure_connection(max_retries=3, timeout=5)
            results['broker_connection'] = '✓ Connected'
            conn.release()
        except Exception as e:
            results['broker_connection'] = f'✗ Failed: {str(e)}'
            results['errors'].append(f'Broker connection: {str(e)}')

        # Test 3: Simple test task
        try:
            from Equiper.celery import test_celery
            task = test_celery.delay()
            results['test_task'] = f'✓ Queued (ID: {task.id})'
            results['test_task_id'] = str(task.id)
        except Exception as e:
            results['test_task'] = f'✗ Failed: {str(e)}'
            results['errors'].append(f'Test task: {str(e)}')

        # Test 4: PPM task import
        try:
            from ppms.tasks import initialize_ppm_schedule_with_logic
            results['ppm_task_import'] = '✓ Imported successfully'
            results['ppm_task_name'] = initialize_ppm_schedule_with_logic.name
        except Exception as e:
            results['ppm_task_import'] = f'✗ Failed: {str(e)}'
            results['errors'].append(f'PPM task import: {str(e)}')

        # Test 5: Workshop info
        access_context = get_user_access_context(request)
        if access_context and access_context['workshop_id']:
            try:
                workshop = Workshop.objects.get(id=access_context['workshop_id'])
                active_equipment = Equipment.objects.filter(
                    workshop_id=workshop.id,
                    active_status=True
                ).count()
                scheduled_equipment = PPMSchedule.objects.filter(
                    workshop_id=workshop.id,
                    equipment__active_status=True
                ).count()

                results['workshop_info'] = {
                    'id': str(workshop.id),
                    'name': workshop.name,
                    'active_equipment': active_equipment,
                    'scheduled_equipment': scheduled_equipment,
                    'unscheduled_equipment': active_equipment - scheduled_equipment
                }
            except Exception as e:
                results['workshop_info'] = {'error': str(e)}

        # Overall status
        results['overall_status'] = '✓ All tests passed' if not results['errors'] else '✗ Some tests failed'

    except Exception as e:
        results['errors'].append(f'General error: {str(e)}')
        results['overall_status'] = '✗ Test suite failed'

    return JsonResponse(results, json_dumps_params={'indent': 2})


@login_required
def check_task_status(request, task_id):
    """Check the real-time status of a Celery task"""
    try:
        task = AsyncResult(task_id)

        response_data = {
            'task_id': task_id,
            'state': task.state,
            'ready': task.ready(),
            'successful': task.successful() if task.ready() else None,
            'failed': task.failed() if task.ready() else None,
            'timestamp': datetime.now().isoformat()
        }

        if task.state == 'PENDING':
            response_data['status'] = 'waiting'
            response_data['message'] = 'Task is waiting in queue'

        elif task.state == 'STARTED':
            response_data['status'] = 'running'
            response_data['message'] = 'Task has started processing'

        elif task.state == 'PROGRESS':
            response_data['status'] = 'in_progress'
            response_data['progress'] = task.info
            response_data['message'] = task.info.get('status', 'Processing...')

        elif task.state == 'SUCCESS':
            response_data['status'] = 'completed'
            response_data['result'] = task.result
            response_data['message'] = 'Task completed successfully'

        elif task.state == 'FAILURE':
            response_data['status'] = 'failed'
            response_data['error'] = str(task.info)
            response_data['message'] = f'Task failed: {str(task.info)}'
            if hasattr(task, 'traceback'):
                response_data['traceback'] = task.traceback

        else:
            response_data['status'] = 'unknown'
            response_data['message'] = f'Unknown state: {task.state}'

        return JsonResponse(response_data, json_dumps_params={'indent': 2})

    except Exception as e:
        return JsonResponse({
            'error': str(e),
            'task_id': task_id,
            'timestamp': datetime.now().isoformat()
        }, status=500)


@login_required
def push_schedule(request, schedule_id):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on schedule {schedule_id}")
            messages.error(request, "You don't have permission to modify schedules.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id': schedule_id}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedule = get_object_or_404(PPMSchedule, **filter_kwargs)
            new_scheduled_month = schedule.scheduled_month + relativedelta(months=1)
            if PPMSchedule.objects.filter(equipment=schedule.equipment, scheduled_month=new_scheduled_month).exists():
                messages.error(request, f"Cannot push schedule for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} to {new_scheduled_month.strftime('%B %Y')} as it already exists.")
                return redirect('ppm_dashboard')
            schedule.scheduled_month = new_scheduled_month
            schedule.status = 'pushed'
            schedule.save()
            messages.success(request, f"Schedule for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} pushed to {new_scheduled_month.strftime('%B %Y')}.")
        except Exception as e:
            logger.error(f"Error pushing schedule {schedule_id} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to push schedule: {str(e)}")
    else:
        logger.warning(f"Invalid request method for push_schedule by user {request.user.username} on schedule {schedule_id}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def mark_completed(request, schedule_id):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on schedule {schedule_id}")
            messages.error(request, "You don't have permission to modify schedules.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id': schedule_id}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedule = get_object_or_404(PPMSchedule, **filter_kwargs)
            schedule.status = 'completed'
            schedule.save()
            messages.success(request, f"Schedule for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} marked as completed.")
        except Exception as e:
            logger.error(f"Error marking schedule {schedule_id} as completed for user {request.user.username}: {e}")
            messages.error(request, f"Failed to mark schedule as completed: {str(e)}")
    else:
        logger.warning(f"Invalid request method for mark_completed by user {request.user.username} on schedule {schedule_id}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def edit_schedule(request, schedule_id):
    access_context = get_user_access_context(request)
    if not access_context or not access_context.get('can_edit', False):
        logger.warning(f"Access denied for user {request.user.username} on schedule {schedule_id}")
        messages.error(request, "You don't have permission to edit schedules.")
        return redirect('ppm_dashboard')

    filter_kwargs = {'id': schedule_id}
    if access_context['access_type'] == 'department':
        filter_kwargs['equipment__department_id'] = access_context['department_id']
    else:
        filter_kwargs['workshop_id'] = access_context['workshop_id']

    schedule = get_object_or_404(PPMSchedule, **filter_kwargs)

    if request.method == 'POST':
        scheduled_month = request.POST.get('scheduled_month')
        status = request.POST.get('status')
        maintenance_period = int(request.POST.get('maintenance_period', schedule.maintenance_period))

        try:
            scheduled_month = datetime.strptime(scheduled_month, '%Y-%m').replace(day=1)
            if PPMSchedule.objects.filter(equipment=schedule.equipment, scheduled_month=scheduled_month).exclude(id=schedule.id).exists():
                messages.error(request, f"Cannot update schedule for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} as a schedule already exists for {scheduled_month.strftime('%B %Y')}.")
                return redirect('ppm_dashboard')
            schedule.scheduled_month = scheduled_month
            schedule.status = status
            schedule.maintenance_period = maintenance_period
            schedule.save()
            messages.success(request, f"Schedule for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} updated successfully.")
        except ValueError:
            logger.error(f"Invalid date format for schedule {schedule_id}: {scheduled_month}")
            messages.error(request, "Invalid date format.")
        except Exception as e:
            logger.error(f"Error updating schedule {schedule_id} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to update schedule: {str(e)}")
        return redirect('ppm_dashboard')
    return render(request, 'PPM/ppm.html', {
        'schedule': schedule,
        'access_context': access_context,
        'show_sidebar': True,  # Added sidebar context
    })


@login_required
def delete_schedule(request, schedule_id):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on schedule {schedule_id}")
            messages.error(request, "You don't have permission to delete schedules.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id': schedule_id}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedule = get_object_or_404(PPMSchedule, **filter_kwargs)
            equipment_name = schedule.equipment.description.name if schedule.equipment.description else 'N/A'

            if hasattr(schedule, 'pending_delete'):
                if getattr(schedule, 'status', None) in ['in_progress', 'completed']:
                    messages.error(request, f"Cannot delete schedule with status: {getattr(schedule, 'status', 'N/A')}")
                else:
                    schedule.pending_delete = True
                    schedule.updated_at = timezone.now()
                    schedule.save(update_fields=['pending_delete', 'updated_at'])
                    messages.success(request, f"Schedule for {equipment_name} marked for deletion.")
            elif hasattr(schedule, 'status'):
                if schedule.status in ['in_progress', 'completed']:
                    messages.error(request, f"Cannot delete schedule with status: {schedule.status}")
                elif schedule.status == 'pending_delete':
                    schedule.updated_at = timezone.now()
                    schedule.save(update_fields=['updated_at'])
                    schedule.delete()
                    messages.success(request, f"Schedule for {equipment_name} deleted successfully.")
                else:
                    schedule.status = 'pending_delete'
                    schedule.updated_at = timezone.now()
                    schedule.save(update_fields=['status', 'updated_at'])
                    messages.success(request, f"Schedule for {equipment_name} marked for deletion.")
            else:
                schedule.updated_at = timezone.now()
                schedule.save(update_fields=['updated_at'])
                schedule.delete()
                messages.success(request, f"Schedule for {equipment_name} deleted successfully.")

        except Exception as e:
            logger.error(f"Error deleting schedule {schedule_id} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to delete schedule: {str(e)}")
    else:
        logger.warning(f"Invalid request method for delete_schedule by user {request.user.username} on schedule {schedule_id}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def bulk_delete_schedules(request):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on bulk delete")
            messages.error(request, "You don't have permission to delete schedules.")
            return redirect('ppm_dashboard')

        schedule_ids = request.POST.getlist('schedule_ids')
        if not schedule_ids:
            logger.warning(f"No schedule IDs provided for bulk delete by user {request.user.username}")
            messages.error(request, "No schedules selected.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id__in': schedule_ids}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedules = PPMSchedule.objects.filter(**filter_kwargs)
            count = schedules.count()
            if count == 0:
                logger.warning(f"No valid schedules found for deletion with IDs {schedule_ids} by user {request.user.username}")
                messages.error(request, "No valid schedules found for deletion.")
                return redirect('ppm_dashboard')

            current_time = timezone.now()

            if hasattr(PPMSchedule, 'pending_delete'):
                in_progress_schedules = schedules.filter(status__in=['in_progress', 'completed'])
                eligible_schedules = schedules.exclude(status__in=['in_progress', 'completed'])
                eligible_count = eligible_schedules.count()
                eligible_schedules.update(pending_delete=True, updated_at=current_time)
                blocked_count = in_progress_schedules.count()

                if eligible_count > 0:
                    message = f"{eligible_count} schedule(s) marked for deletion"
                    if blocked_count > 0:
                        message += f". {blocked_count} schedule(s) could not be processed (in progress or completed)"
                    messages.success(request, message)
                else:
                    messages.error(request, "No schedules could be processed.")

            elif hasattr(PPMSchedule, 'status'):
                pending_delete_schedules = schedules.filter(status='pending_delete')
                in_progress_schedules = schedules.filter(status__in=['in_progress', 'completed'])
                other_schedules = schedules.exclude(status__in=['pending_delete', 'in_progress', 'completed'])

                pending_delete_count = pending_delete_schedules.count()
                if pending_delete_count > 0:
                    pending_delete_schedules.update(updated_at=current_time)
                    pending_delete_schedules.delete()

                other_count = other_schedules.count()
                other_schedules.update(status='pending_delete', updated_at=current_time)
                blocked_count = in_progress_schedules.count()
                total_processed = pending_delete_count + other_count

                if total_processed > 0:
                    message = f"{total_processed} schedule(s) processed successfully"
                    if pending_delete_count > 0 and other_count > 0:
                        message += f" ({pending_delete_count} deleted, {other_count} marked for deletion)"
                    elif pending_delete_count > 0:
                        message += f" ({pending_delete_count} deleted)"
                    elif other_count > 0:
                        message += f" ({other_count} marked for deletion)"

                    if blocked_count > 0:
                        message += f". {blocked_count} schedule(s) could not be processed (in progress or completed)"

                    messages.success(request, message)
                else:
                    messages.error(request, "No schedules could be processed.")
            else:
                schedules.update(updated_at=current_time)
                schedules.delete()
                messages.success(request, f"{count} schedule(s) deleted successfully.")

        except Exception as e:
            logger.error(f"Error deleting schedules {schedule_ids} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to delete schedules: {str(e)}")
    else:
        logger.warning(f"Invalid request method for bulk_delete_schedules by user {request.user.username}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def bulk_mark_completed(request):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on bulk mark completed")
            messages.error(request, "You don't have permission to modify schedules.")
            return redirect('ppm_dashboard')

        schedule_ids = request.POST.getlist('schedule_ids')
        if not schedule_ids:
            logger.warning(f"No schedule IDs provided for bulk mark completed by user {request.user.username}")
            messages.error(request, "No schedules selected.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id__in': schedule_ids}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedules = PPMSchedule.objects.filter(**filter_kwargs)
            count = 0
            for schedule in schedules:
                schedule.status = 'completed'
                schedule.save()
                count += 1
            if count == 0:
                messages.error(request, "No schedules were marked as completed.")
            else:
                messages.success(request, f"{count} schedule(s) marked as completed.")
        except Exception as e:
            logger.error(f"Error marking schedules {schedule_ids} as completed for user {request.user.username}: {e}")
            messages.error(request, f"Failed to mark schedules as completed: {str(e)}")
    else:
        logger.warning(f"Invalid request method for bulk_mark_completed by user {request.user.username}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def bulk_push_schedules(request):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on bulk push")
            messages.error(request, "You don't have permission to modify schedules.")
            return redirect('ppm_dashboard')

        schedule_ids = request.POST.getlist('schedule_ids')
        if not schedule_ids:
            logger.warning(f"No schedule IDs provided for bulk push by user {request.user.username}")
            messages.error(request, "No schedules selected.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id__in': schedule_ids}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedules = PPMSchedule.objects.filter(**filter_kwargs)
            count = 0
            for schedule in schedules:
                new_scheduled_month = schedule.scheduled_month + relativedelta(months=1)
                if not PPMSchedule.objects.filter(equipment=schedule.equipment, scheduled_month=new_scheduled_month).exists():
                    schedule.scheduled_month = new_scheduled_month
                    schedule.status = 'pushed'
                    schedule.save()
                    count += 1
            if count == 0:
                messages.error(request, "No schedules were pushed.")
            else:
                messages.success(request, f"{count} schedule(s) pushed by 1 month.")
        except Exception as e:
            logger.error(f"Error pushing schedules {schedule_ids} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to push schedules: {str(e)}")
    else:
        logger.warning(f"Invalid request method for bulk_push_schedules by user {request.user.username}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def schedule_equipment(request, equipment_id):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_schedule', False):
            logger.warning(f"Access denied for user {request.user.username} on equipment {equipment_id}")
            messages.error(request, "You don't have permission to schedule equipment.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id': equipment_id}
        if access_context['access_type'] == 'department':
            filter_kwargs['department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            filter_kwargs['active_status'] = True
            equipment = get_object_or_404(Equipment, **filter_kwargs)
            workshop = get_object_or_404(Workshop, id=access_context['workshop_id'])
            scheduled_month = datetime.today().replace(day=1) + relativedelta(months=1)

            if PPMSchedule.objects.filter(equipment=equipment).exists():
                messages.error(request, f"Equipment {equipment.description.name if equipment.description else 'N/A'} is already scheduled.")
                return redirect('ppm_dashboard')

            PPMSchedule.objects.create(
                equipment=equipment,
                workshop=workshop,
                scheduled_month=scheduled_month,
                status='pending',
                maintenance_period=6
            )
            messages.success(request, f"Equipment {equipment.description.name if equipment.description else 'N/A'} scheduled successfully for {scheduled_month.strftime('%B %Y')}.")
        except Exception as e:
            logger.error(f"Error scheduling equipment {equipment_id} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to schedule equipment: {str(e)}")
    else:
        logger.warning(f"Invalid request method for schedule_equipment by user {request.user.username} on equipment {equipment_id}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def bulk_schedule_unscheduled(request):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_schedule', False):
            logger.warning(f"Access denied for user {request.user.username} on bulk schedule")
            messages.error(request, "You don't have permission to schedule equipment.")
            return redirect('ppm_dashboard')

        equipment_ids = request.POST.getlist('equipment_ids')
        if not equipment_ids:
            logger.warning(f"No equipment IDs provided for bulk schedule by user {request.user.username}")
            messages.error(request, "No equipment selected.")
            return redirect('ppm_dashboard')

        planning_logic = request.POST.get('planning_logic', 'department')
        maintenance_period = int(request.POST.get('maintenance_period', 6))
        base_month = int(request.POST.get('base_month', datetime.today().month))
        base_year = int(request.POST.get('base_year', datetime.today().year))
        max_departments = int(request.POST.get('max_departments', 20))
        max_descriptions = int(request.POST.get('max_descriptions', 20))

        if access_context['access_type'] == 'department':
            valid_equipment_ids = Equipment.objects.filter(
                id__in=equipment_ids,
                department_id=access_context['department_id'],
                active_status=True
            ).values_list('id', flat=True)
            equipment_ids = list(valid_equipment_ids)

        try:
            task = initialize_ppm_schedule_with_logic.delay(
                str(access_context['workshop_id']),
                planning_logic,
                maintenance_period,
                base_month,
                base_year,
                max_departments,
                max_descriptions,
                [],
                False,
                equipment_ids
            )
            messages.info(request, f"Bulk scheduling started (Task ID: {task.id}). Please check back later.")
        except Exception as e:
            logger.error(f"Failed to trigger bulk_schedule_unscheduled for user {request.user.username}: {e}")
            messages.error(request, f"Failed to schedule equipment: {str(e)}")
    else:
        logger.warning(f"Invalid request method for bulk_schedule_unscheduled by user {request.user.username}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def export_ppm_excel(request):
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"Access denied for user {request.user.username} on export PPM")
        messages.error(request, "Access denied.")
        return redirect('ppm_dashboard')

    if access_context['access_type'] == 'department':
        schedules = PPMSchedule.objects.select_related('equipment__department', 'equipment__description').filter(
            equipment__department_id=access_context['department_id'],
            equipment__active_status=True
        )
    else:
        workshop_id = access_context['workshop_id']
        schedules = PPMSchedule.objects.select_related('equipment__department', 'equipment__description').filter(
            workshop_id=workshop_id,
            equipment__active_status=True
        )
        selected_department_id = request.GET.get('department')
        if selected_department_id:
            schedules = schedules.filter(equipment__department_id=selected_department_id)

    wb = Workbook()
    ws = wb.active
    ws.title = "PPM Schedules"
    ws.append(["Description", "Model", "Serial Number", "Department", "Status", "Scheduled Month", "Maintenance Period"])

    for sched in schedules:
        ws.append([
            sched.equipment.description.name if sched.equipment.description else "N/A",
            sched.equipment.model or "N/A",
            sched.equipment.serial_number or "N/A",
            sched.equipment.department.name if sched.equipment.department else "N/A",
            sched.status.title(),
            sched.scheduled_month.strftime('%B %Y') if sched.scheduled_month else "N/A",
            f"{sched.maintenance_period} Months" if sched.maintenance_period else "N/A"
        ])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=PPM_Schedules.xlsx'
    wb.save(response)
    return response


@login_required
def export_ppm_pdf(request):
    """Export PPM schedules to PDF based on user access"""
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"Access denied for user {request.user.username} on export PPM PDF")
        messages.error(request, "Access denied.")
        return redirect('ppm_dashboard')

    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')

    try:
        if access_context['access_type'] == 'department':
            schedules = PPMSchedule.objects.select_related(
                'equipment__department',
                'equipment__description'
            ).filter(
                equipment__department_id=access_context['department_id'],
                equipment__active_status=True
            ).order_by('scheduled_month', 'equipment__description__name')

            department = access_context['department']
            workshop = None

        else:
            workshop_id = access_context['workshop_id']
            schedules = PPMSchedule.objects.select_related(
                'equipment__department',
                'equipment__description'
            ).filter(
                workshop_id=workshop_id,
                equipment__active_status=True
            ).order_by('equipment__department__name', 'scheduled_month', 'equipment__description__name')

            selected_department_id = request.GET.get('department')
            if selected_department_id:
                schedules = schedules.filter(equipment__department_id=selected_department_id)
                department = Department.objects.get(id=selected_department_id)
                workshop = None
            else:
                department = None
                workshop = access_context['workshop']

        if month_filter:
            schedules = schedules.filter(scheduled_month__month=int(month_filter))
        if year_filter:
            schedules = schedules.filter(scheduled_month__year=int(year_filter))

        return create_ppm_pdf_response(schedules, department, workshop)

    except Exception as e:
        logger.error(f"Error generating PPM PDF for user {request.user.username}: {str(e)}", exc_info=True)
        messages.error(request, "Failed to generate PDF. Please try again.")
        return redirect('ppm_dashboard')


@login_required
def export_department_ppm_pdf(request, dept_id):
    """Export PPM schedules for a specific department to PDF"""
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"Access denied for user {request.user.username} on export department PPM PDF")
        messages.error(request, "Access denied.")
        return redirect('ppm_dashboard')

    if access_context['access_type'] == 'department':
        if str(access_context['department_id']) != str(dept_id):
            messages.error(request, "You don't have access to this department.")
            return redirect('ppm_dashboard')

    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')

    try:
        department = get_object_or_404(Department, id=dept_id)

        if access_context['access_type'] == 'workshop':
            if department.workshop_id != access_context['workshop_id']:
                messages.error(request, "You don't have access to this department.")
                return redirect('ppm_dashboard')

        schedules = PPMSchedule.objects.select_related(
            'equipment__department',
            'equipment__description'
        ).filter(
            equipment__department_id=dept_id,
            equipment__active_status=True
        ).order_by('scheduled_month', 'equipment__description__name')

        if month_filter:
            schedules = schedules.filter(scheduled_month__month=int(month_filter))
        if year_filter:
            schedules = schedules.filter(scheduled_month__year=int(year_filter))

        return create_ppm_pdf_response(schedules, department, None)

    except Exception as e:
        logger.error(f"Error generating department PPM PDF for user {request.user.username}: {str(e)}", exc_info=True)
        messages.error(request, "Failed to generate PDF. Please try again.")
        return redirect('ppm_dashboard')


@login_required
def clear_department_filter(request):
    return redirect('ppm_dashboard')


@login_required
def debug_user_access(request):
    access_context = get_user_access_context(request)

    debug_info = {
        'user': request.user.username,
        'access_context': access_context,
        'session_workshop_id': request.session.get('workshop_id'),
        'session_department_id': request.session.get('department_id'),
        'has_userprofile': hasattr(request.user, 'userprofile'),
    }

    if hasattr(request.user, 'userprofile'):
        profile = request.user.userprofile
        debug_info.update({
            'profile_role': getattr(profile, 'role', 'No role'),
            'profile_level': getattr(profile, 'level', 'No level'),
            'profile_has_workshop': profile.workshop is not None,
            'profile_has_department': profile.department is not None,
        })

        if profile.workshop:
            debug_info['profile_workshop_id'] = profile.workshop.id
            debug_info['profile_workshop_name'] = profile.workshop.name

        if profile.department:
            debug_info['profile_department_id'] = profile.department.id
            debug_info['profile_department_name'] = profile.department.name
            debug_info['profile_department_workshop_id'] = profile.department.workshop_id if profile.department.workshop else None

        try:
            profile.clean()
            debug_info['profile_validation'] = 'Valid'
        except ValidationError as e:
            debug_info['profile_validation'] = f'Invalid: {e}'

    return JsonResponse(debug_info, indent=2)



@login_required
def view_logs(request):
    """View recent log entries"""
    if not request.user.is_staff:
        messages.error(request, "Only staff can view logs")
        return redirect('ppm_dashboard')

    log_type = request.GET.get('type', 'ppm')
    lines = int(request.GET.get('lines', 100))

    import os
    from django.conf import settings

    log_files = {
        'ppm': os.path.join(settings.BASE_DIR, 'logs', 'ppm.log'),
        'celery': os.path.join(settings.BASE_DIR, 'logs', 'celery.log'),
        'debug': os.path.join(settings.BASE_DIR, 'logs', 'debug.log'),
    }

    log_file = log_files.get(log_type)

    if not log_file or not os.path.exists(log_file):
        return HttpResponse(f"Log file not found: {log_file}", content_type='text/plain')

    try:
        with open(log_file, 'r') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:]
            content = ''.join(recent_lines)

        return HttpResponse(content, content_type='text/plain')
    except Exception as e:
        return HttpResponse(f"Error reading log: {str(e)}", content_type='text/plain')


# ==================== PPM NORMALIZATION TRIGGER ====================

@login_required
def trigger_normalize_ppm(request):
    """
    Trigger PPM schedule normalization so all equipment in the same
    group (department OR description) lands in the same month.

    Only affects pending/pushed schedules in the active planning year.
    Completed schedules are never touched.
    """
    import uuid as _uuid
    request_id = str(_uuid.uuid4())[:8]
    logger.info(f"[REQ-{request_id}] PPM Normalize request from user: {request.user.username}")

    if request.method != 'POST':
        return redirect('ppm_dashboard')

    access_context = get_user_access_context(request)
    if not access_context or not access_context.get('can_schedule', False):
        messages.error(
            request,
            "You don't have permission to normalize PPM schedules.",
            extra_tags="permission schedule"
        )
        return redirect('ppm_dashboard')

    try:
        planning_logic    = request.POST.get('planning_logic', 'department')
        maintenance_period = int(request.POST.get('maintenance_period', 6))
        base_month        = int(request.POST.get('base_month', 1))
        max_departments   = int(request.POST.get('max_departments', 100))
        max_descriptions  = int(request.POST.get('max_descriptions', 100))

        # Validate
        if planning_logic not in ('department', 'description'):
            planning_logic = 'department'
        if not (1 <= base_month <= 12):
            base_month = 1
        if maintenance_period not in (3, 6, 9, 12):
            maintenance_period = 6

        # Determine which year will be normalized (for user feedback only)
        from datetime import date as _date
        today = _date.today()
        normalized_year = today.year + 1 if today.month > 6 else today.year

        workshop_id = access_context.get('workshop_id')

        logger.info(
            f"[REQ-{request_id}] Normalize params: logic={planning_logic}, "
            f"period={maintenance_period}m, base_month={base_month}, "
            f"year={normalized_year} (auto), workshop={workshop_id}"
        )

    except (ValueError, TypeError) as exc:
        logger.error(f"[REQ-{request_id}] Invalid form data: {exc}")
        messages.error(request, f"Invalid input data: {exc}", extra_tags="validation")
        return redirect('ppm_dashboard')

    try:
        task = normalize_ppm_schedules.delay(
            planning_logic=planning_logic,
            maintenance_period=maintenance_period,
            base_month=base_month,
            max_departments=max_departments,
            max_descriptions=max_descriptions,
            workshop_id=str(workshop_id) if workshop_id else None,
        )
        logger.info(f"[REQ-{request_id}] ✅ Normalize task queued: {task.id}")
        messages.success(
            request,
            f"PPM normalization started for {normalized_year} (auto-selected)! "
            f"Task ID: {task.id}. "
            f"All '{planning_logic}' groups will be aligned to a single month each. "
            f"Completed schedules are protected.",
            extra_tags="schedule normalization task"
        )
    except Exception as exc:
        logger.error(f"[REQ-{request_id}] Failed to queue normalize task: {exc}")
        messages.error(
            request,
            f"Failed to start normalization: {exc}. "
            f"Please check that a Celery worker is running.",
            extra_tags="normalization error"
        )

    return redirect('ppm_dashboard')
