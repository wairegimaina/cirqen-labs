"""jobcard.views — shared context, signature, docx, filtering and stock helpers."""
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
from users.control import get_user_role
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


def get_user_context(request):
    try:
        profile = UserProfile.objects.select_related('workshop', 'department').get(user=request.user)
        role = get_user_role(request.user)
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
        user_signature, _ = UserSignature.objects.get_or_create(
            user=user,
            defaults={'is_active': True}
        )

        if not user_signature.has_signature():
            user_signature.generate_signature_image()
            user_signature.save()

        return user_signature.get_signature_as_base64()
    except Exception as e:
        logger.error(f"Error getting/creating user signature for {user.username}: {str(e)}")
        return None


def signature_to_image(signature_data, doc_template):
    """
    Convert signature data (base64 or UserSignature) to InlineImage for docxtpl.
    Handles both legacy base64 data and new UserSignature references.
    """
    if not signature_data:
        return None
    if isinstance(signature_data, str) and signature_data.strip() in ("", "N/A"):
        return None

    try:
        if isinstance(signature_data, UserSignature):
            img_buffer = signature_data.get_signature_as_image_buffer()
            if img_buffer:
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


def get_last_week_range():
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_friday = last_monday + timedelta(days=4)
    return last_monday, last_friday
