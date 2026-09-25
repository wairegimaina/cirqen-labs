"""calSchedules views — calibration dashboard, department view, pending list."""
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
from openpyxl import Workbook
from CalSoft.models import CalibrationSession
from django.db import transaction
import logging
from django.db.models.functions import TruncMonth
from django.db.models import Q, Case, When, IntegerField, Count
logger = logging.getLogger(__name__)
from django.utils import timezone
from django.contrib.auth import get_user_model
import uuid
User = get_user_model()
from ..calibration_pdf_generator import create_calibration_pdf_response

# sibling modules in this package
from .helpers import check_overdue_schedules, get_current_month_year, get_user_access_context



def _scheduling_context(request, access_context):
    """The Scheduling panel: each visible workshop's calibration plan."""
    from scheduling.reports import panels
    from scheduling.views import can_manage

    if access_context.get("access_type") == "department" and access_context.get("workshop_id"):
        workshops = Workshop.objects.filter(id=access_context["workshop_id"])
    else:
        workshops = Workshop.objects.filter(active_status=True).order_by("name")
    return {
        "scheduling_panels": panels(workshops, "calibration"),
        "scheduling_can_manage": can_manage(request.user),
    }

@login_required
def calibration_dashboard(request):
    """
    Main calibration dashboard view with enhanced search, overdue warnings, and group status
    ✅ ENHANCED: Added overdue checking and group waiting status
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"No access context for user {request.user.username}")
        messages.error(
            request,
            "No workshop or department access found. Please contact administrator.",
            extra_tags="access permission"
        )
        return redirect('custom_login')

    # Store session info
    if access_context.get("workshop_id"):
        request.session["workshop_id"] = str(access_context["workshop_id"])
    if access_context.get("department_id"):
        request.session["department_id"] = str(access_context["department_id"])
    request.session.modified = True

    # ✅ Get filter parameters (no defaults - only apply if explicitly provided)
    current_month, current_year = get_current_month_year()
    month_filter = request.GET.get('month')  # Don't default
    year_filter = request.GET.get('year')    # Don't default
    search_query = request.GET.get('search', '').strip()  # ✅ NEW: Search parameter

    # Base querysets based on access level
    if access_context["access_type"] == "department":
        departments = Department.objects.filter(id=access_context["department_id"])
        schedules_list = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).filter(
            equipment__department_id=access_context["department_id"],
            equipment__active_status=True
        )

        # FIX #1: Only exclude equipment that has an ACTIVE (non-completed) schedule.
        # Equipment whose only schedules are completed should appear as unscheduled
        # so operators know to create/auto-advance the next schedule.
        scheduled_equipment_ids = CalibrationSchedule.objects.filter(
            equipment__department_id=access_context["department_id"],
            equipment__active_status=True,
            status__in=["pending", "pushed", "in_progress", "overdue"],  # ← KEY FIX
        ).values_list("equipment_id", flat=True)

        unscheduled_equipment_list = Equipment.objects.filter(
            department_id=access_context["department_id"],
            active_status=True
        ).exclude(id__in=scheduled_equipment_ids).select_related("department", "description")

        equipment_descriptions = EquipmentDescription.objects.filter(
            equipment__department_id=access_context["department_id"],
            equipment__active_status=True
        ).distinct()
    else:
        departments = Department.objects.all()
        schedules_list = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).filter(
            equipment__active_status=True
        )

        # FIX #1: Only exclude equipment that has an ACTIVE (non-completed) schedule.
        scheduled_equipment_ids = CalibrationSchedule.objects.filter(
            equipment__active_status=True,
            status__in=["pending", "pushed", "in_progress", "overdue"],  # ← KEY FIX
        ).values_list("equipment_id", flat=True)

        unscheduled_equipment_list = Equipment.objects.filter(
            active_status=True
        ).exclude(
            id__in=scheduled_equipment_ids
        ).select_related("department", "description")

        equipment_descriptions = EquipmentDescription.objects.filter(
            equipment__active_status=True
        ).distinct()

    # Optional department filter
    selected_department_id = request.GET.get("department")
    show_all = request.GET.get("show_all") == "true"
    selected_department = None
    if selected_department_id and not show_all:
        selected_department = get_object_or_404(Department, id=selected_department_id)
        schedules_list = schedules_list.filter(equipment__department_id=selected_department_id)
        unscheduled_equipment_list = unscheduled_equipment_list.filter(department_id=selected_department_id)

    # ✅ ENHANCED: Apply search filter BEFORE pagination (works across all records)
    if search_query:
        schedules_list = schedules_list.filter(
            Q(equipment__description__name__icontains=search_query) |
            Q(equipment__model__icontains=search_query) |
            Q(equipment__serial_number__icontains=search_query) |
            Q(equipment__department__name__icontains=search_query)
        )
        unscheduled_equipment_list = unscheduled_equipment_list.filter(
            Q(description__name__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(department__name__icontains=search_query)
        )
        logger.info(f"Search applied: '{search_query}' - Found {schedules_list.count()} schedules")

    # ✅ Apply month and year filters ONLY if provided
    if month_filter:
        try:
            schedules_list = schedules_list.filter(scheduled_month__month=int(month_filter))
            logger.info(f"Month filter applied: {month_filter}")
        except (ValueError, TypeError):
            logger.warning(f"Invalid month filter: {month_filter}")

    if year_filter:
        try:
            schedules_list = schedules_list.filter(scheduled_month__year=int(year_filter))
            logger.info(f"Year filter applied: {year_filter}")
        except (ValueError, TypeError):
            logger.warning(f"Invalid year filter: {year_filter}")

    # ✅ ENHANCED: Order by status priority (pending first), then department, then date
    schedules_list = schedules_list.annotate(
        status_priority=Case(
            When(status='pending', then=1),
            When(status='pushed', then=2),
            When(status='completed', then=3),
            default=4,
            output_field=IntegerField()
        )
    ).order_by(
        'status_priority',  # ✅ Pending schedules appear first
        'equipment__department__name',
        'scheduled_month',
        'equipment__description__name'
    )

    # ✅ NEW: Check for overdue and warning schedules
    overdue_info = check_overdue_schedules(schedules_list)

    # ✅ NEW: Add group waiting status to schedules
    schedules_with_status = []
    for schedule in schedules_list:
        schedule_dict = {
            'schedule': schedule,
            'is_overdue': schedule in overdue_info['overdue_schedules'],
            'is_warning': schedule in overdue_info['warning_schedules']
        }
        schedules_with_status.append(schedule_dict)

    # Pagination for schedules
    page = request.GET.get("page", 1)
    paginator = Paginator(schedules_with_status, 25)
    try:
        schedules = paginator.page(page)
    except PageNotAnInteger:
        schedules = paginator.page(1)
    except EmptyPage:
        schedules = paginator.page(paginator.num_pages)

    # Pagination for unscheduled equipment
    unscheduled_page = request.GET.get("unscheduled_page", 1)
    unscheduled_paginator = Paginator(unscheduled_equipment_list, 25)
    try:
        unscheduled_equipment = unscheduled_paginator.page(unscheduled_page)
    except PageNotAnInteger:
        unscheduled_equipment = unscheduled_paginator.page(1)
    except EmptyPage:
        unscheduled_equipment = unscheduled_paginator.page(unscheduled_paginator.num_pages)

    # ── Completed schedules — last 30 days only (for the Completed tab) ──
    thirty_days_ago = timezone.localdate() - timedelta(days=30)

    completed_list_qs = CalibrationSchedule.objects.select_related(
        "equipment__department", "equipment__description"
    ).filter(
        status="completed",
        completed_date__gte=thirty_days_ago,   # last 30 days
        equipment__active_status=True,
    )

    # Respect the same department / search filters as the pending tab
    if access_context["access_type"] == "department":
        completed_list_qs = completed_list_qs.filter(
            equipment__department_id=access_context["department_id"]
        )
    if selected_department_id and not show_all:
        completed_list_qs = completed_list_qs.filter(
            equipment__department_id=selected_department_id
        )
    if search_query:
        completed_list_qs = completed_list_qs.filter(
            Q(equipment__description__name__icontains=search_query) |
            Q(equipment__model__icontains=search_query) |
            Q(equipment__serial_number__icontains=search_query) |
            Q(equipment__department__name__icontains=search_query)
        )

    completed_list_qs = completed_list_qs.order_by(
        "-completed_date", "equipment__department__name"
    )

    # Wrap in the same schedule_data dict format the template expects
    completed_with_status = [{"schedule": s} for s in completed_list_qs]

    completed_page = request.GET.get("cpage", 1)
    completed_paginator = Paginator(completed_with_status, 25)
    try:
        completed_schedules = completed_paginator.page(completed_page)
    except PageNotAnInteger:
        completed_schedules = completed_paginator.page(1)
    except EmptyPage:
        completed_schedules = completed_paginator.page(completed_paginator.num_pages)

    # ✅ Context with proper defaults for display
    month_choices = [(i, calendar.month_name[i]) for i in range(1, 13)]
    last_task_id = request.session.get('last_calibration_task_id', '')

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "schedules": schedules,
        "departments": departments,
        "selected_department": selected_department,
        "selected_department_id": selected_department_id if not show_all else None,
        "unscheduled_equipment": unscheduled_equipment,
        "equipment_descriptions": equipment_descriptions,
        "access_context": access_context,
        "user": request.user,
        'selected_month': month_filter if month_filter else str(current_month),
        'selected_year': year_filter if year_filter else str(current_year),
        'month_filter': month_filter,   # raw value — None when not applied
        'year_filter': year_filter,     # raw value — None when not applied
        'current_month': current_month,
        'current_year': current_year,
        'search_query': search_query,  # ✅ Pass search query to template
        'has_filters': bool(month_filter or year_filter or search_query),  # ✅ Indicator for active filters
        'completed_schedules': completed_schedules,  # ✅ NEW: Completed schedules for tab
        'month_choices': month_choices,
        'last_task_id': last_task_id,
        # ✅ NEW: Add stats and overdue info
        'stats': {
            'total': schedules_list.count(),
            'pending': schedules_list.filter(status='pending').count(),
            'pushed': schedules_list.filter(status='pushed').count(),
            'in_progress': schedules_list.filter(status='in_progress').count(),
            'completed': schedules_list.filter(status='completed').count(),
            'overdue': overdue_info['overdue_count'],
            'warning': overdue_info['warning_count']
        },
        'overdue_info': overdue_info,
        **_scheduling_context(request, access_context),
    }

    return render(request, "Calibrition/calScheduels.html", context)


@login_required
def calibration_by_department(request, dept_id):
    """
    View calibration schedules filtered by department
    ✅ FIXED: Search works across all pages
    ✅ FIXED: Date filters work independently
    ✅ FIXED: Pending schedules show first
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"No access context for user {request.user.username}")
        messages.error(
            request,
            "No workshop or department access found. Please contact administrator.",
            extra_tags="access permission"
        )
        return redirect("custom_login")

    departments = Department.objects.all()
    selected_department = get_object_or_404(Department, id=dept_id)

    # Verify access
    if access_context["access_type"] == "department":
        if str(access_context["department_id"]) != str(dept_id):
            messages.error(
                request,
                "You don't have access to this department.",
                extra_tags="access permission"
            )
            return redirect("schedule:calibration_dashboard")

    # ✅ Get filter parameters
    current_month, current_year = get_current_month_year()
    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')
    search_query = request.GET.get('search', '').strip()  # ✅ NEW

    # Base queryset for this department
    schedules_list = CalibrationSchedule.objects.select_related(
        "equipment__department", "equipment__description"
    ).filter(
        equipment__department_id=dept_id,
        equipment__active_status=True
    )

    # ✅ ENHANCED: Apply search filter
    if search_query:
        schedules_list = schedules_list.filter(
            Q(equipment__description__name__icontains=search_query) |
            Q(equipment__model__icontains=search_query) |
            Q(equipment__serial_number__icontains=search_query)
        )
        logger.info(f"Department {dept_id} search: '{search_query}' - Found {schedules_list.count()} schedules")

    # Apply month and year filters
    if month_filter:
        try:
            schedules_list = schedules_list.filter(scheduled_month__month=int(month_filter))
        except (ValueError, TypeError):
            logger.warning(f"Invalid month filter: {month_filter}")

    if year_filter:
        try:
            schedules_list = schedules_list.filter(scheduled_month__year=int(year_filter))
        except (ValueError, TypeError):
            logger.warning(f"Invalid year filter: {year_filter}")

    # ✅ ENHANCED: Order with pending first
    schedules_list = schedules_list.annotate(
        status_priority=Case(
            When(status='pending', then=1),
            When(status='pushed', then=2),
            When(status='completed', then=3),
            default=4,
            output_field=IntegerField()
        )
    ).order_by('status_priority', 'scheduled_month', 'equipment__description__name')

    # ✅ NEW: Check for overdue and warning schedules
    overdue_info = check_overdue_schedules(schedules_list)

    # ✅ NEW: Add group waiting status to schedules
    schedules_with_status = []
    for schedule in schedules_list:
        schedule_dict = {
            'schedule': schedule,
            'is_overdue': schedule in overdue_info['overdue_schedules'],
            'is_warning': schedule in overdue_info['warning_schedules']
        }
        schedules_with_status.append(schedule_dict)

    # Pagination
    page = request.GET.get("page", 1)
    paginator = Paginator(schedules_with_status, 25)
    try:
        schedules = paginator.page(page)
    except PageNotAnInteger:
        schedules = paginator.page(1)
    except EmptyPage:
        schedules = paginator.page(paginator.num_pages)

    # Unscheduled equipment for this department
    # FIX #1: Only exclude equipment that has an ACTIVE (non-completed) schedule.
    scheduled_equipment_ids = CalibrationSchedule.objects.filter(
        equipment__department_id=dept_id,
        equipment__active_status=True,
        status__in=["pending", "pushed", "in_progress", "overdue"],  # ← KEY FIX
    ).values_list("equipment_id", flat=True)

    unscheduled_equipment_list = Equipment.objects.filter(
        department_id=dept_id,
        active_status=True
    ).exclude(id__in=scheduled_equipment_ids).select_related("department", "description")

    # ✅ ENHANCED: Apply search to unscheduled equipment
    if search_query:
        unscheduled_equipment_list = unscheduled_equipment_list.filter(
            Q(description__name__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(serial_number__icontains=search_query)
        )

    # Pagination for unscheduled
    unscheduled_page = request.GET.get("unscheduled_page", 1)
    unscheduled_paginator = Paginator(unscheduled_equipment_list, 25)
    try:
        unscheduled_equipment = unscheduled_paginator.page(unscheduled_page)
    except PageNotAnInteger:
        unscheduled_equipment = unscheduled_paginator.page(1)
    except EmptyPage:
        unscheduled_equipment = unscheduled_paginator.page(unscheduled_paginator.num_pages)

    # ── Completed schedules — last 30 days only (for the Completed tab) ──
    thirty_days_ago = timezone.localdate() - timedelta(days=30)
    completed_list_qs = CalibrationSchedule.objects.select_related(
        "equipment__department", "equipment__description"
    ).filter(
        status="completed",
        completed_date__gte=thirty_days_ago,
        equipment__department_id=dept_id,
        equipment__active_status=True,
    )
    if search_query:
        completed_list_qs = completed_list_qs.filter(
            Q(equipment__description__name__icontains=search_query) |
            Q(equipment__model__icontains=search_query) |
            Q(equipment__serial_number__icontains=search_query) |
            Q(equipment__department__name__icontains=search_query)
        )
    completed_list_qs = completed_list_qs.order_by("-completed_date")

    completed_with_status = [{"schedule": s} for s in completed_list_qs]
    completed_page = request.GET.get("cpage", 1)
    completed_paginator = Paginator(completed_with_status, 25)
    try:
        completed_schedules = completed_paginator.page(completed_page)
    except PageNotAnInteger:
        completed_schedules = completed_paginator.page(1)
    except EmptyPage:
        completed_schedules = completed_paginator.page(completed_paginator.num_pages)

    # Equipment descriptions for this department
    equipment_descriptions = EquipmentDescription.objects.filter(
        equipment__department_id=dept_id,
        equipment__active_status=True
    ).distinct()

    month_choices = [(i, calendar.month_name[i]) for i in range(1, 13)]
    last_task_id = request.session.get('last_calibration_task_id', '')

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "schedules": schedules,
        "departments": departments,
        "selected_department": selected_department,
        "selected_department_id": selected_department.id,
        "unscheduled_equipment": unscheduled_equipment,
        "equipment_descriptions": equipment_descriptions,
        "title": f"Calibration Equipment in {selected_department.name}",
        "access_context": access_context,
        "user": request.user,
        'selected_month': month_filter if month_filter else str(current_month),
        'selected_year': year_filter if year_filter else str(current_year),
        'month_filter': month_filter,   # raw value — None when not applied
        'year_filter': year_filter,     # raw value — None when not applied
        'current_month': current_month,
        'current_year': current_year,
        'search_query': search_query,  # ✅ Pass search query
        'has_filters': bool(month_filter or year_filter or search_query),
        'completed_schedules': completed_schedules,  # ✅ NEW: Completed schedules for tab
        'month_choices': month_choices,
        'last_task_id': last_task_id,
        # ✅ NEW: Add stats and overdue info
        'stats': {
            'total': schedules_list.count(),
            'pending': schedules_list.filter(status='pending').count(),
            'pushed': schedules_list.filter(status='pushed').count(),
            'in_progress': schedules_list.filter(status='in_progress').count(),
            'completed': schedules_list.filter(status='completed').count(),
            'overdue': overdue_info['overdue_count'],
            'warning': overdue_info['warning_count']
        },
        'overdue_info': overdue_info,
        **_scheduling_context(request, access_context),
    }

    return render(request, "Calibrition/calScheduels.html", context)


def pending_calibrations(request):
    """
    View for pending and pushed calibrations only
    ✅ ENHANCED: Added search across all pages, pagination, and filter persistence
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"No access context for user {request.user.username}")
        messages.error(
            request,
            "No workshop or department access found. Please contact administrator.",
            extra_tags="access permission",
        )
        return redirect("custom_login")

    # Save access context into session
    if access_context.get("workshop_id"):
        request.session["workshop_id"] = str(access_context["workshop_id"])
    if access_context.get("department_id"):
        request.session["department_id"] = str(access_context["department_id"])
    request.session.modified = True

    # ✅ Get filter parameters with proper defaults
    today = now()
    current_month = request.GET.get("month", str(today.month))
    current_year = request.GET.get("year", str(today.year))
    search_query = request.GET.get("search", "").strip()
    selected_department_id = request.GET.get("department", "")

    # ✅ Base queryset WITH active_status filter
    if access_context["access_type"] == "department":
        departments = Department.objects.filter(id=access_context["department_id"])
        schedules_list = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).filter(
            equipment__department_id=access_context["department_id"],
            equipment__active_status=True,
            status__in=["pending", "pushed"],
        )
    else:
        departments = Department.objects.all()
        schedules_list = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).filter(
            equipment__active_status=True,
            status__in=["pending", "pushed"]
        )

    # ✅ Department filter (for workshop-level users)
    selected_department = None
    if selected_department_id and access_context["access_type"] == "workshop":
        try:
            selected_department = Department.objects.get(id=selected_department_id)
            schedules_list = schedules_list.filter(equipment__department_id=selected_department_id)
            logger.info(f"Department filter applied: {selected_department.name}")
        except Department.DoesNotExist:
            logger.warning(f"Invalid department ID: {selected_department_id}")

    # ✅ Month & year filtering
    try:
        filter_month = int(current_month)
        filter_year = int(current_year)
        filter_date = date(filter_year, filter_month, 1)
        schedules_list = schedules_list.annotate(
            month=TruncMonth("scheduled_month")
        ).filter(month=filter_date)
        logger.info(f"Date filter applied: {filter_month}/{filter_year}")
    except (ValueError, TypeError):
        filter_date = date(today.year, today.month, 1)
        schedules_list = schedules_list.annotate(
            month=TruncMonth("scheduled_month")
        ).filter(month=filter_date)

    # ✅ ENHANCED: Search filter BEFORE pagination (works across all pages)
    if search_query:
        schedules_list = schedules_list.filter(
            Q(equipment__description__name__icontains=search_query)
            | Q(equipment__model__icontains=search_query)
            | Q(equipment__serial_number__icontains=search_query)
            | Q(equipment__department__name__icontains=search_query)
        )
        logger.info(f"Search applied: '{search_query}' - Found {schedules_list.count()} schedules")

    # ✅ ENHANCED: Order with pending first
    schedules_list = schedules_list.annotate(
        status_priority=Case(
            When(status='pending', then=1),
            When(status='pushed', then=2),
            default=3,
            output_field=IntegerField()
        )
    ).order_by(
        "status_priority",
        "equipment__department__name",
        "equipment__description__name",
        "scheduled_month"
    )

    # ✅ Pagination (50 items per page)
    paginator = Paginator(schedules_list, 50)
    page = request.GET.get("page", 1)
    try:
        schedules = paginator.page(page)
    except PageNotAnInteger:
        schedules = paginator.page(1)
    except EmptyPage:
        schedules = paginator.page(paginator.num_pages)

    # Statistics
    total_pending = schedules_list.filter(status="pending").count()
    total_pushed = schedules_list.filter(status="pushed").count()
    overdue_count = schedules_list.filter(scheduled_month__lt=filter_date).count()

    # ✅ Year options (extended to 2081)
    years = range(today.year - 5, 2082)

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "schedules": schedules,
        "departments": departments,
        "selected_department": selected_department,
        "selected_department_id": selected_department_id,
        "access_context": access_context,
        "user": request.user,
        "current_month": int(current_month),
        "current_year": int(current_year),
        "years": years,
        "today": today.date(),
        "title": f"Pending Calibrations - {selected_department.name if selected_department else 'All Departments'}",
        "total_pending": total_pending,
        "total_pushed": total_pushed,
        "overdue_count": overdue_count,
        "search_query": search_query,
    }

    return render(request, "Calibrition/Pending-Calibrations.html", context)


@login_required
def clear_calibration_department_filter(request):
    """Clear department filter for calibration dashboard"""
    return redirect('schedule:calibration_dashboard')
