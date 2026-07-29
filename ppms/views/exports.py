"""ppms.views — Excel / PDF exports."""
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
from .helpers import get_user_access_context


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
