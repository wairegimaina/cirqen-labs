"""jobcard.views — waiting/approved/declined and HOD job-card list views."""
from locale import D_T_FMT
from uuid import UUID
import zipfile
from django.utils.timezone import now
from users.control import role_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import FileResponse, HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from django import forms
from docxtpl import DocxTemplate
from docxtpl import InlineImage
from docx.shared import Mm
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
import os
from django.db.models import Q
from io import BytesIO
from django.conf import settings
from django.contrib.messages import error as messages_error, success as messages_success
import json
import logging
from jobcard.modern_jobcard_pdf import generate_jobcard_pdf, create_jobcard_pdf_response
from django.db import transaction, IntegrityError
from users.models import UserProfile, UserSignature
from workshop.models import Workshop
from ..models import jobcard, SparePartUsed
from Inventory.models import Equipment, Department
from parts_tools.models import Accessories
from PIL import Image
import base64
import io
from django.core.exceptions import ValidationError
from datetime import timedelta, datetime, date
from django.db.models.functions import ExtractYear
from django.utils.dateparse import parse_date
from decimal import Decimal, InvalidOperation
import json
from uuid import UUID
from django.utils.timezone import now
from django.core.exceptions import ValidationError
from django.db import transaction
from django.contrib import messages
from django.shortcuts import render, redirect
import logging
logger = logging.getLogger(__name__)

# sibling modules in this package
from .helpers import get_filtered_jobcards, get_last_week_range, get_user_context


@login_required
@role_required('NIC', 'Tech', redirect_to='jobcard:create_job_card', message='Access denied or profile incomplete.', extra_tags='jobcard')
def waiting_jobcards(request):
    profile, department, workshop, role = get_user_context(request)

    search_query = request.GET.get('search', '').strip()

    if role == 'NIC':
        if not department:
            messages.error(request, "Your nurse profile is missing an associated department.")
            job_cards = jobcard.objects.none()
        else:
            job_cards = jobcard.objects.filter(
                status="Waiting Approval",
                department=department
            ).select_related('department', 'equipment__description', 'performed_by', 'workshop').order_by('-date_issued')

    elif role == 'Tech':
        if not workshop:
            messages.error(request, "Your technician profile is missing an associated workshop.")
            job_cards = jobcard.objects.none()
        else:
            job_cards = jobcard.objects.filter(
                status="Waiting Approval",
                workshop=workshop
            ).select_related('department', 'equipment__description', 'performed_by', 'workshop').order_by('-date_issued')

    job_cards = get_filtered_jobcards(job_cards, search_query)

    paginator = Paginator(job_cards, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    default_start, default_end = get_last_week_range()

    return render(request, 'jobcard/job-card-waiting.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'job_cards': page_obj,
        'paginator': paginator,
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'is_nurse': role == 'NIC',
        'is_technician': role == 'Tech',
        'search_query': search_query,
        'default_start': default_start,
        'default_end': default_end,
    })


@login_required
def approved_jobcards(request):
    profile, department, workshop, role = get_user_context(request)
    if not profile:
        messages.error(request, "Access denied or profile incomplete.", extra_tags="jobcard")
        return redirect('jobcard:create_job_card')

    jobcards = jobcard.objects.filter(status="Approved")

    if role == 'NIC':
        if not department:
            messages.error(request, "Your nurse profile is missing an associated department.")
            jobcards = jobcard.objects.none()
        else:
            jobcards = jobcards.filter(department=department)

    elif role == 'Tech':
        if not workshop:
            messages.error(request, "Your technician profile is missing an associated workshop.")
            jobcards = jobcard.objects.none()
        else:
            jobcards = jobcards.filter(workshop=workshop)

    elif role == 'HOD':
        pass
    else:
        messages.error(request, "You don't have permission to view approved work orders.")
        return redirect('jobcard:create_job_card')

    # Captured before search/date filters below, so the KPI strip stays a
    # stable "how are we doing overall" summary regardless of what the
    # table itself is currently filtered to.
    role_scoped_jobcards = jobcards

    search_query = request.GET.get("search")
    if search_query:
        jobcards = get_filtered_jobcards(jobcards, search_query)

    year = request.GET.get("year")
    month = request.GET.get("month")
    date_filter = request.GET.get("date")

    if date_filter:
        try:
            parsed = parse_date(date_filter)
            if parsed:
                jobcards = jobcards.filter(nurse_signed_date__date=parsed)
        except Exception:
            pass
    else:
        if year:
            jobcards = jobcards.filter(nurse_signed_date__year=year)
        if month:
            jobcards = jobcards.filter(nurse_signed_date__month=month)

    jobcards = jobcards.select_related('department', 'equipment__description', 'performed_by', 'verified_by_nurse', 'workshop')

    paginator = Paginator(jobcards, 50)
    page = request.GET.get("page")
    jobcards_page = paginator.get_page(page)

    default_start, default_end = get_last_week_range()
    approved_this_week_count = role_scoped_jobcards.filter(
        nurse_signed_date__date__range=(default_start, default_end)
    ).count()

    return render(request, "jobcard/job-card-approval.html", {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "job_cards": jobcards_page,
        "paginator": paginator,
        "is_paginated": True,
        "selected_year": year,
        "selected_month": month,
        "selected_date": date_filter,
        "search_query": search_query,
        "is_nurse": role == 'NIC',
        "is_technician": role == 'Tech',
        "is_hod": role == 'HOD',
        "approved_this_week_count": approved_this_week_count,
        'default_start': default_start,
        'default_end': default_end,
    })


@login_required
@role_required('NIC', 'Tech', 'HOD', redirect_to='jobcard:create_job_card', message='Access denied or profile incomplete.')
def declined_jobcards(request):
    profile, department, workshop, role = get_user_context(request)

    search_query = request.GET.get('search', '').strip()

    job_cards = jobcard.objects.filter(status="Declined")

    if role == 'NIC':
        if not department:
            messages.error(request, "Your nurse profile is missing an associated department.")
            job_cards = jobcard.objects.none()
        else:
            job_cards = job_cards.filter(department=department)

    elif role == 'Tech':
        if not workshop:
            messages.error(request, "Your technician profile is missing an associated workshop.")
            job_cards = jobcard.objects.none()
        else:
            job_cards = job_cards.filter(workshop=workshop)

    elif role == 'HOD':
        pass

    job_cards = job_cards.select_related('department', 'equipment__description', 'performed_by', 'workshop').order_by('-date_issued')

    job_cards = get_filtered_jobcards(job_cards, search_query)

    paginator = Paginator(job_cards, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    default_start, default_end = get_last_week_range()

    return render(request, 'jobcard/declined-jobcard.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'job_cards': page_obj,
        'paginator': paginator,
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'is_nurse': role == 'NIC',
        'is_technician': role == 'Tech',
        'is_hod': role == 'HOD',
        'search_query': search_query,
        'default_start': default_start,
        'default_end': default_end,
    })


@login_required
@role_required('HOD', redirect_to='jobcard:create_job_card', message='Access denied. Only HODs can view workshop work orders.')
def hod_workshop_jobcards(request, workshop_id):
    profile, _, _, role = get_user_context(request)

    workshop = get_object_or_404(Workshop, id=workshop_id)
    status_filter = request.GET.get('status_filter', 'Waiting Approval')
    search_query = request.GET.get('search', '').strip()

    if workshop.category == 'calibration_center':
        job_cards_queryset = jobcard.objects.filter(
            workshop=workshop,
            status=status_filter
        )
        context_type = 'calibration_center'
        context_description = f"Work orders performed by {workshop.name} (calibration center)"

    else:
        job_cards_queryset = jobcard.objects.filter(
            department__workshop=workshop,
            status=status_filter
        ).exclude(
            workshop__category='calibration_center'
        )
        context_type = 'regular_workshop'
        context_description = f"Work orders for equipment in {workshop.name}'s departments (excluding calibration center work)"

    job_cards_queryset = job_cards_queryset.select_related(
        'department',
        'equipment',
        'performed_by',
        'verified_by_nurse',
        'workshop'
    ).order_by('-date_issued')

    if search_query:
        job_cards_queryset = get_filtered_jobcards(job_cards_queryset, search_query)

    total_count = job_cards_queryset.count()
    high_priority_count = job_cards_queryset.filter(priority_level='High').count()

    last_month = datetime.now() - timedelta(days=30)
    last_month_count = job_cards_queryset.filter(date_issued__gte=last_month).count()

    additional_context = {}
    if workshop.category == 'calibration_center':
        departments_served = job_cards_queryset.values_list(
            'department__name', flat=True
        ).distinct().order_by('department__name')
        additional_context['departments_served'] = list(departments_served)

        equipment_types = job_cards_queryset.values_list(
            'equipment__description__name', flat=True
        ).distinct().order_by('equipment__description__name')[:10]
        additional_context['equipment_types'] = list(equipment_types)

    else:
        technicians = job_cards_queryset.values_list(
            'performed_by__username', flat=True
        ).distinct().order_by('performed_by__username')
        additional_context['technicians_involved'] = list(technicians)

        own_work_count = job_cards_queryset.filter(workshop=workshop).count()
        external_work_count = total_count - own_work_count
        additional_context['own_work_count'] = own_work_count
        additional_context['external_work_count'] = external_work_count

    paginator = Paginator(job_cards_queryset, 25)
    page = request.GET.get('page')
    job_cards = paginator.get_page(page)

    default_start, default_end = get_last_week_range()

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "job_cards": job_cards,
        "paginator": paginator,
        "is_paginated": job_cards.has_other_pages(),
        "workshop": workshop,
        "status_choices": ["Waiting Approval", "Approved", "Declined"],
        "current_status_filter": status_filter,
        "search_query": search_query,
        "total_count": total_count,
        "high_priority_count": high_priority_count,
        "last_month_count": last_month_count,
        "default_start": default_start,
        "default_end": default_end,
        "context_type": context_type,
        "context_description": context_description,
        "is_calibration_center": workshop.category == 'calibration_center',
        **additional_context
    }

    return render(request, "jobcard/hod_workshop_jobcards.html", context)


@login_required
@role_required('HOD', redirect_to='jobcard:create_job_card', message='Access denied. Only HODs can view this page.')
def hod_calibration_work_on_equipment(request, workshop_id):
    """
    Separate view for regular workshop HODs to see calibration center work
    performed on their department's equipment
    """
    profile, _, _, role = get_user_context(request)

    workshop = get_object_or_404(Workshop, id=workshop_id)

    if workshop.category == 'calibration_center':
        messages.error(request, "Calibration centers should use the main work orders view.")
        return redirect('jobcard:hod_workshop_jobcards', workshop_id=workshop_id)

    status_filter = request.GET.get('status_filter', 'Waiting Approval')
    search_query = request.GET.get('search', '').strip()

    job_cards_queryset = jobcard.objects.filter(
        department__workshop=workshop,
        workshop__category='calibration_center',
        status=status_filter
    ).select_related(
        'department',
        'equipment',
        'performed_by',
        'verified_by_nurse',
        'workshop'
    ).order_by('-date_issued')

    if search_query:
        job_cards_queryset = get_filtered_jobcards(job_cards_queryset, search_query)

    total_count = job_cards_queryset.count()
    high_priority_count = job_cards_queryset.filter(priority_level='High').count()

    last_month = datetime.now() - timedelta(days=30)
    last_month_count = job_cards_queryset.filter(date_issued__gte=last_month).count()

    calibration_centers = job_cards_queryset.values_list(
        'workshop__name', flat=True
    ).distinct().order_by('workshop__name')

    paginator = Paginator(job_cards_queryset, 25)
    page = request.GET.get('page')
    job_cards = paginator.get_page(page)

    default_start, default_end = get_last_week_range()

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "job_cards": job_cards,
        "paginator": paginator,
        "is_paginated": job_cards.has_other_pages(),
        "workshop": workshop,
        "status_choices": ["Waiting Approval", "Approved", "Declined"],
        "current_status_filter": status_filter,
        "search_query": search_query,
        "total_count": total_count,
        "high_priority_count": high_priority_count,
        "last_month_count": last_month_count,
        "default_start": default_start,
        "default_end": default_end,
        "context_type": 'calibration_work_view',
        "context_description": f"Calibration center work performed on {workshop.name}'s equipment",
        "is_calibration_center": False,
        "calibration_centers": list(calibration_centers),
        "is_calibration_work_view": True
    }

    return render(request, "jobcard/hod_workshop_jobcards.html", context)
