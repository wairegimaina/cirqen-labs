"""ppms.views — analytics + summary JSON endpoints."""
from calendar import monthrange
from django.db.models import Count
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from core import aggregate_cache
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.timezone import localdate
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

    today = localdate()
    # Cached per access scope and day; PPM and equipment saves invalidate it.
    scope = [access_context['access_type'], access_context['workshop_id'],
             access_context['department_id'], today]
    response_data = aggregate_cache.get_or_compute(
        "ppm", ["analytics", *scope], lambda: _ppm_analytics(access_context, today)
    )
    return JsonResponse(response_data, safe=False)


def _ppm_analytics(access_context, today):
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

    # One pass over the schedules, read as plain values. The per-month,
    # per-department and per-type querysets this replaced cost ~195 queries
    # and grew with the number of departments.
    def month_end(d):
        return d.replace(day=monthrange(d.year, d.month)[1])

    rows = list(base_schedules.values(
        'id', 'status', 'scheduled_month', 'updated_at', 'equipment_id',
        'equipment__department_id', 'equipment__department__name',
        'equipment__description_id', 'equipment__description__name',
    ))

    def label(row, key):
        # 'N/A' only when the relation is missing, as before.
        return row[key] if row[key.replace('__name', '_id')] else 'N/A'

    overdue_schedules = []
    pending_schedules = []
    month_keys = [(current_month_start + relativedelta(months=i)) for i in range(12)]
    monthly = {(m.year, m.month): {'total': 0, 'completed': 0, 'pending': 0, 'pushed': 0, 'overdue': 0}
               for m in month_keys}
    by_dept = {}
    by_type = {}
    completed_total = 0
    completed_on_time = 0
    upcoming_schedules = []

    for row in rows:
        status, month = row['status'], row['scheduled_month']
        overdue = bool(month) and status != 'completed' and today > month_end(month)
        if month and status != 'completed':
            if overdue:
                overdue_schedules.append(row)
            elif status == 'pending':
                pending_schedules.append(row)

        if month and (month.year, month.month) in monthly:
            bucket = monthly[(month.year, month.month)]
            bucket['total'] += 1
            if status in ('completed', 'pending', 'pushed'):
                bucket[status] += 1
            bucket['overdue'] += overdue

        for key, table in (('equipment__department_id', by_dept), ('equipment__description_id', by_type)):
            counts = table.setdefault(row[key], {'scheduled': 0, 'completed': 0, 'pending': 0, 'overdue': 0})
            counts['scheduled'] += 1
            if status in ('completed', 'pending'):
                counts[status] += 1
            counts['overdue'] += overdue

        if status == 'completed':
            completed_total += 1
            if month and row['updated_at'] and timezone.localdate(row['updated_at']) <= month_end(month):
                completed_on_time += 1

        if (status == 'pending' and month and month.year == today.year and month.month == today.month):
            upcoming_schedules.append({
                'id': str(row['id']),
                'equipment': label(row, 'equipment__description__name'),
                'department': label(row, 'equipment__department__name'),
                'scheduled_date': month.strftime('%Y-%m-%d'),
                'days_remaining': (month_end(month) - today).days,
            })

    # Status breakdown
    status_counts = base_schedules.values('status').annotate(
        count=Count('id')
    ).order_by('status')

    monthly_data = [
        {'month': m.strftime('%b %Y'), **{k: monthly[(m.year, m.month)][k]
                                          for k in ('total', 'completed', 'pending', 'pushed', 'overdue')}}
        for m in month_keys
    ]

    empty = {'scheduled': 0, 'completed': 0, 'pending': 0, 'overdue': 0}

    # Department breakdown (for workshop-level users)
    department_data = []
    if access_context['access_type'] == 'workshop':
        departments = Department.objects.filter(
            workshop_id=access_context['workshop_id'],
            active_status=True
        )
        equipment_per_dept = dict(
            Equipment.objects.filter(department__in=departments, active_status=True)
            .order_by().values_list('department_id').annotate(n=Count('id'))
        )
        for dept in departments:
            counts = by_dept.get(dept.id, empty)
            department_data.append({
                'name': dept.name,
                'total_equipment': equipment_per_dept.get(dept.id, 0),
                'scheduled': counts['scheduled'],
                'completed': counts['completed'],
                'pending': counts['pending'],
                'overdue': counts['overdue'],
            })

    # Equipment type breakdown
    descriptions = EquipmentDescription.objects.filter(
        equipment__department__workshop_id=access_context['workshop_id'],
        equipment__active_status=True
    ).distinct()
    equipment_type_data = []
    for desc in descriptions[:10]:  # Top 10 equipment types
        counts = by_type.get(desc.id, empty)
        equipment_type_data.append({
            'name': desc.name,
            'scheduled': counts['scheduled'],
            'completed': counts['completed'],
            'pending': counts['pending'],
        })

    total_scheduled = len(rows)
    compliance_rate = (completed_on_time / total_scheduled * 100) if total_scheduled > 0 else 0

    response_data = {
        'summary': {
            'total_equipment': total_equipment,
            'total_scheduled': total_scheduled,
            'unscheduled': total_equipment - len({row['equipment_id'] for row in rows}),
            'completed': completed_total,
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
            'id': str(row['id']),
            'equipment': label(row, 'equipment__description__name'),
            'department': label(row, 'equipment__department__name'),
            'scheduled_date': row['scheduled_month'].strftime('%Y-%m-%d'),
            'days_overdue': (today - month_end(row['scheduled_month'])).days,
            'status': row['status']
        } for row in overdue_schedules[:10]]
    }

    return response_data


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
