"""jobcard.views — equipment/accessory lookups, stock checks, signature data."""
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


@login_required
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
