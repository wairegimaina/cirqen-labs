"""Accessory request workflow — submit, approve/decline, accept, history."""
import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST
from django.db import transaction

from ..models import (
    Accessories, AccessoryRequest, AccessoryRequestHistory,
    AccessoriesManufacturer, Accessoriesname,
)
from Inventory.models import EquipmentDescription

logger = logging.getLogger(__name__)


@login_required
def request_accessory(request):
    """
    Submit a request for a new accessory or restock.
    Unit cost is not set here — HOD sets it during approval.
    """
    if request.method == 'GET':
        return redirect('partstools:accessories_dashboard')

    try:
        profile = request.user.userprofile
    except AttributeError:
        messages.error(request, "You do not have a user profile assigned.")
        return redirect('partstools:accessories_dashboard')

    if not profile.workshop:
        messages.error(request, "You must be assigned to a workshop to request accessories.")
        return redirect('partstools:accessories_dashboard')

    if request.method != 'POST':
        return redirect('partstools:accessories_dashboard')

    try:
        request_type = request.POST.get('request_type', 'new')
        equipment_id = request.POST.get('equipment_description')
        requested_quantity = request.POST.get('requested_quantity', 1)
        note = request.POST.get('note', '').strip()

        if not equipment_id:
            messages.error(request, 'Equipment description is required.')
            return redirect('partstools:accessories_dashboard')

        equipment = get_object_or_404(EquipmentDescription, id=equipment_id)

        with transaction.atomic():
            if request_type == 'new':
                accessory_name = request.POST.get('accessory_name', '').strip()
                manufacturer_name = request.POST.get('manufacturer_name', '').strip()

                if not accessory_name:
                    messages.error(request, 'Accessory name is required for new accessory requests.')
                    return redirect('partstools:accessories_dashboard')

                accessory_request = AccessoryRequest.objects.create(
                    request_type='new',
                    accessory_name=accessory_name,
                    manufacturer_name=manufacturer_name,
                    equipment_description=equipment,
                    requested_quantity=int(requested_quantity),
                    unit_cost=0.00,
                    note=note,
                    requested_by=profile,
                    workshop=profile.workshop,
                    status='Pending',
                )
                AccessoryRequestHistory.objects.create(
                    request=accessory_request,
                    action='created',
                    performed_by=profile,
                    notes=f"Request created for new accessory: {accessory_name}",
                    new_status='Pending',
                )
                logger.info(f"New accessory request '{accessory_name}' created by {request.user.username}")
                messages.success(request, f'Request for "{accessory_name}" submitted. HOD will set final quantity and cost.')

            elif request_type == 'restock':
                existing_accessory_id = request.POST.get('existing_accessory')
                if not existing_accessory_id:
                    messages.error(request, 'Please select an accessory to restock.')
                    return redirect('partstools:accessories_dashboard')

                existing_accessory = get_object_or_404(Accessories, id=existing_accessory_id)
                accessory_request = AccessoryRequest.objects.create(
                    request_type='restock',
                    existing_accessory=existing_accessory,
                    equipment_description=equipment,
                    requested_quantity=int(requested_quantity),
                    unit_cost=0.00,
                    note=note,
                    requested_by=profile,
                    workshop=profile.workshop,
                    status='Pending',
                )
                AccessoryRequestHistory.objects.create(
                    request=accessory_request,
                    action='created',
                    performed_by=profile,
                    notes=f"Restock request for {existing_accessory.name.name if existing_accessory.name else 'accessory'}",
                    new_status='Pending',
                )
                logger.info(f"Restock request for '{existing_accessory}' created by {request.user.username}")
                name_display = existing_accessory.name.name if existing_accessory.name else 'accessory'
                messages.success(request, f'Restock request for "{name_display}" submitted. HOD will set final quantity and cost.')

            else:
                messages.error(request, 'Invalid request type.')

    except ValidationError as e:
        logger.error(f"Validation error requesting accessory: {e}")
        messages.error(request, f'Validation error: {e}')
    except Exception as e:
        logger.error(f"Error requesting accessory: {e}")
        messages.error(request, f'Error: {str(e)}')

    return redirect('partstools:accessories_dashboard')


@login_required
@require_POST
def approve_accessory_request(request, request_id):
    """HOD approves or declines an accessory request, setting quantity and unit cost."""
    profile = request.user.userprofile

    if profile.role != 'HOD':
        logger.warning(f"Unauthorized approval attempt by {request.user.username} (role={profile.role})")
        messages.error(request, "Only HOD can approve or decline requests.")
        return redirect('partstools:accessories_dashboard')

    accessory_request = get_object_or_404(AccessoryRequest, id=request_id)

    if not accessory_request.can_be_approved:
        messages.error(request, "This request cannot be approved at this stage.")
        return redirect('partstools:accessories_dashboard')

    try:
        action = request.POST.get('action')
        approval_reason = request.POST.get('approval_reason', '').strip()

        if action not in ['approve', 'decline']:
            messages.error(request, f"Invalid action: {action}")
            return redirect('partstools:accessories_dashboard')

        with transaction.atomic():
            if action == 'approve':
                approved_quantity_str = request.POST.get('approved_quantity', '').strip()
                approved_unit_cost_str = request.POST.get('approved_unit_cost', '').strip()

                if not approved_quantity_str or not approved_unit_cost_str:
                    messages.error(request, "Please provide both approved quantity and unit cost.")
                    return redirect('partstools:accessories_dashboard')

                try:
                    approved_quantity = int(approved_quantity_str)
                    if approved_quantity <= 0:
                        messages.error(request, "Approved quantity must be greater than 0.")
                        return redirect('partstools:accessories_dashboard')
                except ValueError:
                    messages.error(request, "Invalid quantity value.")
                    return redirect('partstools:accessories_dashboard')

                try:
                    approved_unit_cost = float(approved_unit_cost_str)
                    if approved_unit_cost < 0:
                        messages.error(request, "Unit cost cannot be negative.")
                        return redirect('partstools:accessories_dashboard')
                except ValueError:
                    messages.error(request, "Invalid unit cost value.")
                    return redirect('partstools:accessories_dashboard')

                accessory_request.requested_quantity = approved_quantity
                accessory_request.unit_cost = approved_unit_cost
                accessory_request.status = 'Approved'
                accessory_request.approved_by = profile
                accessory_request.approved_at = timezone.now()
                accessory_request.approval_reason = approval_reason
                accessory_request.save()

                history_note = f"{approval_reason}\nApproved Quantity: {approved_quantity} units\nApproved Unit Cost: KSh {approved_unit_cost}"
                AccessoryRequestHistory.objects.create(
                    request=accessory_request,
                    action='approved',
                    performed_by=profile,
                    notes=history_note,
                    previous_status='Pending',
                    new_status='Approved',
                )

                total = approved_quantity * approved_unit_cost
                logger.info(f"Request {request_id} APPROVED by {request.user.username} — Qty: {approved_quantity}, Cost: {approved_unit_cost}")
                messages.success(request, f'Request approved: {approved_quantity} units at KSh {approved_unit_cost} each (Total: KSh {total}).')

            elif action == 'decline':
                accessory_request.status = 'Declined'
                accessory_request.approved_by = profile
                accessory_request.approved_at = timezone.now()
                accessory_request.approval_reason = approval_reason
                accessory_request.save()

                AccessoryRequestHistory.objects.create(
                    request=accessory_request,
                    action='declined',
                    performed_by=profile,
                    notes=approval_reason,
                    previous_status='Pending',
                    new_status='Declined',
                )
                logger.info(f"Request {request_id} DECLINED by {request.user.username}")
                messages.warning(request, 'Request has been declined.')

    except Exception as e:
        logger.error(f"Error approving/declining request {request_id}: {e}")
        messages.error(request, f'Error processing request: {str(e)}')

    return redirect('partstools:accessories_dashboard')


@login_required
@require_POST
def accept_accessory_request(request, request_id):
    """
    Workshop user accepts an approved accessory request and creates/restocks the accessory.
    Quantity and cost come from HOD approval — not editable here.
    """
    profile = request.user.userprofile

    if not profile.workshop:
        messages.error(request, "You must be assigned to a workshop.")
        return redirect('partstools:accessories_dashboard')

    accessory_request = get_object_or_404(AccessoryRequest, id=request_id)

    if accessory_request.workshop != profile.workshop:
        messages.error(request, "You can only accept requests for your own workshop.")
        return redirect('partstools:accessories_dashboard')

    if not accessory_request.can_be_accepted:
        messages.error(request, "This request cannot be accepted at this stage.")
        return redirect('partstools:accessories_dashboard')

    try:
        approved_quantity = accessory_request.requested_quantity
        approved_unit_cost = accessory_request.unit_cost

        with transaction.atomic():
            if accessory_request.request_type == 'new':
                accessory_name_obj, _ = Accessoriesname.objects.get_or_create(
                    name__iexact=accessory_request.accessory_name,
                    defaults={'name': accessory_request.accessory_name}
                )
                manufacturer_obj = None
                if accessory_request.manufacturer_name:
                    manufacturer_obj, _ = AccessoriesManufacturer.objects.get_or_create(
                        name__iexact=accessory_request.manufacturer_name,
                        defaults={'name': accessory_request.manufacturer_name}
                    )

                new_accessory = Accessories.objects.create(
                    name=accessory_name_obj,
                    manufacturer=manufacturer_obj,
                    equipment_description=accessory_request.equipment_description,
                    stock_count=approved_quantity,
                    unit_cost=approved_unit_cost,
                    note=accessory_request.note,
                    workshop=accessory_request.workshop,
                )
                accessory_request.created_accessory = new_accessory
                logger.info(f"New accessory '{new_accessory}' created from request {request_id}")
                success_message = (
                    f'Accessory "{new_accessory.name.name}" created: '
                    f'{approved_quantity} units at KSh {approved_unit_cost} each '
                    f'(Total: KSh {approved_quantity * approved_unit_cost}).'
                )

            elif accessory_request.request_type == 'restock':
                existing_accessory = accessory_request.existing_accessory
                existing_accessory.stock_count += approved_quantity
                existing_accessory.unit_cost = approved_unit_cost
                existing_accessory.save()
                accessory_request.created_accessory = existing_accessory
                logger.info(f"Accessory '{existing_accessory}' restocked +{approved_quantity} units from request {request_id}")
                success_message = (
                    f'"{existing_accessory.name.name}" restocked with {approved_quantity} units '
                    f'at KSh {approved_unit_cost} each. New stock: {existing_accessory.stock_count}.'
                )

            accessory_request.status = 'Accepted'
            accessory_request.accepted_by = profile
            accessory_request.accepted_at = timezone.now()
            accessory_request.acceptance_note = f"Accepted by {profile.user.get_full_name() or profile.user.username}"
            accessory_request.save()

            history_note = (
                f"Accepted by {profile.user.get_full_name() or profile.user.username}\n"
                f"Received: {approved_quantity} units at KSh {approved_unit_cost} (as approved by HOD)\n"
                f"Total Value: KSh {approved_quantity * approved_unit_cost}"
            )
            AccessoryRequestHistory.objects.create(
                request=accessory_request,
                action='accepted',
                performed_by=profile,
                notes=history_note,
                previous_status='Approved',
                new_status='Accepted',
            )

            messages.success(request, success_message)

    except Exception as e:
        logger.error(f"Error accepting request {request_id}: {e}")
        messages.error(request, f'Error accepting request: {str(e)}')

    return redirect('partstools:accessories_dashboard')


