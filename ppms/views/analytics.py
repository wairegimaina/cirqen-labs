"""ppms.views — analytics + summary JSON endpoints."""
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
from .helpers import calculate_ppm_statistics, get_user_access_context


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
