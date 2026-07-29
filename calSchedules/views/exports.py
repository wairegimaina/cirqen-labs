"""calSchedules views — Excel / PDF exports."""
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


@login_required
def export_calibration_pdf(request):
    """
    Export Calibration schedules to PDF based on user access
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"Access denied for user {request.user.username} on export Calibration PDF")
        messages.error(request, "Access denied.")
        return redirect('schedule:calibration_dashboard')

    # Get filter parameters
    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')

    try:
        # Get schedules based on user access
        if access_context['access_type'] == 'department':
            schedules = CalibrationSchedule.objects.select_related(
                'equipment__department',
                'equipment__description'
            ).filter(
                equipment__department_id=access_context['department_id'],
                equipment__active_status=True
            ).order_by('scheduled_month', 'equipment__description__name')

            department = access_context['department']
            workshop = None

        else:
            schedules = CalibrationSchedule.objects.select_related(
                'equipment__department',
                'equipment__description'
            ).filter(
                equipment__active_status=True
            ).order_by('equipment__department__name', 'scheduled_month', 'equipment__description__name')

            # Check if filtering by specific department
            selected_department_id = request.GET.get('department')
            if selected_department_id:
                schedules = schedules.filter(equipment__department_id=selected_department_id)
                department = Department.objects.get(id=selected_department_id)
                workshop = None
            else:
                department = None
                workshop = access_context.get('workshop')

        # Apply month and year filters
        if month_filter:
            schedules = schedules.filter(scheduled_month__month=int(month_filter))
        if year_filter:
            schedules = schedules.filter(scheduled_month__year=int(year_filter))

        # Generate and return PDF
        return create_calibration_pdf_response(schedules, department, workshop)

    except Exception as e:
        logger.error(f"Error generating Calibration PDF for user {request.user.username}: {str(e)}", exc_info=True)
        messages.error(request, "Failed to generate PDF. Please try again.")
        return redirect('schedule:calibration_dashboard')


@login_required
def export_department_calibration_pdf(request, dept_id):
    """
    Export Calibration schedules for a specific department to PDF
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"Access denied for user {request.user.username} on export department Calibration PDF")
        messages.error(request, "Access denied.")
        return redirect('schedule:calibration_dashboard')

    # Verify access to department
    if access_context['access_type'] == 'department':
        if str(access_context['department_id']) != str(dept_id):
            messages.error(request, "You don't have access to this department.")
            return redirect('schedule:calibration_dashboard')

    # Get filter parameters
    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')

    try:
        department = get_object_or_404(Department, id=dept_id)

        # Get schedules for this department
        schedules = CalibrationSchedule.objects.select_related(
            'equipment__department',
            'equipment__description'
        ).filter(
            equipment__department_id=dept_id,
            equipment__active_status=True
        ).order_by('scheduled_month', 'equipment__description__name')

        # Apply month and year filters
        if month_filter:
            schedules = schedules.filter(scheduled_month__month=int(month_filter))
        if year_filter:
            schedules = schedules.filter(scheduled_month__year=int(year_filter))

        # Generate and return PDF
        return create_calibration_pdf_response(schedules, department, None)

    except Exception as e:
        logger.error(f"Error generating department Calibration PDF for user {request.user.username}: {str(e)}", exc_info=True)
        messages.error(request, "Failed to generate PDF. Please try again.")
        return redirect('schedule:calibration_dashboard')


@login_required
def export_calibration_excel(request):
    """Export calibration schedules to Excel"""
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"Access denied for user {request.user.username} on export calibration")
        messages.error(
            request,
            "Access denied.",
            extra_tags="access permission",
        )
        return redirect("schedule:calibration_dashboard")

    if access_context["access_type"] == "department":
        schedules = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).filter(
            equipment__department_id=access_context["department_id"]
        )
    else:
        schedules = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).all()

    selected_department_id = request.GET.get("department")
    if selected_department_id:
        schedules = schedules.filter(equipment__department_id=selected_department_id)

    wb = Workbook()
    ws = wb.active
    ws.title = "Calibration Schedules"
    ws.append(
        ["Description", "Model", "Serial Number", "Department", "Status", "Scheduled Month", "Calibration Period"]
    )

    for sched in schedules:
        ws.append([
            sched.equipment.description.name if sched.equipment.description else "N/A",
            sched.equipment.model or "N/A",
            sched.equipment.serial_number or "N/A",
            sched.equipment.department.name if sched.equipment.department else "N/A",
            sched.status.title(),
            sched.scheduled_month.strftime("%B %Y") if sched.scheduled_month else "N/A",
            f"{sched.calibration_period} Months" if sched.calibration_period else "N/A",
        ])

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = "attachment; filename=Calibration_Schedules.xlsx"
    wb.save(response)
    return response
