"""jobcard.views — technician job-card handler for job-card creation."""
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
            # The device must belong to the selected department, otherwise a job
            # card could be raised against another workshop's equipment.
            equipment = Equipment.objects.get(id=equipment_uuid, department=department, active_status=True)
        else:
            department = Department.objects.get(id=department_uuid, workshop=workshop, active_status=True)
            equipment = Equipment.objects.get(id=equipment_uuid, department=department, active_status=True)

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
