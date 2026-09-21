"""jobcard.views — single and bulk docx/pdf downloads."""
from locale import D_T_FMT
from uuid import UUID
import zipfile
from django.utils.timezone import now
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
from .helpers import generate_docx, get_user_context


@login_required
def download_jobcard_docx(request, jobcard_id: UUID):
    profile, department, workshop, role = get_user_context(request)

    if not profile:
        messages.error(request, "User profile not found or incomplete. Cannot download job card.")
        return redirect('jobcard:create_job_card')

    job_card = get_object_or_404(jobcard, id=jobcard_id)

    if role == 'NIC':
        if not department or job_card.department != department:
            messages.error(request, "You can only download job cards from your department.")
            return redirect('jobcard:waiting_jobcards')
    elif role == 'Tech':
        if not workshop or job_card.workshop != workshop:
            messages.error(request, "You can only download job cards from your workshop.")
            return redirect('jobcard:waiting_jobcards')
    elif role == 'HOD':
        pass
    else:
        messages.error(request, "Access denied.")
        return redirect('jobcard:create_job_card')

    buffer = generate_docx(job_card, job_card.department.workshop)
    return FileResponse(buffer, as_attachment=True, filename=f'job_card_{job_card.id}.docx')


@login_required
def download_jobcard_pdf(request, jobcard_id):
    """
    Download job card as modern PDF (replaces old DOCX to PDF conversion)
    """
    profile, department, workshop, role = get_user_context(request)

    if not profile:
        messages.error(request, "User profile not found or incomplete. Cannot download job card.")
        return redirect('jobcard:create_job_card')

    job_card = get_object_or_404(
        jobcard.objects.select_related('department', 'equipment', 'performed_by', 'verified_by_nurse', 'workshop')
        .prefetch_related('spare_parts__part'),
        id=jobcard_id
    )

    # Permission check
    if role == 'NIC':
        if not department or job_card.department != department:
            messages.error(request, "You can only download job cards from your department.")
            return redirect('jobcard:waiting_jobcards')
    elif role == 'Tech':
        if not workshop or job_card.workshop != workshop:
            messages.error(request, "You can only download job cards from your workshop.")
            return redirect('jobcard:waiting_jobcards')
    elif role == 'HOD':
        pass  # HOD can download all
    else:
        messages.error(request, "Access denied.")
        return redirect('jobcard:create_job_card')

    # Generate and return modern PDF
    try:
        return create_jobcard_pdf_response(job_card)
    except Exception as e:
        logger.error(f"Error generating PDF for job card {jobcard_id}: {str(e)}", exc_info=True)
        messages.error(request, f"Error generating PDF: {str(e)}")
        return redirect('jobcard:approved_jobcards')


def _hod_redirect(request):
    """Send an HOD back to the workshop they downloaded from, or to their dashboard.

    ``workshop_id`` arrives from the query string and the route only accepts a
    UUID, so anything else (including a missing value) must not be reversed.
    """
    try:
        workshop_id = UUID(request.GET.get("workshop_id", ""))
    except ValueError:
        return redirect("dashboard:hod_dashboard")
    return redirect("jobcard:hod_workshop_jobcards", workshop_id=workshop_id)


@login_required
def bulk_download_approved_jobcards(request):
    """
    Bulk download approved job cards as PDFs in a ZIP file
    """
    profile, department, workshop, role = get_user_context(request)

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    if not start_date or not end_date:
        messages.error(request, "Please select a valid date range.")
        if role == 'HOD':
            return _hod_redirect(request)
        elif role == 'NIC':
            return redirect("jobcard:approved_jobcards")
        elif role == 'Tech':
            return redirect("jobcard:approved_jobcards")
        else:
            return redirect("jobcard:approved_jobcards")

    start = parse_date(start_date)
    end = parse_date(end_date)

    # Filter job cards based on user role
    if role == 'HOD':
        workshop_id = request.GET.get('workshop_id')
        if workshop_id:
            job_cards = jobcard.objects.filter(
                status="Approved",
                date_issued__range=[start, end],
                department__workshop_id=workshop_id
            ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
        else:
            job_cards = jobcard.objects.filter(
                status="Approved",
                date_issued__range=[start, end]
            ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
    elif role == 'NIC':
        job_cards = jobcard.objects.filter(
            status="Approved",
            date_issued__range=[start, end],
            department=department
        ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
    elif role == 'Tech':
        job_cards = jobcard.objects.filter(
            status="Approved",
            date_issued__range=[start, end],
            workshop=workshop
        ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
    else:
        job_cards = jobcard.objects.none()

    if not job_cards.exists():
        messages.warning(request, "No approved job cards found in this date range.", extra_tags="jobcard")
        if role == 'HOD':
            return _hod_redirect(request)
        elif role == 'NIC':
            return redirect("jobcard:approved_jobcards")
        elif role == 'Tech':
            return redirect("jobcard:approved_jobcards")
        else:
            return redirect("jobcard:approved_jobcards")

    # Create ZIP file with modern PDFs
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
        for job_card in job_cards:
            try:
                # Generate modern PDF directly
                pdf_buffer = generate_jobcard_pdf(job_card)
                filename = f"jobcard_{job_card.id}_Approved_{job_card.date_issued.strftime('%Y%m%d')}.pdf"
                zip_file.writestr(filename, pdf_buffer.getvalue())
            except Exception as e:
                logger.error(f"Error generating PDF for job card {job_card.id}: {str(e)}")
                continue

    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="approved_jobcards_{start}_{end}.zip"'
    return response


@login_required
def bulk_download_waiting_jobcards(request):
    """
    Bulk download waiting approval job cards as PDFs in a ZIP file
    """
    profile, department, workshop, role = get_user_context(request)

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    if not start_date or not end_date:
        messages.error(request, "Please select a valid date range.", extra_tags="jobcard")
        if role == 'HOD':
            return _hod_redirect(request)
        elif role == 'NIC':
            return redirect("jobcard:waiting_jobcards")
        elif role == 'Tech':
            return redirect("jobcard:waiting_jobcards")
        else:
            return redirect("jobcard:waiting_jobcards")

    start = parse_date(start_date)
    end = parse_date(end_date)

    # Filter job cards based on user role
    if role == 'HOD':
        workshop_id = request.GET.get('workshop_id')
        if workshop_id:
            job_cards = jobcard.objects.filter(
                status="Waiting Approval",
                date_issued__range=[start, end],
                department__workshop_id=workshop_id
            ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
        else:
            job_cards = jobcard.objects.filter(
                status="Waiting Approval",
                date_issued__range=[start, end]
            ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
    elif role == 'NIC':
        job_cards = jobcard.objects.filter(
            status="Waiting Approval",
            date_issued__range=[start, end],
            department=department
        ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
    elif role == 'Tech':
        job_cards = jobcard.objects.filter(
            status="Waiting Approval",
            date_issued__range=[start, end],
            workshop=workshop
        ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
    else:
        job_cards = jobcard.objects.none()

    if not job_cards.exists():
        messages.warning(request, "No waiting approval job cards found in this date range.", extra_tags="jobcard")
        if role == 'HOD':
            return _hod_redirect(request)
        elif role == 'NIC':
            return redirect("jobcard:waiting_jobcards")
        elif role == 'Tech':
            return redirect("jobcard:waiting_jobcards")
        else:
            return redirect("jobcard:waiting_jobcards")

    # Create ZIP file with modern PDFs
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
        for job_card in job_cards:
            try:
                pdf_buffer = generate_jobcard_pdf(job_card)
                filename = f"jobcard_{job_card.id}_Waiting_{job_card.date_issued.strftime('%Y%m%d')}.pdf"
                zip_file.writestr(filename, pdf_buffer.getvalue())
            except Exception as e:
                logger.error(f"Error generating PDF for job card {job_card.id}: {str(e)}")
                continue

    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="waiting_jobcards_{start}_{end}.zip"'
    return response


@login_required
def bulk_download_declined_jobcards(request):
    """
    Bulk download declined job cards as PDFs in a ZIP file
    """
    profile, department, workshop, role = get_user_context(request)

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    if not start_date or not end_date:
        messages.error(request, "Please select a valid date range.", extra_tags="jobcard")
        if role == 'HOD':
            return _hod_redirect(request)
        elif role == 'NIC':
            return redirect("jobcard:declined_jobcards")
        elif role == 'Tech':
            return redirect("jobcard:declined_jobcards")
        else:
            return redirect("jobcard:declined_jobcards")

    start = parse_date(start_date)
    end = parse_date(end_date)

    # Filter job cards based on user role
    if role == 'HOD':
        workshop_id = request.GET.get('workshop_id')
        if workshop_id:
            job_cards = jobcard.objects.filter(
                status="Declined",
                date_issued__range=[start, end],
                department__workshop_id=workshop_id
            ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
        else:
            job_cards = jobcard.objects.filter(
                status="Declined",
                date_issued__range=[start, end]
            ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
    elif role == 'NIC':
        job_cards = jobcard.objects.filter(
            status="Declined",
            date_issued__range=[start, end],
            department=department
        ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
    elif role == 'Tech':
        job_cards = jobcard.objects.filter(
            status="Declined",
            date_issued__range=[start, end],
            workshop=workshop
        ).select_related("department", "equipment", "performed_by", "workshop").prefetch_related('spare_parts__part')
    else:
        job_cards = jobcard.objects.none()

    if not job_cards.exists():
        messages.warning(request, "No declined job cards found in this date range.", extra_tags="jobcard")
        if role == 'HOD':
            return _hod_redirect(request)
        elif role == 'NIC':
            return redirect("jobcard:declined_jobcards")
        elif role == 'Tech':
            return redirect("jobcard:declined_jobcards")
        else:
            return redirect("jobcard:declined_jobcards")

    # Create ZIP file with modern PDFs
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
        for job_card in job_cards:
            try:
                pdf_buffer = generate_jobcard_pdf(job_card)
                filename = f"jobcard_{job_card.id}_Declined_{job_card.date_issued.strftime('%Y%m%d')}.pdf"
                zip_file.writestr(filename, pdf_buffer.getvalue())
            except Exception as e:
                logger.error(f"Error generating PDF for job card {job_card.id}: {str(e)}")
                continue

    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="declined_jobcards_{start}_{end}.zip"'
    return response
