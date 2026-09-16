"""JSON API endpoints — power the JS fetch calls in accessories.js."""
import json
import logging

from django.shortcuts import get_object_or_404
from core.scoping import get_for_user_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db import transaction

from ..models import Tools, Accessories, AccessoryRequest, AccessoryRequestHistory
from Inventory.models import EquipmentDescription

logger = logging.getLogger(__name__)


@login_required
def api_accessories(request):
    """JSON API: list accessories scoped by role and optional workshop filter."""
    profile = request.user.userprofile
    workshop_id = request.GET.get('workshop')

    if profile.role == 'HOD':
        qs = Accessories.objects.filter(active_status=True).select_related(
            'name', 'manufacturer', 'equipment_description', 'workshop'
        )
        if workshop_id:
            qs = qs.filter(workshop_id=workshop_id)
    else:
        if not profile.workshop:
            return JsonResponse({'accessories': []})
        qs = Accessories.objects.filter(
            workshop=profile.workshop, active_status=True
        ).select_related('name', 'manufacturer', 'equipment_description', 'workshop')

    data = [
        {
            'id': str(a.id),
            'name': a.name.name if a.name else '',
            'equipment': a.equipment_description.name if a.equipment_description else '',
            'manufacturer': a.manufacturer.name if a.manufacturer else '',
            'stock_count': a.stock_count,
            'unit_cost': str(a.unit_cost),
            'note': a.note or '',
            'workshop': a.workshop.name if a.workshop else 'Global',
        }
        for a in qs.order_by('-created_at')
    ]
    return JsonResponse({'accessories': data})


@login_required
def api_accessory_detail(request, pk):
    """JSON API: single accessory — supports DELETE."""
    profile = request.user.userprofile

    if profile.role != 'HOD':
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    accessory = get_object_or_404(Accessories, pk=pk)

    if request.method == 'DELETE':
        try:
            accessory.pending_delete = True
            accessory.active_status = False
            accessory.updated_at = timezone.now()
            accessory.save(update_fields=['pending_delete', 'active_status', 'updated_at'])
            logger.info(f"Accessory {pk} marked for deletion via API by {request.user.username}")
            return JsonResponse({'success': True})
        except Exception as e:
            logger.error(f"API delete accessory {pk} error: {e}")
            return JsonResponse({'error': str(e)}, status=500)

    data = {
        'id': str(accessory.id),
        'name': str(accessory.name.id) if accessory.name else '',
        'name_display': accessory.name.name if accessory.name else '',
        'manufacturer': str(accessory.manufacturer.id) if accessory.manufacturer else '',
        'manufacturer_display': accessory.manufacturer.name if accessory.manufacturer else '',
        'equipment_description': str(accessory.equipment_description.id) if accessory.equipment_description else '',
        'equipment_display': accessory.equipment_description.name if accessory.equipment_description else '',
        'stock_count': accessory.stock_count,
        'unit_cost': str(accessory.unit_cost),
        'note': accessory.note or '',
    }
    return JsonResponse(data)


@login_required
def api_tools(request):
    """JSON API: list tools scoped by role and optional workshop filter."""
    profile = request.user.userprofile
    workshop_id = request.GET.get('workshop')

    if profile.role == 'HOD':
        qs = Tools.objects.filter(active_status=True).select_related('name', 'manufacturer', 'workshop')
        if workshop_id:
            qs = qs.filter(workshop_id=workshop_id)
    else:
        if not profile.workshop:
            return JsonResponse({'tools': []})
        qs = Tools.objects.filter(
            workshop=profile.workshop, active_status=True
        ).select_related('name', 'manufacturer', 'workshop')

    data = [
        {
            'id': str(t.id),
            'name': t.name.name if t.name else '',
            'manufacturer': t.manufacturer.name if t.manufacturer else '',
            'model': t.model or '',
            'serial_number': t.serial_number or '',
            'workshop': t.workshop.name if t.workshop else 'Global',
        }
        for t in qs.order_by('-created_at')
    ]
    return JsonResponse({'tools': data})


@login_required
def api_tool_detail(request, pk):
    """JSON API: single tool — supports DELETE."""
    profile = request.user.userprofile
    tool = get_for_user_or_404(Tools, request.user, pk=pk)

    if request.method == 'DELETE':
        if profile.role != 'Tech':
            return JsonResponse({'error': 'Only Tech users can delete tools.'}, status=403)
        try:
            tool.pending_delete = True
            tool.active_status = False
            tool.updated_at = timezone.now()
            tool.save(update_fields=['pending_delete', 'active_status', 'updated_at'])
            logger.info(f"Tool {pk} marked for deletion via API by {request.user.username}")
            return JsonResponse({'success': True})
        except Exception as e:
            logger.error(f"API delete tool {pk} error: {e}")
            return JsonResponse({'error': str(e)}, status=500)

    data = {
        'id': str(tool.id),
        'name': str(tool.name.id) if tool.name else '',
        'name_display': tool.name.name if tool.name else '',
        'manufacturer': str(tool.manufacturer.id) if tool.manufacturer else '',
        'manufacturer_display': tool.manufacturer.name if tool.manufacturer else '',
        'model': tool.model or '',
        'serial_number': tool.serial_number or '',
    }
    return JsonResponse(data)


@login_required
@require_POST
def api_accessory_requests(request):
    """JSON API: submit a new accessory request (POST)."""
    profile = request.user.userprofile

    if not profile.workshop:
        return JsonResponse({'error': 'You must be assigned to a workshop to make requests.'}, status=400)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, Exception):
        body = request.POST

    request_type = body.get('request_type', 'new')
    equipment_id = body.get('equipment_description')
    requested_quantity = body.get('requested_quantity', 1)
    note = body.get('note', '').strip()

    if not equipment_id:
        return JsonResponse({'error': 'Equipment description is required.'}, status=400)

    try:
        equipment = get_object_or_404(EquipmentDescription, id=equipment_id)
    except Exception:
        return JsonResponse({'error': 'Invalid equipment selected.'}, status=400)

    try:
        with transaction.atomic():
            if request_type == 'new':
                accessory_name = body.get('accessory_name', '').strip()
                manufacturer_name = body.get('manufacturer_name', '').strip()

                if not accessory_name:
                    return JsonResponse({'error': 'Accessory name is required.'}, status=400)

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

            elif request_type == 'restock':
                existing_accessory_id = body.get('existing_accessory')
                if not existing_accessory_id:
                    return JsonResponse({'error': 'Please select an accessory to restock.'}, status=400)

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
                    notes=f"Restock request created for {existing_accessory.name.name if existing_accessory.name else 'accessory'}",
                    new_status='Pending',
                )
            else:
                return JsonResponse({'error': 'Invalid request type.'}, status=400)

        logger.info(f"Accessory request {accessory_request.id} created via API by {request.user.username}")
        return JsonResponse({'success': True, 'id': str(accessory_request.id)})

    except Exception as e:
        logger.error(f"API create accessory request error: {e}")
        return JsonResponse({'error': str(e)}, status=500)
