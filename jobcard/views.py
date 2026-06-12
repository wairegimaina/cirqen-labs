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
from .models import jobcard, SparePartUsed
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

def get_user_context(request):
    try:
        profile = UserProfile.objects.select_related('workshop', 'department').get(user=request.user)
        role = profile.role
        department = profile.department if role == 'NIC' else None
        workshop = profile.workshop if role in {'Tech', 'HOD'} else None

        if role == 'HOD' and not workshop:
            logger.warning(
                f"HOD profile for user {request.user.username}: HOD can oversee all jobcards but cannot create any."
            )

        if role == 'Tech' and not workshop:
            messages.error(request, "Technician profile must have an associated workshop.")
            return profile, None, None, role

        if role == 'NIC' and not department:
            messages.error(request, "Nurse profile must have an associated department.")
            return profile, None, None, role

        return profile, department, workshop, role

    except UserProfile.DoesNotExist:
        messages.error(
            request,
            "User profile not found. Please complete your profile setup.",
            extra_tags="jobcard"
        )
        logger.error(f"UserProfile not found for user: {request.user.username}")
        return None, None, None, None

    except Exception as e:
        messages.error(
            request,
            f"An unexpected error occurred while fetching user context: {e}",
            extra_tags="jobcard"
        )
        logger.error(f"Unexpected error in get_user_context for user {request.user.username}: {e}", exc_info=True)
        return None, None, None, None


def get_or_create_user_signature(user):
    """
    Get or create a UserSignature for the given user.
    Returns the signature image data as base64 for backward compatibility.
    """
    try:
        user_signature, created = UserSignature.objects.get_or_create(
            user=user,
            defaults={'is_active': True}
        )

        if created or not user_signature.signature_image:
            user_signature.generate_signature_image()
            user_signature.save()

        if user_signature.signature_image:
            with user_signature.signature_image.open('rb') as img_file:
                img_data = img_file.read()
                base64_data = base64.b64encode(img_data).decode('utf-8')
                return f"data:image/png;base64,{base64_data}"

        return None
    except Exception as e:
        logger.error(f"Error getting/creating user signature for {user.username}: {str(e)}")
        return None


def signature_to_image(signature_data, doc_template):
    """
    Convert signature data (base64 or UserSignature) to InlineImage for docxtpl.
    Handles both legacy base64 data and new UserSignature references.
    """
    if not signature_data or signature_data == "N/A" or signature_data.strip() == "":
        return None

    try:
        if isinstance(signature_data, UserSignature):
            if signature_data.signature_image:
                with signature_data.signature_image.open('rb') as img_file:
                    img_buffer = BytesIO(img_file.read())
                    return InlineImage(doc_template, img_buffer, width=Mm(50), height=Mm(25))
            return None

        if isinstance(signature_data, str):
            if signature_data.startswith('data:image'):
                signature_data = signature_data.split(',')[1]

            img_data = base64.b64decode(signature_data)
            img = Image.open(io.BytesIO(img_data))

            if img.mode in ('RGBA', 'LA'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            img_buffer = io.BytesIO()
            img.save(img_buffer, format='PNG')
            img_buffer.seek(0)

            return InlineImage(doc_template, img_buffer, width=Mm(50), height=Mm(25))

    except Exception as e:
        logger.error(f"Error converting signature to image: {str(e)}", exc_info=True)
        return None


def generate_docx(job_card, workshop):
    from datetime import datetime, time

    def format_time(time_value):
        if not time_value:
            return "N/A"
        if isinstance(time_value, time):
            return time_value.strftime('%H:%M')
        if isinstance(time_value, str):
            try:
                parsed_time = datetime.strptime(time_value, '%H:%M').time()
                return parsed_time.strftime('%H:%M')
            except ValueError:
                try:
                    parsed_time = datetime.strptime(time_value, '%H:%M:%S').time()
                    return parsed_time.strftime('%H:%M')
                except ValueError:
                    return time_value
        if hasattr(time_value, 'time'):
            return time_value.time().strftime('%H:%M')
        if hasattr(time_value, 'strftime'):
            return time_value.strftime('%H:%M')
        return str(time_value)

    try:
        template_path = os.path.join(settings.BASE_DIR, 'templates/docx/Document 8.docx')
        doc = DocxTemplate(template_path)

        logger.debug(f"Using template from {template_path} for job_card {job_card.id}")

        spare_parts = SparePartUsed.objects.filter(job_card=job_card)
        spare_parts_list = []

        if spare_parts.exists():
            for sp in spare_parts:
                spare_parts_list.append({
                    'description': sp.part.description if sp.part else "N/A",
                    'quantity': str(sp.quantity),
                    'remarks': sp.remarks or "N/A"
                })
        else:
            spare_parts_list.append({
                'description': "No spare parts used",
                'quantity': "0",
                'remarks': "N/A"
            })

        tech_signature_img = None
        nurse_signature_img = None

        if job_card.tech_signature:
            tech_signature_img = signature_to_image(job_card.tech_signature, doc)
        elif job_card.performed_by:
            try:
                user_signature = UserSignature.objects.get(user=job_card.performed_by, active_status=True)
                tech_signature_img = signature_to_image(user_signature, doc)
            except UserSignature.DoesNotExist:
                pass

        if job_card.verified_signature:
            nurse_signature_img = signature_to_image(job_card.verified_signature, doc)
        elif job_card.verified_by_nurse:
            try:
                user_signature = UserSignature.objects.get(user=job_card.verified_by_nurse, active_status=True)
                nurse_signature_img = signature_to_image(user_signature, doc)
            except UserSignature.DoesNotExist:
                pass

        logger.debug(f"Tech signature processed: {'Success' if tech_signature_img else 'Failed'}")
        logger.debug(f"Nurse signature processed: {'Success' if nurse_signature_img else 'Failed'}")

        context = {
            'id': job_card.id,
            'workshop_name': workshop.name if workshop else 'N/A',
            'department_name': job_card.department.name if job_card.department else 'N/A',
            'equipment_description': job_card.equipment.description if job_card.equipment else 'N/A',
            'equipment_serial': job_card.equipment.serial_number if job_card.equipment else 'N/A',
            'equipment_model': job_card.equipment.model if job_card.equipment else 'N/A',
            'equipment_manufacturer': job_card.equipment.manufacturer if job_card.equipment and job_card.equipment.manufacturer else 'N/A',
            'equipment_status': job_card.equipment.status if job_card.equipment else 'N/A',
            'date_issued': job_card.date_issued.strftime('%Y-%m-%d') if job_card.date_issued else 'N/A',
            'priority_level': job_card.priority_level or 'N/A',
            'status': job_card.status or 'N/A',
            'job_description': job_card.job_description or 'N/A',
            'action_taken': job_card.action_taken or 'N/A',
            'time_started': format_time(job_card.time_started),
            'time_completed': format_time(job_card.time_completed),
            'performed_by': job_card.performed_by.get_full_name() if job_card.performed_by else 'N/A',
            'spare_parts': spare_parts_list,
            'tech_signature': tech_signature_img,
            'nurse_signature': nurse_signature_img,
            'verified_by_nurse': job_card.nurse_name if job_card.nurse_name else (job_card.verified_by_nurse.get_full_name() if job_card.verified_by_nurse else 'N/A'),
            'has_tech_signature': tech_signature_img is not None,
            'has_nurse_signature': nurse_signature_img is not None,
            'decline_reason': job_card.decline_reason if job_card.status == 'Declined' else 'N/A',
        }

        doc.render(context)
        buffer = BytesIO()
        doc.save(buffer)
        buffer.seek(0)

        logger.debug(f"Generated docx using template for job_card {job_card.id}")
        return buffer

    except Exception as e:
        logger.error(f"Error generating docx for job_card {job_card.id}: {str(e)}", exc_info=True)
        raise
# views.py

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
def handle_nurse_approval(request, nurse_department):
    import logging
    from uuid import UUID
    from django.utils.timezone import now
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from django.contrib import messages
    from django.shortcuts import render, redirect

    logger = logging.getLogger(__name__)

    jobcard_id = request.POST.get('jobcard_id')
    nurse_name = request.POST.get('nurse_name')
    nurse_signature_data = request.POST.get('nurse_signature_data')
    use_auto_signature = request.POST.get('use_auto_signature') == 'true'
    decline_reason = request.POST.get('decline_reason', '').strip()

    # Store form data for re-rendering in case of errors
    form_data = {
        'jobcard_id': jobcard_id,
        'nurse_name': nurse_name,
        'nurse_signature_data': nurse_signature_data,
        'decline_reason': decline_reason,
        'use_auto_signature': use_auto_signature
    }

    # Get the context data for rendering
    waiting_jobcards = jobcard.objects.filter(status="Waiting Approval", department=nurse_department)
    departments_list = Department.objects.filter(id=nurse_department.id)

    # Get selected job card for display
    selected_job_card = None
    if jobcard_id:
        try:
            selected_job_card = jobcard.objects.filter(
                id=jobcard_id,
                status="Waiting Approval",
                department=nurse_department
            ).first()
        except (ValueError, AttributeError):
            selected_job_card = None

    # Basic validation
    if not use_auto_signature and not nurse_signature_data:
        messages.error(request, "Please provide a signature or enable auto-signature.", extra_tags="jobcard")
        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
            'is_nurse': True,
            'is_technician': False,
            'job_cards': waiting_jobcards,
            'departments': departments_list,
            'accessories': Accessories.objects.none(),
            'selected_job_card': selected_job_card,
            'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
        })

    if not all([jobcard_id, nurse_name]):
        messages.error(request, "Job card and nurse name are required.", extra_tags="jobcard")
        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
            'is_nurse': True,
            'is_technician': False,
            'job_cards': waiting_jobcards,
            'departments': departments_list,
            'accessories': Accessories.objects.none(),
            'selected_job_card': selected_job_card,
            'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
        })

    try:
        # Validate UUID format
        try:
            jobcard_uuid = UUID(jobcard_id)
        except (ValueError, AttributeError):
            messages.error(request, "Invalid job card ID format.", extra_tags="jobcard")
            return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                'is_nurse': True,
                'is_technician': False,
                'job_cards': waiting_jobcards,
                'departments': departments_list,
                'accessories': Accessories.objects.none(),
                'selected_job_card': selected_job_card,
                'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
            })

        with transaction.atomic():
            # Get job card with lock for update
            job_card = jobcard.objects.select_for_update().get(
                id=jobcard_uuid,
                status="Waiting Approval"
            )

            # Verify department permission
            if job_card.department != nurse_department:
                messages.error(request, "You can only approve/decline job cards for your department.", extra_tags="jobcard")
                return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                    'is_nurse': True,
                    'is_technician': False,
                    'job_cards': waiting_jobcards,
                    'departments': departments_list,
                    'accessories': Accessories.objects.none(),
                    'selected_job_card': job_card,
                    'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
                })

            # Set nurse verification details
            job_card.verified_by_nurse = request.user
            job_card.nurse_name = nurse_name
            job_card.nurse_signed_date = now()

            # Handle signature
            if use_auto_signature:
                auto_signature = get_or_create_user_signature(request.user)
                if auto_signature:
                    job_card.verified_signature = auto_signature
                else:
                    messages.warning(request, "Auto-signature could not be generated. Using manual signature.", extra_tags="jobcard")
                    job_card.verified_signature = nurse_signature_data
            else:
                job_card.verified_signature = nurse_signature_data

            # Handle approval
            if 'approve' in request.POST:
                try:
                    # Check if job card is linked to PPM schedule and validate
                    if job_card.related_ppm_schedule:
                        ppm_schedule = job_card.related_ppm_schedule

                        # ✅ NEW: Additional validation for PPM schedule
                        if ppm_schedule.status == 'completed':
                            messages.error(
                                request,
                                f"Cannot approve: Linked PPM schedule for {ppm_schedule.scheduled_month.strftime('%B %Y')} "
                                f"is already marked as completed.",
                                extra_tags="jobcard"
                            )
                            return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                                'is_nurse': True,
                                'is_technician': False,
                                'job_cards': waiting_jobcards,
                                'departments': departments_list,
                                'accessories': Accessories.objects.none(),
                                'selected_job_card': job_card,
                                'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
                            })

                        # Validate equipment still matches
                        if ppm_schedule.equipment != job_card.equipment:
                            messages.error(
                                request,
                                f"PPM schedule equipment mismatch. Schedule is for {ppm_schedule.equipment.description}, "
                                f"job card is for {job_card.equipment.description}.",
                                extra_tags="jobcard"
                            )
                            return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                                'is_nurse': True,
                                'is_technician': False,
                                'job_cards': waiting_jobcards,
                                'departments': departments_list,
                                'accessories': Accessories.objects.none(),
                                'selected_job_card': job_card,
                                'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
                            })

                    # Deduct stock and update job card
                    job_card.deduct_stock()
                    job_card.status = "Approved"
                    job_card.decline_reason = None
                    job_card.save()

                    # ✅ NEW: Update linked PPM schedule if exists
                    ppm_updated = False
                    if job_card.related_ppm_schedule:
                        ppm_updated = job_card.update_ppm_status_if_applicable()

                    # ✅ MODIFIED: Success message with PPM info
                    success_message = (
                        f"Job card #{job_card.id} approved successfully. "
                        f"Total cost: KSh {job_card.get_total_cost():,.2f}. "
                        f"Stock has been updated."
                    )

                    if ppm_updated:
                        ppm_schedule = job_card.related_ppm_schedule
                        success_message += (
                            f" PPM schedule for {ppm_schedule.scheduled_month.strftime('%B %Y')} "
                            f"has been marked as completed."
                        )
                        logger.info(
                            f"✅ Job card #{job_card.id} approval completed PPM schedule "
                            f"{ppm_schedule.id} ({ppm_schedule.scheduled_month.strftime('%B %Y')})"
                        )
                    elif job_card.action_taken == 'PPM' and not job_card.related_ppm_schedule:
                        success_message += " (Manual PPM work - no schedule was linked)"
                        logger.info(
                            f"ℹ️ Job card #{job_card.id} was manual PPM (no linked schedule)"
                        )
                    elif job_card.action_taken == 'PPM' and job_card.related_ppm_schedule:
                        # PPM schedule was linked but not updated (already completed)
                        success_message += f" (PPM schedule was already completed)"

                    messages.success(request, success_message, extra_tags="jobcard")

                    # Log the approval with details
                    logger.info(
                        f"📋 Job card #{job_card.id} approved by {request.user.get_full_name()} ({nurse_name})\n"
                        f"   Equipment: {job_card.equipment.description}\n"
                        f"   Action: {job_card.action_taken}\n"
                        f"   Total Cost: KSh {job_card.get_total_cost():,.2f}\n"
                        f"   Department: {job_card.department.name}\n"
                        f"   Workshop: {job_card.workshop.name}\n"
                        f"   PPM Linked: {'Yes' if job_card.related_ppm_schedule else 'No'}"
                    )

                    return redirect('jobcard:approved_jobcards')

                except ValidationError as e:
                    messages.error(request, f"Cannot approve job card: {str(e)}", extra_tags="jobcard")
                    logger.error(f"Validation error approving job card #{job_card.id}: {str(e)}")
                    return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                        'is_nurse': True,
                        'is_technician': False,
                        'job_cards': waiting_jobcards,
                        'departments': departments_list,
                        'accessories': Accessories.objects.none(),
                        'selected_job_card': job_card,
                        'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
                    })

            # Handle decline
            elif 'decline' in request.POST:
                if not decline_reason:
                    messages.error(request, "Reason for decline is required.", extra_tags="jobcard")
                    return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                        'is_nurse': True,
                        'is_technician': False,
                        'job_cards': waiting_jobcards,
                        'departments': departments_list,
                        'accessories': Accessories.objects.none(),
                        'selected_job_card': job_card,
                        'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
                    })

                # ✅ NEW: Check if declining a PPM-linked job card
                if job_card.related_ppm_schedule and job_card.action_taken == 'PPM':
                    logger.warning(
                        f"⚠️ Declining PPM-linked job card #{job_card.id}. "
                        f"PPM schedule {job_card.related_ppm_schedule.id} remains pending."
                    )

                job_card.status = "Declined"
                job_card.decline_reason = decline_reason
                job_card.save()

                messages.success(
                    request,
                    f"Job card #{job_card.id} declined successfully. No stock changes made.",
                    extra_tags="jobcard"
                )

                # Log the decline
                logger.info(
                    f"❌ Job card #{job_card.id} declined by {request.user.get_full_name()} ({nurse_name})\n"
                    f"   Reason: {decline_reason[:100]}..."
                )

                return redirect('jobcard:waiting_jobcards')

            else:
                # Neither approve nor decline button was pressed
                messages.error(request, "Invalid action requested.", extra_tags="jobcard")
                return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                    'is_nurse': True,
                    'is_technician': False,
                    'job_cards': waiting_jobcards,
                    'departments': departments_list,
                    'accessories': Accessories.objects.none(),
                    'selected_job_card': job_card,
                    'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
                })

    except jobcard.DoesNotExist:
        messages.error(request,
            "Job card not found, already processed, or you don't have permission to access it.",
            extra_tags="jobcard"
        )
        logger.warning(f"Job card not found or inaccessible: {jobcard_id}")
        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
            'is_nurse': True,
            'is_technician': False,
            'job_cards': waiting_jobcards,
            'departments': departments_list,
            'accessories': Accessories.objects.none(),
            'selected_job_card': selected_job_card,
            'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
        })

    except Exception as e:
        logger.error(f"Error in nurse approval/decline for jobcard_id {jobcard_id}: {str(e)}", exc_info=True)
        messages.error(request, f"Error processing job card: {str(e)}", extra_tags="jobcard")
        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
            'is_nurse': True,
            'is_technician': False,
            'job_cards': waiting_jobcards,
            'departments': departments_list,
            'accessories': Accessories.objects.none(),
            'selected_job_card': selected_job_card,
            'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
        })

def get_filtered_jobcards(queryset, search_query=None):
    if search_query:
        term = search_query.strip()
        if term:
            filters = (
                Q(job_description__icontains=term) |
                Q(priority_level__icontains=term) |
                Q(status__icontains=term) |
                Q(equipment__description__name__icontains=term) |
                Q(equipment__serial_number__icontains=term) |
                Q(equipment__model__icontains=term) |
                Q(equipment__manufacturer__name__icontains=term) |
                Q(department__name__icontains=term) |
                Q(performed_by__username__icontains=term) |
                Q(performed_by__first_name__icontains=term) |
                Q(performed_by__last_name__icontains=term) |
                Q(verified_by_nurse__username__icontains=term) |
                Q(verified_by_nurse__first_name__icontains=term) |
                Q(verified_by_nurse__last_name__icontains=term)
            )

            if term.isdigit():
                filters |= Q(id=int(term))

            queryset = queryset.filter(filters)
    return queryset

def handle_technician_job_card(request, workshop):
    logger = logging.getLogger(__name__)

    # Get form data
    priority_level = request.POST.get('priority_level')
    department_id = request.POST.get('department')
    equipment_id = request.POST.get('equipment')
    job_description = request.POST.get('job_description')
    action_taken = request.POST.get('action_taken')
    time_started = request.POST.get('time_started')
    time_completed = request.POST.get('time_completed')
    signature_data = request.POST.get('signature_data')
    use_auto_signature = request.POST.get('use_auto_signature') == 'true'
    spare_parts_data = request.POST.get('spare_parts_data', '[]')

    # ✅ NEW: Get PPM schedule ID
    ppm_schedule_id = request.POST.get('ppm_schedule_id')

    # Cost fields
    labor_cost = request.POST.get('labor_cost', '0.00')
    additional_costs = request.POST.get('additional_costs', '0.00')
    additional_costs_description = request.POST.get('additional_costs_description', '')

    # Store form data for re-rendering in case of errors
    form_data = {
        'priority_level': priority_level,
        'department': department_id,
        'equipment': equipment_id,
        'job_description': job_description,
        'action_taken': action_taken,
        'time_started': time_started,
        'time_completed': time_completed,
        'signature_data': signature_data,
        'use_auto_signature': use_auto_signature,
        'spare_parts_data': spare_parts_data,
        'labor_cost': labor_cost,
        'additional_costs': additional_costs,
        'additional_costs_description': additional_costs_description,
        'ppm_schedule_id': ppm_schedule_id,  # ✅ NEW: Store PPM schedule ID
    }

    # Get departments and accessories based on workshop type
    if workshop and workshop.category == 'calibration_center':
        departments_for_render = Department.objects.filter(active_status=True).order_by('name')
        accessories_for_render = Accessories.objects.filter(active_status=True).order_by('name')
    else:
        departments_for_render = Department.objects.filter(workshop=workshop, active_status=True).order_by('name') if workshop else Department.objects.none()
        accessories_for_render = Accessories.objects.filter(workshop=workshop, active_status=True).order_by('name') if workshop else Accessories.objects.none()

    # Basic validation
    if not use_auto_signature and not signature_data:
        messages.error(request, "Please provide a signature or enable auto-signature.", extra_tags="jobcard")
        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
            'is_technician': True,
            'is_nurse': False,
            'departments': departments_for_render,
            'accessories': accessories_for_render,
            'job_cards': jobcard.objects.none()
        })

    if not all([priority_level, department_id, equipment_id, job_description, action_taken, time_started]):
        messages.error(request, "All required fields must be provided.", extra_tags="jobcard")
        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
            'is_technician': True,
            'is_nurse': False,
            'departments': departments_for_render,
            'accessories': accessories_for_render,
            'job_cards': jobcard.objects.none()
        })

    # ✅ NEW: Validate action based on workshop category
    if workshop.category == 'maintenance':
        # Maintenance workshops can only do PPM, Repair, and Others
        allowed_actions = ['PPM', 'Repair', 'Others']
        if action_taken not in allowed_actions:
            messages.error(
                request,
                f"Action '{action_taken}' is not allowed for maintenance workshops. "
                f"Allowed actions: {', '.join(allowed_actions)}",
                extra_tags="jobcard"
            )
            return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                'is_technician': True,
                'is_nurse': False,
                'departments': departments_for_render,
                'accessories': accessories_for_render,
                'job_cards': jobcard.objects.none()
            })

    elif workshop.category == 'calibration_center':
        # Calibration centers can only do Calibration and Others
        allowed_actions = ['Calibration', 'Others']
        if action_taken not in allowed_actions:
            messages.error(
                request,
                f"Action '{action_taken}' is not allowed for calibration centers. "
                f"Allowed actions: {', '.join(allowed_actions)}",
                extra_tags="jobcard"
            )
            return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                'is_technician': True,
                'is_nurse': False,
                'departments': departments_for_render,
                'accessories': accessories_for_render,
                'job_cards': jobcard.objects.none()
            })

    # ✅ NEW: Additional validation for PPM schedule linking
    if action_taken == 'PPM' and ppm_schedule_id:
        if workshop.category != 'maintenance':
            messages.warning(
                request,
                "PPM schedules can only be linked in maintenance workshops. Creating job card without PPM link.",
                extra_tags="jobcard"
            )
            ppm_schedule_id = None

    # ✅ NEW: Validate PPM schedule if action is PPM
    if action_taken == 'PPM' and ppm_schedule_id:
        try:
            ppm_schedule_uuid = UUID(ppm_schedule_id)
            if ppm_schedule_uuid:
                from ppms.models import PPMSchedule
                # Verify schedule exists (will validate equipment match later)
                try:
                    PPMSchedule.objects.get(id=ppm_schedule_uuid, active_status=True)
                except PPMSchedule.DoesNotExist:
                    messages.warning(
                        request,
                        "Selected PPM schedule not found or inactive. Creating job card without PPM link.",
                        extra_tags="jobcard"
                    )
                    ppm_schedule_id = None  # Reset to create without link
        except (ValueError, AttributeError):
            messages.warning(
                request,
                "Invalid PPM schedule format. Creating job card without PPM link.",
                extra_tags="jobcard"
            )
            ppm_schedule_id = None

    try:
        if not workshop:
            messages.error(request, "Technician's workshop not found. Cannot create job card.", extra_tags="jobcard")
            return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                'is_technician': True,
                'is_nurse': False,
                'departments': departments_for_render,
                'accessories': accessories_for_render,
                'job_cards': jobcard.objects.none()
            })

        # Validate UUID format
        try:
            department_uuid = UUID(department_id)
            equipment_uuid = UUID(equipment_id)
        except (ValueError, AttributeError) as e:
            messages.error(request, f"Invalid department or equipment ID format: {e}", extra_tags="jobcard")
            logger.error(f"Invalid UUID format - department_id: {department_id}, equipment_id: {equipment_id}")
            return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                'is_technician': True,
                'is_nurse': False,
                'departments': departments_for_render,
                'accessories': accessories_for_render,
                'job_cards': jobcard.objects.none()
            })

        # Get department and equipment
        if workshop.category == 'calibration_center':
            department = Department.objects.get(id=department_uuid, active_status=True)
            equipment = Equipment.objects.get(id=equipment_uuid, active_status=True)
        else:
            department = Department.objects.get(id=department_uuid, workshop=workshop, active_status=True)
            equipment = Equipment.objects.get(id=equipment_uuid, active_status=True)

    except Department.DoesNotExist:
        messages.error(request, "Invalid department selected or department is inactive.", extra_tags="jobcard")
        logger.error(f"Department not found or inactive: {department_id}")
        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
            'is_technician': True,
            'is_nurse': False,
            'departments': departments_for_render,
            'accessories': accessories_for_render,
            'job_cards': jobcard.objects.none()
        })
    except Equipment.DoesNotExist:
        messages.error(request, "Invalid equipment selected or equipment is inactive.", extra_tags="jobcard")
        logger.error(f"Equipment not found or inactive: {equipment_id}")
        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
            'is_technician': True,
            'is_nurse': False,
            'departments': departments_for_render,
            'accessories': accessories_for_render,
            'job_cards': jobcard.objects.none()
        })

    try:
        with transaction.atomic():
            # Handle signature
            final_signature = signature_data
            if use_auto_signature:
                final_signature = get_or_create_user_signature(request.user)
                if not final_signature:
                    messages.warning(request, "Auto-signature could not be generated. Using manual signature.", extra_tags="jobcard")
                    final_signature = signature_data

            # Convert and validate costs
            try:
                labor_cost_decimal = Decimal(labor_cost.strip()) if labor_cost and labor_cost.strip() else Decimal('0.00')
                additional_costs_decimal = Decimal(additional_costs.strip()) if additional_costs and additional_costs.strip() else Decimal('0.00')

                # Validate that costs are non-negative
                if labor_cost_decimal < 0 or additional_costs_decimal < 0:
                    raise ValueError("Costs cannot be negative")

            except (InvalidOperation, ValueError) as e:
                logger.error(f"Invalid cost format: labor_cost={labor_cost}, additional_costs={additional_costs}, error={str(e)}")
                messages.error(request, "Invalid cost format. Please enter valid positive numbers.", extra_tags="jobcard")
                return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
                    'is_technician': True,
                    'is_nurse': False,
                    'departments': departments_for_render,
                    'accessories': accessories_for_render,
                    'job_cards': jobcard.objects.none()
                })

            # ✅ NEW: Get PPM schedule if provided and action is PPM
            ppm_schedule = None
            if ppm_schedule_id and action_taken == 'PPM':
                try:
                    from ppms.models import PPMSchedule
                    ppm_schedule_uuid = UUID(ppm_schedule_id)

                    # Validate PPM schedule exists and is eligible
                    ppm_schedule = PPMSchedule.objects.get(
                        id=ppm_schedule_uuid,
                        equipment_id=equipment_uuid,
                        workshop=workshop,
                        status__in=['pending', 'pushed', 'overdue'],  # Allow linking to incomplete schedules
                        active_status=True
                    )

                    # Additional validation: Ensure schedule is not already linked to another completed job card
                    if hasattr(ppm_schedule, 'related_job_card') and ppm_schedule.related_job_card:
                        if ppm_schedule.related_job_card.status == 'Approved':
                            messages.warning(
                                request,
                                f"PPM schedule {ppm_schedule.scheduled_month.strftime('%B %Y')} is already linked to an approved job card. Creating job card without PPM link.",
                                extra_tags="jobcard"
                            )
                            ppm_schedule = None

                    if ppm_schedule:
                        logger.info(
                            f"Linking job card to PPM schedule {ppm_schedule.id} "
                            f"({ppm_schedule.scheduled_month.strftime('%B %Y')}) "
                            f"for equipment {equipment.serial_number}"
                        )

                except PPMSchedule.DoesNotExist:
                    logger.warning(
                        f"PPM schedule {ppm_schedule_id} not found, already completed, "
                        f"or doesn't match equipment/workshop"
                    )
                    ppm_schedule = None
                except (ValueError, AttributeError) as e:
                    logger.error(f"Invalid PPM schedule ID format: {ppm_schedule_id}, error: {e}")
                    ppm_schedule = None

            # ✅ MODIFIED: Create job card with PPM link
            job_card = jobcard.objects.create(
                department=department,
                equipment=equipment,
                workshop=workshop,
                priority_level=priority_level,
                job_description=job_description,
                action_taken=action_taken,
                time_started=time_started,
                time_completed=time_completed or None,
                performed_by=request.user,
                tech_signature=final_signature,
                technician_signed_date=now(),
                status="Waiting Approval",
                labor_cost=labor_cost_decimal,
                additional_costs=additional_costs_decimal,
                additional_costs_description=additional_costs_description.strip() if additional_costs_description else '',
                related_ppm_schedule=ppm_schedule  # ✅ Link PPM schedule
            )

            logger.debug(
                f"Created job_card {job_card.id} with workshop {workshop.id} ({workshop.name}), "
                f"status 'Waiting Approval', labor_cost={labor_cost_decimal}, "
                f"additional_costs={additional_costs_decimal}"
                + (f", linked to PPM schedule {ppm_schedule.id}" if ppm_schedule else "")
            )

            # Parse spare parts data
            try:
                parts_data = json.loads(spare_parts_data)
                if not isinstance(parts_data, list):
                    raise ValueError("Spare parts data must be a list")
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Invalid spare parts data format: {str(e)}")
                messages.error(request, f"Invalid spare parts data format: {str(e)}", extra_tags="jobcard")
                raise ValidationError(f"Invalid spare parts data: {str(e)}")

            # Validate stock availability before creating spare parts records
            stock_issues = []
            for part_entry in parts_data:
                part_id = part_entry.get('part_id')
                quantity = part_entry.get('quantity')

                if not part_id or part_id == 'none' or not quantity or int(quantity) <= 0:
                    continue

                try:
                    try:
                        part_uuid = UUID(part_id)
                    except (ValueError, AttributeError):
                        stock_issues.append(f"Invalid part ID format: {part_id}")
                        continue

                    if workshop.category == 'calibration_center':
                        part_obj = Accessories.objects.get(id=part_uuid, active_status=True)
                    else:
                        part_obj = Accessories.objects.get(id=part_uuid, workshop=workshop, active_status=True)

                    if part_obj.stock_count < int(quantity):
                        part_name = part_obj.name.name if part_obj.name else f"Part {part_obj.id}"
                        stock_issues.append(f"{part_name}: Available {part_obj.stock_count}, Requested {quantity}")

                except Accessories.DoesNotExist:
                    stock_issues.append(f"Part with ID {part_id} not found, inactive, or not accessible to your workshop")

            if stock_issues:
                error_msg = "Stock validation failed:\n" + "\n".join(stock_issues)
                messages.error(request, error_msg, extra_tags="jobcard")
                raise ValidationError(error_msg)

            # Create spare parts records with unit costs
            for part_entry in parts_data:
                part_id = part_entry.get('part_id')
                quantity = part_entry.get('quantity')
                remarks = part_entry.get('remarks', '')
                unit_cost = part_entry.get('unit_cost', '0.00')

                if not part_id or part_id == 'none' or not quantity or int(quantity) <= 0:
                    continue

                try:
                    try:
                        part_uuid = UUID(part_id)
                        cost_decimal = Decimal(str(unit_cost).strip()) if unit_cost else Decimal('0.00')

                        # Validate cost is non-negative
                        if cost_decimal < 0:
                            logger.warning(f"Negative unit cost detected for part {part_id}, setting to 0.00")
                            cost_decimal = Decimal('0.00')

                    except (ValueError, AttributeError, InvalidOperation) as e:
                        logger.error(f"Invalid UUID format or cost for part_id during creation: {part_id}, error: {str(e)}")
                        cost_decimal = Decimal('0.00')
                        continue

                    if workshop.category == 'calibration_center':
                        part_obj = Accessories.objects.get(id=part_uuid, active_status=True)
                    else:
                        part_obj = Accessories.objects.get(id=part_uuid, workshop=workshop, active_status=True)

                    SparePartUsed.objects.create(
                        job_card=job_card,
                        part=part_obj,
                        quantity=int(quantity),
                        remarks=remarks or "",
                        unit_cost=cost_decimal
                    )
                    logger.debug(f"Created SparePartUsed for part_id={part_id}, quantity={quantity}, unit_cost={cost_decimal}, workshop={workshop.name}")

                except Accessories.DoesNotExist:
                    logger.error(f"Part {part_id} not found or inactive during SparePartUsed creation")
                    continue

            # Update calculated costs
            job_card.update_costs()

            # Get the total cost for the success message
            total_cost = job_card.get_total_cost()

            # ✅ MODIFIED: Success message with PPM info
            success_message = (
                f"Job card #{job_card.id} created successfully by {workshop.name} "
                f"and is waiting for approval. Total cost: KSh {total_cost:,.2f}."
            )

            if ppm_schedule:
                success_message += (
                    f" Linked to PPM schedule for {ppm_schedule.scheduled_month.strftime('%B %Y')}. "
                    f"PPM will be marked as completed when approved."
                )
            elif action_taken == 'PPM':
                success_message += " (Manual/unscheduled PPM work)"

            success_message += " Stock will be deducted when approved."

            messages.success(request, success_message, extra_tags="jobcard")

            # Clear form data on successful submission
            request.session.pop('jobcard_form_data', None)

            return redirect('jobcard:waiting_jobcards')

    except ValidationError as e:
        logger.error(f"Validation error creating job card: {str(e)}")
        messages.error(request, f"Cannot create job card: {str(e)}", extra_tags="jobcard")

        # Store form data in session for persistence across redirects if needed
        request.session['jobcard_form_data'] = form_data

        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
            'is_technician': True,
            'is_nurse': False,
            'departments': departments_for_render,
            'accessories': accessories_for_render,
            'job_cards': jobcard.objects.none(),
            'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
        })

    except Exception as e:
        logger.error(f"Unexpected error creating job card: {str(e)}", exc_info=True)
        messages.error(request, f"Error creating job card: {str(e)}", extra_tags="jobcard")

        # Store form data in session for persistence
        request.session['jobcard_form_data'] = form_data

        return render(request, 'jobcard/jbb.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'form_data': form_data,
            'is_technician': True,
            'is_nurse': False,
            'departments': departments_for_render,
            'accessories': accessories_for_render,
            'job_cards': jobcard.objects.none(),
            'user_signature_available': hasattr(request.user, 'signature_data') and bool(request.user.signature_data)
        })

def get_user_signature_data(request):
    """
    AJAX endpoint to get user's auto-signature data
    """
    try:
        signature_data = get_or_create_user_signature(request.user)
        if signature_data:
            return JsonResponse({
                'success': True,
                'signature_data': signature_data,
                'message': 'Auto-signature loaded successfully'
            })
        else:
            return JsonResponse({
                'success': False,
                'message': 'Could not generate auto-signature'
            })
    except Exception as e:
        logger.error(f"Error getting user signature data: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': 'Error loading auto-signature'
        })


def load_accessories(request):
    """
    AJAX endpoint to load accessories based on workshop.
    Returns accessories with proper field access for ForeignKey relationships.
    """
    workshop_id = request.GET.get('workshop_id')

    if not workshop_id:
        logger.warning("load_accessories called without workshop_id")
        return JsonResponse({'error': 'Workshop ID is required'}, status=400)

    try:
        try:
            workshop_uuid = UUID(workshop_id)
        except ValueError:
            logger.error(f"Invalid UUID format for workshop_id: {workshop_id}")
            return JsonResponse({'error': 'Invalid Workshop ID format'}, status=400)

        logger.info(f"Loading accessories for workshop_id: {workshop_uuid}")

        accessories_qs = Accessories.objects.filter(
            workshop_id=workshop_uuid,
            active_status=True
        ).select_related(
            'name',
            'manufacturer',
            'equipment_description'
        ).order_by('name__name')

        data = []
        for acc in accessories_qs:
            accessory_name = acc.name.name if acc.name else 'Unnamed Accessory'
            manufacturer_name = acc.manufacturer.name if acc.manufacturer else 'Unknown Manufacturer'
            equipment_desc = acc.equipment_description.name if acc.equipment_description else 'No Equipment Description'

            data.append({
                'id': str(acc.id),
                'name': accessory_name,
                'manufacturer': manufacturer_name,
                'equipment_description': equipment_desc,
                'stock_count': acc.stock_count,
                'note': acc.note or '',
                'display_name': f"{accessory_name} - {manufacturer_name} (Stock: {acc.stock_count})"
            })

        logger.info(f"Successfully loaded {len(data)} accessories for workshop {workshop_uuid}")
        return JsonResponse(data, safe=False)

    except Exception as e:
        logger.error(f"Unexpected error loading accessories for workshop_id {workshop_id}: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Failed to load accessories. Please try again.'}, status=500)


def check_stock_availability(request):
    """
    AJAX endpoint to check current stock availability for selected parts.
    Accepts comma-separated part IDs (UUIDs) and returns detailed stock information.
    """
    part_ids_param = request.GET.get('part_ids', '').strip()

    logger.info(f"Stock check request from user {request.user.username} for part_ids: '{part_ids_param}'")

    if not part_ids_param:
        logger.warning("check_stock_availability called without part_ids")
        return JsonResponse({
            'success': False,
            'error': 'No parts specified',
            'data': {}
        }, status=400)

    part_ids = []
    for part_id in part_ids_param.split(','):
        part_id = part_id.strip()
        if part_id and part_id != 'none' and part_id != '':
            try:
                part_uuid = UUID(part_id)
                part_ids.append(part_uuid)
            except ValueError:
                logger.warning(f"Invalid UUID format for part_id: {part_id}")
                continue

    if not part_ids:
        return JsonResponse({
            'success': False,
            'error': 'No valid part IDs provided',
            'data': {}
        }, status=400)

    try:
        logger.info(f"Checking stock availability for part_ids: {part_ids}")

        try:
            profile = UserProfile.objects.select_related('workshop').get(user=request.user)
            user_workshop = profile.workshop
        except UserProfile.DoesNotExist:
            logger.warning(f"UserProfile not found for user {request.user.username}")
            user_workshop = None

        if user_workshop and user_workshop.category != 'calibration_center':
            parts = Accessories.objects.filter(
                id__in=part_ids,
                workshop=user_workshop,
                active_status=True
            ).select_related('name', 'manufacturer', 'equipment_description')
        else:
            parts = Accessories.objects.filter(
                id__in=part_ids,
                active_status=True
            ).select_related('name', 'manufacturer', 'equipment_description')

        parts_dict = {part.id: part for part in parts}
        logger.info(f"Found {len(parts_dict)} parts in database")

        stock_data = {}
        for part_id in part_ids:
            part_id_str = str(part_id)

            if part_id in parts_dict:
                part = parts_dict[part_id]

                try:
                    accessory_name = part.name.name if part.name else 'Unnamed Accessory'
                    manufacturer_name = part.manufacturer.name if part.manufacturer else 'Unknown'
                    equipment_desc = part.equipment_description.name if part.equipment_description else 'No Description'

                    stock_data[part_id_str] = {
                        'id': str(part.id),
                        'name': accessory_name,
                        'manufacturer': manufacturer_name,
                        'equipment_description': equipment_desc,
                        'current_stock': part.stock_count,
                        'available': part.stock_count > 0,
                        'status': 'available' if part.stock_count > 0 else 'out_of_stock',
                        'display_name': f"{accessory_name} ({manufacturer_name})"
                    }
                    logger.debug(f"Part {part_id} - {accessory_name}: stock={part.stock_count}")

                except Exception as e:
                    logger.error(f"Error processing part {part_id}: {str(e)}")
                    stock_data[part_id_str] = {
                        'id': str(part_id),
                        'error': f'Error processing part data: {str(e)}',
                        'available': False,
                        'current_stock': 0,
                        'status': 'error'
                    }
            else:
                logger.warning(f"Part {part_id} not found or inactive in database")
                stock_data[part_id_str] = {
                    'id': str(part_id),
                    'error': 'Part not found, inactive, or access denied',
                    'available': False,
                    'current_stock': 0,
                    'status': 'not_found'
                }

        logger.info(f"Successfully checked stock for {len(stock_data)} parts")

        return JsonResponse({
            'success': True,
            'data': stock_data,
            'total_parts': len(stock_data),
            'message': f'Stock check completed for {len(stock_data)} parts'
        })

    except Exception as e:
        logger.error(f"Unexpected error checking stock availability: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Server error: {str(e)}',
            'data': {}
        }, status=500)


def validate_stock_for_job_card(request):
    """
    Endpoint to validate stock availability for multiple parts before creating a job card.
    Used for pre-validation to prevent job card creation failures.
    """
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'error': 'POST method required'
        }, status=405)

    try:
        data = json.loads(request.body)
        parts_data = data.get('parts', [])
        workshop_id = data.get('workshop_id')

        logger.info(f"Job card stock validation for workshop {workshop_id}, {len(parts_data)} parts")

        if not workshop_id:
            return JsonResponse({
                'success': False,
                'error': 'Workshop ID is required'
            }, status=400)

        if not parts_data:
            return JsonResponse({
                'success': True,
                'valid': True,
                'message': 'No parts to validate',
                'valid_parts': [],
                'validation_errors': []
            })

        validation_errors = []
        valid_parts = []

        try:
            workshop_uuid = UUID(workshop_id) if isinstance(workshop_id, str) else workshop_id
            workshop = Workshop.objects.get(id=workshop_uuid)
        except (ValueError, Workshop.DoesNotExist) as e:
            return JsonResponse({
                'success': False,
                'error': f'Invalid workshop ID or workshop not found: {str(e)}'
            }, status=400)

        for part_entry in parts_data:
            part_id = part_entry.get('part_id')
            requested_quantity = part_entry.get('quantity', 0)

            if not part_id or part_id == 'none':
                continue

            try:
                if workshop.category == 'calibration_center':
                    part = Accessories.objects.select_related('name').get(
                        id=UUID(part_id),
                        active_status=True
                    )
                else:
                    part = Accessories.objects.select_related('name').get(
                        id=UUID(part_id),
                        workshop_id=workshop_uuid,
                        active_status=True
                    )

                part_name = part.name.name if part.name else f'Part {part.id}'

                if part.stock_count < requested_quantity:
                    validation_errors.append({
                        'part_id': part_id,
                        'part_name': part_name,
                        'requested': requested_quantity,
                        'available': part.stock_count,
                        'error': f"Insufficient stock for {part_name}. Requested: {requested_quantity}, Available: {part.stock_count}"
                    })
                else:
                    valid_parts.append({
                        'part_id': part_id,
                        'part_name': part_name,
                        'requested': requested_quantity,
                        'available': part.stock_count
                    })

            except Accessories.DoesNotExist:
                validation_errors.append({
                    'part_id': part_id,
                    'error': f"Part with ID {part_id} not found in workshop"
                })
            except ValueError:
                validation_errors.append({
                    'part_id': part_id,
                    'error': f"Invalid part ID format: {part_id}"
                })
            except Exception as e:
                logger.error(f"Unexpected error validating part {part_id}: {str(e)}")
                validation_errors.append({
                    'part_id': part_id,
                    'error': f"Server error validating part: {str(e)}"
                })

        is_valid = len(validation_errors) == 0

        response_data = {
            'success': True,
            'valid': is_valid,
            'valid_parts': valid_parts,
            'validation_errors': validation_errors,
            'total_parts_checked': len(parts_data),
            'message': 'Stock validation completed' if is_valid else f'{len(validation_errors)} validation errors found'
        }

        logger.info(f"Stock validation result: {response_data['message']}")
        return JsonResponse(response_data)

    except json.JSONDecodeError:
        logger.error("Invalid JSON data in stock validation request")
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error validating stock for job card: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Stock validation failed: {str(e)}'
        }, status=500)


def get_accessory_details(request):
    """
    Additional helper endpoint to get detailed information about a specific accessory.
    Useful for dynamic form updates.
    """
    accessory_id = request.GET.get('accessory_id')

    if not accessory_id:
        return JsonResponse({'error': 'Accessory ID is required'}, status=400)

    try:
        accessory_uuid = UUID(accessory_id)
        accessory = Accessories.objects.select_related(
            'name',
            'manufacturer',
            'equipment_description'
        ).get(id=accessory_uuid)

        data = {
            'id': str(accessory.id),
            'name': accessory.name.name if accessory.name else 'Unnamed',
            'manufacturer': accessory.manufacturer.name if accessory.manufacturer else 'Unknown',
            'equipment_description': accessory.equipment_description.name if accessory.equipment_description else 'No Description',
            'stock_count': accessory.stock_count,
            'note': accessory.note or '',
            'available': accessory.stock_count > 0,
            'workshop_id': str(accessory.workshop.id) if accessory.workshop else None,
            'workshop_name': accessory.workshop.name if accessory.workshop else 'No Workshop'
        }

        return JsonResponse({
            'success': True,
            'data': data
        })

    except ValueError:
        return JsonResponse({'error': 'Invalid Accessory ID format'}, status=400)
    except Accessories.DoesNotExist:
        return JsonResponse({'error': 'Accessory not found'}, status=404)
    except Exception as e:
        logger.error(f"Error getting accessory details for ID {accessory_id}: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Failed to get accessory details'}, status=500)


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


@login_required
def waiting_jobcards(request):
    profile, department, workshop, role = get_user_context(request)
    if not profile or role not in ['NIC', 'Tech']:
        messages.error(request, "Access denied or profile incomplete.", extra_tags="jobcard")
        return redirect('jobcard:create_job_card')

    search_query = request.GET.get('search', '').strip()

    if role == 'NIC':
        if not department:
            messages.error(request, "Your nurse profile is missing an associated department.")
            job_cards = jobcard.objects.none()
        else:
            job_cards = jobcard.objects.filter(
                status="Waiting Approval",
                department=department
            ).select_related('department', 'equipment', 'performed_by', 'workshop').order_by('-date_issued')

    elif role == 'Tech':
        if not workshop:
            messages.error(request, "Your technician profile is missing an associated workshop.")
            job_cards = jobcard.objects.none()
        else:
            job_cards = jobcard.objects.filter(
                status="Waiting Approval",
                workshop=workshop
            ).select_related('department', 'equipment', 'performed_by', 'workshop').order_by('-date_issued')

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
        messages.error(request, "You don't have permission to view approved job cards.")
        return redirect('jobcard:create_job_card')

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

    jobcards = jobcards.select_related('department', 'equipment', 'performed_by', 'verified_by_nurse', 'workshop')

    paginator = Paginator(jobcards, 50)
    page = request.GET.get("page")
    jobcards_page = paginator.get_page(page)

    default_start, default_end = get_last_week_range()

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
        'default_start': default_start,
        'default_end': default_end,
    })


@login_required
def declined_jobcards(request):
    profile, department, workshop, role = get_user_context(request)
    if not profile or role not in ['NIC', 'Tech', 'HOD']:
        messages.error(request, "Access denied or profile incomplete.")
        return redirect('jobcard:create_job_card')

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

    job_cards = job_cards.select_related('department', 'equipment', 'performed_by', 'workshop').order_by('-date_issued')

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


def get_last_week_range():
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_friday = last_monday + timedelta(days=4)
    return last_monday, last_friday


@login_required
def hod_workshop_jobcards(request, workshop_id):
    profile, _, _, role = get_user_context(request)
    if not profile or role != 'HOD':
        messages.error(request, "Access denied. Only HODs can view workshop job cards.")
        return redirect('jobcard:create_job_card')

    workshop = get_object_or_404(Workshop, id=workshop_id)
    status_filter = request.GET.get('status_filter', 'Waiting Approval')
    search_query = request.GET.get('search', '').strip()

    if workshop.category == 'calibration_center':
        job_cards_queryset = jobcard.objects.filter(
            workshop=workshop,
            status=status_filter
        )
        context_type = 'calibration_center'
        context_description = f"Job cards performed by {workshop.name} (calibration center)"

    else:
        job_cards_queryset = jobcard.objects.filter(
            department__workshop=workshop,
            status=status_filter
        ).exclude(
            workshop__category='calibration_center'
        )
        context_type = 'regular_workshop'
        context_description = f"Job cards for equipment in {workshop.name}'s departments (excluding calibration center work)"

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
def hod_calibration_work_on_equipment(request, workshop_id):
    """
    Separate view for regular workshop HODs to see calibration center work
    performed on their department's equipment
    """
    profile, _, _, role = get_user_context(request)
    if not profile or role != 'HOD':
        messages.error(request, "Access denied. Only HODs can view this page.")
        return redirect('jobcard:create_job_card')

    workshop = get_object_or_404(Workshop, id=workshop_id)

    if workshop.category == 'calibration_center':
        messages.error(request, "Calibration centers should use the main job cards view.")
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


def load_equipment(request):
    """
    AJAX endpoint to load equipment based on department.
    Returns only active equipment.
    """
    department_id = request.GET.get('department_id')

    if not department_id:
        logger.warning("load_equipment called without department_id.")
        return JsonResponse({'error': 'department ID is required'}, status=400)

    try:
        try:
            department_uuid = UUID(department_id)
        except ValueError:
            logger.error(f"Invalid UUID format for department_id: {department_id}")
            return JsonResponse({'error': 'Invalid department ID format'}, status=400)

        equipment_qs = Equipment.objects.filter(
            department_id=department_uuid,
            active_status=True
        ).select_related('description', 'manufacturer')

        data = [
            {
                'id': str(eq.id),
                'description': str(eq.description) if eq.description is not None else '',
                'serial_number': str(eq.serial_number) if eq.serial_number is not None else '',
                'model': str(eq.model) if eq.model is not None else '',
                'manufacturer': str(eq.manufacturer) if eq.manufacturer is not None else '',
                'status': str(eq.status) if eq.status is not None else '',
            }
            for eq in equipment_qs
        ]

        logger.info(f"Successfully loaded {len(data)} active equipment for department {department_uuid}")
        return JsonResponse(data, safe=False)

    except Exception as e:
        logger.error(f"Error loading equipment for department_id {department_id}: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Failed to load equipment'}, status=500)


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
            return redirect("jobcard:hod_workshop_jobcards", workshop_id=request.GET.get('workshop_id', 1))
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
            return redirect("jobcard:hod_workshop_jobcards", workshop_id=request.GET.get('workshop_id', 1))
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
            return redirect("jobcard:hod_workshop_jobcards", workshop_id=request.GET.get('workshop_id', 1))
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
            return redirect("jobcard:hod_workshop_jobcards", workshop_id=request.GET.get('workshop_id', 1))
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
            return redirect("jobcard:hod_workshop_jobcards", workshop_id=request.GET.get('workshop_id', 1))
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
            return redirect("jobcard:hod_workshop_jobcards", workshop_id=request.GET.get('workshop_id', 1))
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
