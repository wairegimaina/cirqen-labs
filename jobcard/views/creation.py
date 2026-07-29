"""jobcard.views — job-card creation entry point and pending-PPM lookup."""
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
from .helpers import get_user_context
from .nurse_approval import handle_nurse_approval
from .technician_approval import handle_technician_job_card


@login_required
def get_pending_ppm_schedules(request):
    """
    AJAX endpoint to get pending PPM schedules for selected equipment.
    Returns schedules that need to be completed.
    """
    equipment_id = request.GET.get('equipment_id')

    if not equipment_id:
        return JsonResponse({'error': 'Equipment ID is required'}, status=400)

    try:
        from ppms.models import PPMSchedule
        from uuid import UUID
        from calendar import monthrange

        equipment_uuid = UUID(equipment_id)

        # Get user's workshop
        profile, _, workshop, role = get_user_context(request)

        if not workshop:
            return JsonResponse({'error': 'No workshop access'}, status=403)

        # Get pending PPM schedules for this equipment
        schedules = PPMSchedule.objects.filter(
            equipment_id=equipment_uuid,
            workshop=workshop,
            status__in=['pending', 'pushed'],
            active_status=True
        ).select_related('equipment', 'equipment__description').order_by('scheduled_month')

        data = []
        today = now().date()

        for schedule in schedules:
            # Check if overdue - use LAST DAY of scheduled month
            last_day = monthrange(schedule.scheduled_month.year, schedule.scheduled_month.month)[1]
            month_end = schedule.scheduled_month.replace(day=last_day)
            is_overdue = today > month_end
            days_until_due = (month_end - today).days if not is_overdue else 0

            data.append({
                'id': str(schedule.id),
                'scheduled_month': schedule.scheduled_month.strftime('%B %Y'),
                'scheduled_month_short': schedule.scheduled_month.strftime('%b %Y'),
                'status': schedule.status,
                'status_display': schedule.get_status_display(),
                'maintenance_period': schedule.maintenance_period,
                'is_overdue': is_overdue,
                'days_until_due': days_until_due,
                'equipment_name': schedule.equipment.description.name if schedule.equipment.description else 'N/A',
                'equipment_serial': schedule.equipment.serial_number or 'N/A',
            })

        logger.info(
            f"User {request.user.username} loaded {len(data)} pending PPM schedule(s) "
            f"for equipment {equipment_uuid}"
        )

        return JsonResponse({
            'success': True,
            'schedules': data,
            'count': len(data)
        })

    except ValueError:
        logger.error(f"Invalid equipment ID format: {equipment_id}")
        return JsonResponse({'error': 'Invalid equipment ID format'}, status=400)
    except Exception as e:
        logger.error(f"Error loading PPM schedules: {str(e)}", exc_info=True)
        return JsonResponse({'error': f'Failed to load schedules: {str(e)}'}, status=500)


def create_job_card(request):
    profile, department, workshop, role = get_user_context(request)

    if not profile:
        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'error': "User profile not found or incomplete. Please ensure your profile is set up correctly.",
            'is_nurse': False,
            'is_technician': False,
            'departments': Department.objects.none(),
            'accessories': Accessories.objects.none(),
            'job_cards': jobcard.objects.none()
        })

    is_nurse = role == 'NIC'
    is_technician = role == 'Tech'

    if not (is_nurse or is_technician):
        messages.error(request, "Unauthorized role for this action.", extra_tags="jobcard")
        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'error': "Unauthorized role for this action.",
            'is_nurse': False,
            'is_technician': False,
            'departments': Department.objects.none(),
            'accessories': Accessories.objects.none(),
            'job_cards': jobcard.objects.none()
        })

    departments = Department.objects.none()
    accessories = Accessories.objects.none()
    job_cards = jobcard.objects.none()
    selected_job_card = None
    form_data = request.POST if request.method == 'POST' else {}
    user_signature_available = False

    try:
        user_signature = UserSignature.objects.get(user=request.user, active_status=True)
        user_signature_available = bool(user_signature.signature_image)
    except UserSignature.DoesNotExist:
        user_signature_available = False

    if is_technician and workshop:
        if workshop.category == 'calibration_center':
            departments = Department.objects.all().order_by('name')
            accessories = Accessories.objects.all().order_by('name')
        else:
            departments = Department.objects.filter(workshop=workshop).order_by('name')
            accessories = Accessories.objects.filter(workshop=workshop).order_by('name')

        logger.debug(f"Loaded {accessories.count()} accessories for workshop {workshop.id}")

    elif is_nurse and department:
        departments = Department.objects.filter(id=department.id)
        job_cards = jobcard.objects.filter(status="Waiting Approval", department=department)

    if request.method == 'POST':
        if is_nurse and ('approve' in request.POST or 'decline' in request.POST):
            return handle_nurse_approval(request, department)
        elif is_technician:
            return handle_technician_job_card(request, workshop)

    if 'jobcard_id' in request.GET and is_nurse:
        selected_job_card = jobcard.objects.filter(
            id=request.GET.get('jobcard_id'),
            status="Waiting Approval",
            department=department
        ).first()
        if selected_job_card:
            form_data = {
                'jobcard_id': selected_job_card.id,
                'nurse_name': request.user.get_full_name(),
                'nurse_signature_data': selected_job_card.verified_signature,
            }

    if is_technician:
        logger.debug(f"Technician {request.user.username} - Workshop: {workshop}, Accessories count: {accessories.count()}")

    return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
        'departments': departments,
        'accessories': accessories,
        'is_nurse': is_nurse,
        'is_technician': is_technician,
        'job_cards': job_cards,
        'selected_job_card': selected_job_card,
        'role': role,
        'department': department,
        'workshop': workshop,
        'user_signature_available': user_signature_available,
    })
