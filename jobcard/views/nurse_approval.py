"""jobcard.views — nurse approval handler for job-card creation."""
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
from .helpers import get_or_create_user_signature
from .listing import waiting_jobcards


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
