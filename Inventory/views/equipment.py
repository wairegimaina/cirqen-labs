"""Equipment lifecycle views: create, edit, delete, transfer, reactivate.

Also holds the two equipment-scoped API endpoints (dependency info and PPM
location check) so all equipment-dependency usage lives in one module.
Behaviour is unchanged from the original ``views.py``.
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from core.scoping import for_user, get_for_user_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from Inventory.models import Department, Equipment, EquipmentDescription, Manufacturer
from audit_log.models import AuditLog
from Inventory.equipment_dependencies import (
    get_equipment_dependencies_count,
    transfer_equipment_dependencies,
    validate_equipment_transfer,
    verify_dependency_transfer,
    check_ppm_location,
)

logger = logging.getLogger(__name__)


@login_required
@require_http_methods(["GET"])
def check_equipment_ppm_locations(request, equipment_id):
    """
    API endpoint to check PPM locations for equipment
    Useful for debugging transfer issues
    """
    try:
        equipment = get_for_user_or_404(Equipment, request.user, pk=equipment_id)

        # Call the dedicated PPM check function
        ppm_details = check_ppm_location(equipment)

        # Get equipment's current location
        equipment_info = {
            'equipment_id': str(equipment.id),
            'serial_number': equipment.serial_number,
            'workshop': equipment.department.workshop.name if equipment.department and equipment.department.workshop else 'None',
            'workshop_id': str(equipment.department.workshop.id) if equipment.department and equipment.department.workshop else None,
            'department': equipment.department.name if equipment.department else 'None',
            'department_id': str(equipment.department.id) if equipment.department else None
        }

        # Check if all PPMs match equipment location
        all_match = all(detail['matches_equipment_location'] for detail in ppm_details)
        mismatches = [d for d in ppm_details if not d['matches_equipment_location']]

        return JsonResponse({
            'success': True,
            'equipment': equipment_info,
            'ppms': ppm_details,
            'total_ppms': len(ppm_details),
            'all_match': all_match,
            'mismatches': len(mismatches),
            'mismatch_details': mismatches
        })

    except Http404:
        raise
    except Exception as e:
        logger.error(f"Error checking PPM locations: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def create_equipment_description(request):
    response_data = {
        'success': False,
        'error': None,
        'description_id': None,
        'description_name': None
    }

    try:
        # Debug: Log incoming request
        logger.debug(f"Incoming request from {request.user.username}: {request.POST}")

        # Validate input
        name = request.POST.get('description_name', '').strip()
        if not name:
            response_data['error'] = 'Equipment description name is required.'
            return JsonResponse(response_data, status=400)

        # Check permissions
        if not hasattr(request.user, 'userprofile'):
            response_data['error'] = 'User profile not found.'
            return JsonResponse(response_data, status=403)

        if request.user.userprofile.role != 'Tech':
            response_data['error'] = 'Only Technologists can add equipment descriptions.'
            return JsonResponse(response_data, status=403)

        # Check for duplicates (case-insensitive)
        if EquipmentDescription.objects.filter(name__iexact=name).exists():
            response_data['error'] = f'Equipment description "{name}" already exists.'
            return JsonResponse(response_data, status=409)

        # Create new description
        new_description = EquipmentDescription.objects.create(name=name)

        response_data.update({
            'success': True,
            'description_id': new_description.id,
            'description_name': new_description.name
        })

        logger.info(f"Created new equipment description: {new_description.name} by {request.user.username}")
        return JsonResponse(response_data)

    except Exception as e:
        logger.error(f"Error creating equipment description: {str(e)}",
                     exc_info=True,
                     extra={'user': request.user.username, 'data': request.POST})
        response_data['error'] = 'An internal error occurred while creating the equipment description.'
        return JsonResponse(response_data, status=500)


@login_required
@require_http_methods(["POST"])
def create_manufacturer(request):
    response_data = {
        'success': False,
        'error': None,
        'manufacturer_id': None,
        'manufacturer_name': None
    }

    try:
        name = request.POST.get('manufacturer_name', '').strip()
        if not name:
            response_data['error'] = 'Manufacturer name is required.'
            return JsonResponse(response_data, status=400)

        # Check duplicates (case-insensitive)
        existing = Manufacturer.objects.filter(name__iexact=name).first()
        if existing:
            response_data['error'] = f"Manufacturer '{existing.name}' already exists."
            return JsonResponse(response_data, status=409)

        # Normalize & save
        new_manufacturer = Manufacturer.objects.create(name=name.title())

        response_data.update({
            'success': True,
            'manufacturer_id': new_manufacturer.id,
            'manufacturer_name': new_manufacturer.name
        })
        return JsonResponse(response_data)

    except Exception as e:
        response_data['error'] = f"Internal error: {str(e)}"
        return JsonResponse(response_data, status=500)


@login_required
def add_inventory(request):
    if request.method == "POST":
        description_id = request.POST.get("description")
        manufacturer_id = request.POST.get("manufacturer")
        model = request.POST.get("model")
        serial_number = request.POST.get("serial_number")
        department_id = request.POST.get("department")
        status = request.POST.get("status")

        try:
            description = EquipmentDescription.objects.get(id=description_id)
        except EquipmentDescription.DoesNotExist:
            messages.error(request, "Invalid equipment description selected.")
            return redirect("inventory")

        try:
            department = for_user(Department.objects.all(), request.user).get(id=department_id)
        except Department.DoesNotExist:
            messages.error(request, "Invalid department selected.")
            return redirect("inventory")

        manufacturer = None
        if manufacturer_id:
            try:
                manufacturer = Manufacturer.objects.get(id=manufacturer_id)
            except Manufacturer.DoesNotExist:
                messages.error(request, "Invalid manufacturer selected.")
                return redirect("inventory")

        # Check if equipment with same serial number exists (active or inactive)
        existing_equipment = None
        if serial_number:
            existing_equipment = Equipment.objects.filter(
                serial_number=serial_number
            ).first()

        # If equipment exists and is deactivated, offer to reactivate
        if existing_equipment and (not existing_equipment.active_status or existing_equipment.pending_delete):
            try:
                with transaction.atomic():
                    # Reactivate the equipment
                    existing_equipment.description = description
                    existing_equipment.manufacturer = manufacturer
                    existing_equipment.model = model
                    existing_equipment.serial_number = serial_number
                    existing_equipment.department = department
                    existing_equipment.status = status
                    existing_equipment.active_status = True  # Reactivate
                    existing_equipment.pending_delete = False  # Remove deletion flag
                    existing_equipment.updated_at = timezone.now()
                    existing_equipment.save()

                    messages.success(
                        request,
                        f'Equipment with serial number "{serial_number}" has been reactivated and moved to {department.name} in {department.workshop.name}.',
                        extra_tags="success create task"
                    )
                    logger.info(f"Equipment {serial_number} reactivated by {request.user.username} - moved to {department.name}")
            except Exception as e:
                messages.error(request, f"Error reactivating equipment: {e}")
                logger.error(f"Failed to reactivate equipment {serial_number}: {str(e)}")

            return redirect("inventory")

        # Check if active equipment with same serial already exists
        if existing_equipment and existing_equipment.active_status:
            messages.error(
                request,
                f'Equipment with serial number "{serial_number}" already exists in {existing_equipment.department.name}.'
            )
            return redirect("inventory")

        # Create new equipment
        try:
            equipment = Equipment(
                description=description,
                manufacturer=manufacturer,
                model=model,
                serial_number=serial_number,
                department=department,
                status=status,
                active_status=True,  # Ensure new equipment is active
                pending_delete=False
            )
            equipment.save()
            messages.success(
                request,
                "Equipment added successfully.",
                extra_tags="success create task"
            )
            logger.info(f"New equipment created by {request.user.username}: {serial_number} in {department.name}")
        except Exception as e:
            messages.error(request, f"Error adding equipment: {e}")
            logger.error(f"Failed to create equipment: {str(e)}")

    # The add form is a modal on the inventory page; a plain GET has nothing
    # of its own to show.
    return redirect("inventory")


@login_required
@require_http_methods(["GET"])
def get_equipment_dependency_info(request, equipment_id):
    """
    API endpoint to get dependency information for equipment
    Useful for showing users what will be transferred
    """
    try:
        equipment = get_for_user_or_404(Equipment, request.user, pk=equipment_id)
        dep_count, dep_breakdown = get_equipment_dependencies_count(equipment)

        # Format breakdown for display
        formatted_breakdown = []
        for model_key, info in dep_breakdown.items():
            formatted_breakdown.append({
                'model': model_key,
                'count': info['count'],
                'field': info['field'],
                'type': info['on_delete']
            })

        return JsonResponse({
            'success': True,
            'equipment_id': str(equipment.id),
            'serial_number': equipment.serial_number,
            'total_dependencies': dep_count,
            'breakdown': formatted_breakdown,
            'has_dependencies': dep_count > 0
        })

    except Exception as e:
        logger.error(f"Error getting dependency info: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def edit_inventory(request, equipment_id):
    # Scoped: a user can only edit devices they can see, and only move them
    # into departments they can see.
    equipment = get_for_user_or_404(Equipment, request.user, id=equipment_id)

    if request.method == "POST":
        description_id = request.POST.get("description")
        manufacturer_id = request.POST.get("manufacturer")
        model = request.POST.get("model")
        serial_number = request.POST.get("serial_number")
        department_id = request.POST.get("department")
        status = request.POST.get("status")

        try:
            description = EquipmentDescription.objects.get(id=description_id)
        except EquipmentDescription.DoesNotExist:
            messages.error(request, "Invalid equipment description selected.")
            return redirect("inventory")

        try:
            department = for_user(Department.objects.all(), request.user).get(id=department_id)
        except Department.DoesNotExist:
            messages.error(request, "Invalid department selected.")
            return redirect("inventory")

        manufacturer = None
        if manufacturer_id:
            try:
                manufacturer = Manufacturer.objects.get(id=manufacturer_id)
            except Manufacturer.DoesNotExist:
                messages.error(request, "Invalid manufacturer selected.")
                return redirect("inventory")

        try:
            equipment.description = description
            equipment.manufacturer = manufacturer   # ✅ correct instance now
            equipment.model = model
            equipment.serial_number = serial_number
            equipment.department = department
            equipment.status = status
            equipment.save()

            messages.success(request, "Equipment updated successfully.", extra_tags="success  task created")
        except Exception as e:
            messages.error(request, f"Error updating equipment: {e}")

        return redirect("inventory")

    # Editing happens in the modal on the inventory page, which posts here.
    return redirect("inventory")


@login_required
@require_POST
def delete_equipment(request, pk):
    equipment = get_object_or_404(Equipment, pk=pk)
    profile = request.user.userprofile
    equipment_display_name = f"{equipment.description.name} ({equipment.serial_number})"

    logger.info(f"Delete equipment attempt - User: {request.user}, Equipment: {equipment_display_name} (ID: {pk}), Profile Role: {profile.role}")

    if profile.role != 'Tech' or equipment.department.workshop != profile.workshop:
        logger.warning(f"Unauthorized delete attempt - User: {request.user}, Equipment: {equipment_display_name}, User Workshop: {getattr(profile.workshop, 'name', 'None')}, Equipment Workshop: {equipment.department.workshop.name}")
        messages.error(request, "Unauthorized to delete this equipment.")
        return redirect('inventory')

    if request.method == 'POST':
        logger.info(f"Marking equipment for deletion - Equipment: {equipment_display_name} (ID: {pk}), Current Status: pending_delete={equipment.pending_delete}, active_status={equipment.active_status}")

        try:
            # Mark as pending_delete AND set active_status to False
            equipment.pending_delete = True
            equipment.active_status = False  # ← ADD THIS LINE
            equipment.updated_at = timezone.now()
            equipment.save(update_fields=['pending_delete', 'active_status', 'updated_at'])  # ← UPDATE THIS

            logger.info(f"Successfully marked equipment for deletion - Equipment: {equipment_display_name} (ID: {pk}), New Status: pending_delete={equipment.pending_delete}, active_status={equipment.active_status}")
            messages.success(request, f'Equipment "{equipment_display_name}" marked for deletion and will sync to HQ.', extra_tags="success delete task")
            return redirect('inventory')

        except Exception as e:
            logger.error(f"Failed to mark equipment for deletion - Equipment: {equipment_display_name} (ID: {pk}), Error: {str(e)}")
            messages.error(request, f"Failed to mark equipment for deletion: {str(e)}")
            return redirect('inventory')

    logger.info(f"Non-POST request to delete equipment - User: {request.user}, Equipment: {equipment_display_name}")
    return redirect('inventory')
