"""Accessory direct management (HOD) — edit/delete, listing, AJAX name/manufacturer helpers."""
import logging

from django.shortcuts import render, redirect, get_object_or_404
from users.control import role_required
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST

from ..models import Accessories, AccessoriesManufacturer, Accessoriesname
from Inventory.models import EquipmentDescription

logger = logging.getLogger(__name__)


@login_required
@role_required('HOD', redirect_to='partstools:accessories_dashboard', message='Only HOD can directly edit accessories.')
def edit_accessory(request, pk):
    """Edit an existing accessory — HOD only."""
    profile = request.user.userprofile
    accessory = get_object_or_404(Accessories, pk=pk)


    if request.method != 'POST':
        return redirect('partstools:accessories_dashboard')

    try:
        name_id = request.POST.get('name')
        new_name = request.POST.get('new_name', '').strip()
        manufacturer_id = request.POST.get('manufacturer')
        new_manufacturer = request.POST.get('new_manufacturer', '').strip()
        equipment_id = request.POST.get('equipment_description')
        stock_count = request.POST.get('stock_count', 0)
        unit_cost = request.POST.get('unit_cost', 0)
        note = request.POST.get('note', '').strip()

        # Resolve accessory name
        if name_id:
            accessory_name = Accessoriesname.objects.filter(id=name_id).first()
            if accessory_name:
                accessory.name = accessory_name
        elif new_name:
            accessory_name, created = Accessoriesname.objects.get_or_create(
                name__iexact=new_name, defaults={'name': new_name.strip()}
            )
            accessory.name = accessory_name
            if created:
                logger.info(f"New accessory name '{new_name}' created by HOD {request.user.username}")

        # Resolve manufacturer
        if manufacturer_id:
            manufacturer = AccessoriesManufacturer.objects.filter(id=manufacturer_id).first()
            if manufacturer:
                accessory.manufacturer = manufacturer
        elif new_manufacturer:
            manufacturer, created = AccessoriesManufacturer.objects.get_or_create(
                name__iexact=new_manufacturer, defaults={'name': new_manufacturer.strip()}
            )
            accessory.manufacturer = manufacturer
            if created:
                logger.info(f"New manufacturer '{new_manufacturer}' created by HOD {request.user.username}")

        # Resolve equipment description
        if equipment_id:
            equipment = get_object_or_404(EquipmentDescription, id=equipment_id)
            accessory.equipment_description = equipment

        accessory.stock_count = int(stock_count)
        accessory.unit_cost = float(unit_cost)
        accessory.note = note
        accessory.save()

        name_display = accessory.name.name if accessory.name else 'Unnamed'
        logger.info(f"Accessory '{name_display}' updated by HOD {request.user.username}")
        messages.success(request, f'Accessory "{name_display}" has been updated successfully!')

    except ValidationError as e:
        logger.error(f"Validation error updating accessory: {e}")
        messages.error(request, f'Validation error: {e}')
    except Exception as e:
        logger.error(f"Error updating accessory: {e}")
        messages.error(request, f'Error updating accessory: {str(e)}')

    return redirect('partstools:accessories_dashboard')


@login_required
@role_required('HOD', json=True, message='Unauthorized')
def get_accessory(request, pk):
    """Return accessory data as JSON for the edit modal — HOD only."""
    profile = request.user.userprofile

    try:
        accessory = get_object_or_404(Accessories, pk=pk)
        return JsonResponse({
            'name': str(accessory.name.id) if accessory.name else '',
            'name_display': accessory.name.name if accessory.name else '',
            'manufacturer': str(accessory.manufacturer.id) if accessory.manufacturer else '',
            'manufacturer_display': accessory.manufacturer.name if accessory.manufacturer else '',
            'equipment_description': str(accessory.equipment_description.id) if accessory.equipment_description else '',
            'equipment_display': accessory.equipment_description.name if accessory.equipment_description else '',
            'stock_count': accessory.stock_count,
            'unit_cost': str(accessory.unit_cost),
            'note': accessory.note or '',
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@role_required('HOD', redirect_to='partstools:accessories_dashboard', message='Only HOD can directly delete accessories.')
def delete_accessory(request, pk):
    """Soft-delete an accessory — HOD only."""
    profile = request.user.userprofile
    accessory = get_object_or_404(Accessories, pk=pk)
    display = f"{accessory.name.name if accessory.name else 'Unnamed'} for {accessory.equipment_description.name if accessory.equipment_description else 'Unknown Equipment'}"


    try:
        accessory.pending_delete = True
        accessory.active_status = False
        accessory.updated_at = timezone.now()
        accessory.save(update_fields=['pending_delete', 'active_status', 'updated_at'])
        logger.info(f"Accessory '{display}' ({pk}) marked for deletion by HOD {request.user.username}")
        messages.success(request, f'Accessory "{display}" marked for deletion.')
    except Exception as e:
        logger.error(f"Error deleting accessory {pk}: {e}")
        messages.error(request, f"Failed to mark accessory for deletion: {str(e)}")

    return redirect('partstools:accessories_dashboard')


@login_required
def accessory_list(request):
    """List all accessories (with optional search)."""
    query = request.GET.get('q', '')
    profile = request.user.userprofile

    if profile.role == 'HOD':
        accessories = Accessories.objects.filter(active_status=True).order_by('-created_at')
    else:
        accessories = Accessories.objects.filter(workshop=profile.workshop, active_status=True).order_by('-created_at')

    if query:
        accessories = accessories.filter(Q(name__name__icontains=query))

    return render(request, 'Parts & tools/accessories.html', {
        'show_sidebar': True,
        'accessories': accessories,
        'query': query,
        'is_hod': profile.role == 'HOD',
    })


@login_required
@require_POST
def ajax_add_accessory_name(request):
    """AJAX: add a new accessory name."""
    try:
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'Accessory name cannot be empty.'}, status=400)

        if Accessoriesname.objects.filter(name__iexact=name).exists():
            return JsonResponse({'error': f'Accessory name "{name}" already exists.'}, status=400)

        accessory_name = Accessoriesname.objects.create(name=name.title())
        logger.info(f"New accessory name '{name}' created by {request.user.username}")
        return JsonResponse({'success': True, 'id': str(accessory_name.id), 'name': accessory_name.name})

    except Exception as e:
        logger.error(f"Error adding accessory name: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def ajax_add_manufacturer(request):
    """AJAX: add a new accessory manufacturer."""
    try:
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'Manufacturer name cannot be empty.'}, status=400)

        if AccessoriesManufacturer.objects.filter(name__iexact=name).exists():
            return JsonResponse({'error': f'Manufacturer "{name}" already exists.'}, status=400)

        manufacturer = AccessoriesManufacturer.objects.create(name=name.title())
        logger.info(f"New accessory manufacturer '{name}' created by {request.user.username}")
        return JsonResponse({'success': True, 'id': str(manufacturer.id), 'name': manufacturer.name})

    except Exception as e:
        logger.error(f"Error adding accessory manufacturer: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
@role_required('HOD', json=True, message='Only HOD can delete accessory names.')
def delete_accessory_name(request, name_id):
    """Soft-delete an accessory name — HOD only."""
    profile = request.user.userprofile

    try:
        accessory_name = get_object_or_404(Accessoriesname, id=name_id)
        in_use = Accessories.objects.filter(name=accessory_name, active_status=True).count()
        if in_use:
            return JsonResponse({'error': f'Cannot delete "{accessory_name.name}" — used by {in_use} active accessory/accessories.'}, status=400)

        accessory_name.active_status = False
        accessory_name.pending_delete = True
        accessory_name.save()
        logger.info(f"Accessory name '{accessory_name.name}' deleted by {request.user.username}")
        return JsonResponse({'success': True, 'message': f'Accessory name "{accessory_name.name}" deleted.'})

    except Exception as e:
        logger.error(f"Error deleting accessory name {name_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
@role_required('HOD', json=True, message='Only HOD can delete manufacturers.')
def delete_accessory_manufacturer(request, manufacturer_id):
    """Soft-delete an accessory manufacturer — HOD only."""
    profile = request.user.userprofile

    try:
        manufacturer = get_object_or_404(AccessoriesManufacturer, id=manufacturer_id)
        in_use = Accessories.objects.filter(manufacturer=manufacturer, active_status=True).count()
        if in_use:
            return JsonResponse({'error': f'Cannot delete "{manufacturer.name}" — used by {in_use} active accessory/accessories.'}, status=400)

        manufacturer.active_status = False
        manufacturer.pending_delete = True
        manufacturer.save()
        logger.info(f"Accessory manufacturer '{manufacturer.name}' deleted by {request.user.username}")
        return JsonResponse({'success': True, 'message': f'Manufacturer "{manufacturer.name}" deleted.'})

    except Exception as e:
        logger.error(f"Error deleting accessory manufacturer {manufacturer_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)
