"""Tools management (Tech users) — CRUD, listing, AJAX name/manufacturer helpers."""
import logging

from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from core.scoping import get_for_user_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST

from ..models import Tools, ToolsManufacturer, Toolname

logger = logging.getLogger(__name__)


@login_required
def add_tool(request):
    """Add a new tool — only Tech users."""
    profile = request.user.userprofile

    if profile.role != 'Tech':
        logger.warning(f"Unauthorized add_tool attempt by {request.user.username} (role={profile.role})")
        messages.error(request, 'Only Tech users can add tools.')
        return redirect('partstools:accessories_dashboard')

    if request.method != 'POST':
        return redirect('partstools:accessories_dashboard')

    try:
        name_id = request.POST.get('name')
        new_name = request.POST.get('new_name', '').strip()
        manufacturer_id = request.POST.get('manufacturer')
        new_manufacturer = request.POST.get('new_manufacturer', '').strip()
        model = request.POST.get('model', '').strip()
        serial_number = request.POST.get('serial_number', '').strip()

        # Resolve tool name
        tool_name = None
        if name_id:
            tool_name = Toolname.objects.filter(id=name_id).first()
        elif new_name:
            tool_name, created = Toolname.objects.get_or_create(
                name__iexact=new_name,
                defaults={'name': new_name.strip()}
            )
            if created:
                logger.info(f"New tool name '{new_name}' created by {request.user.username}")

        if not tool_name:
            messages.error(request, 'Tool name is required.')
            return redirect('partstools:accessories_dashboard')

        # Resolve manufacturer
        manufacturer = None
        if manufacturer_id:
            manufacturer = ToolsManufacturer.objects.filter(id=manufacturer_id).first()
        elif new_manufacturer:
            manufacturer, created = ToolsManufacturer.objects.get_or_create(
                name__iexact=new_manufacturer,
                defaults={'name': new_manufacturer.strip()}
            )
            if created:
                logger.info(f"New tool manufacturer '{new_manufacturer}' created by {request.user.username}")

        tool = Tools.objects.create(
            name=tool_name,
            manufacturer=manufacturer,
            model=model,
            serial_number=serial_number or None,
            workshop=profile.workshop,
        )

        logger.info(f"Tool '{tool}' created by {request.user.username}")
        messages.success(request, f'Tool "{tool}" has been added successfully!')

    except ValidationError as e:
        logger.error(f"Validation error adding tool: {e}")
        messages.error(request, f'Validation error: {e}')
    except Exception as e:
        logger.error(f"Error adding tool: {e}")
        messages.error(request, f'Error adding tool: {str(e)}')

    return redirect('partstools:accessories_dashboard')


@login_required
def edit_tool(request, pk):
    """Edit an existing tool — only Tech users for their own workshop."""
    profile = request.user.userprofile
    tool = get_object_or_404(Tools, pk=pk)

    if profile.role != 'Tech' or tool.workshop != profile.workshop:
        messages.error(request, "Unauthorized to edit this tool.")
        return redirect('partstools:accessories_dashboard')

    if request.method != 'POST':
        return redirect('partstools:accessories_dashboard')

    try:
        name_id = request.POST.get('name')
        new_name = request.POST.get('new_name', '').strip()
        manufacturer_id = request.POST.get('manufacturer')
        new_manufacturer = request.POST.get('new_manufacturer', '').strip()
        model = request.POST.get('model', '').strip()
        serial_number = request.POST.get('serial_number', '').strip()

        if name_id:
            tool.name = get_object_or_404(Toolname, id=name_id)
        elif new_name:
            tool_name, created = Toolname.objects.get_or_create(
                name__iexact=new_name, defaults={'name': new_name}
            )
            tool.name = tool_name
            if created:
                logger.info(f"New tool name '{new_name}' created during edit by {request.user.username}")

        if manufacturer_id:
            tool.manufacturer = get_object_or_404(ToolsManufacturer, id=manufacturer_id)
        elif new_manufacturer:
            manufacturer, created = ToolsManufacturer.objects.get_or_create(
                name__iexact=new_manufacturer, defaults={'name': new_manufacturer}
            )
            tool.manufacturer = manufacturer
            if created:
                logger.info(f"New manufacturer '{new_manufacturer}' created during edit by {request.user.username}")

        tool.model = model
        tool.serial_number = serial_number or None
        tool.save()

        logger.info(f"Tool '{tool}' updated by {request.user.username}")
        messages.success(request, f'Tool "{tool}" has been updated successfully!')

    except Exception as e:
        logger.error(f"Error updating tool: {e}")
        messages.error(request, f'Error updating tool: {str(e)}')

    return redirect('partstools:accessories_dashboard')


@login_required
def get_tool(request, pk):
    """Return tool data as JSON for the edit modal."""
    try:
        tool = get_for_user_or_404(Tools, request.user, pk=pk)
        return JsonResponse({
            'name': str(tool.name.id) if tool.name else '',
            'manufacturer': str(tool.manufacturer.id) if tool.manufacturer else '',
            'model': tool.model or '',
            'serial_number': tool.serial_number or '',
        })
    except Http404:
        raise
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
def delete_tool(request, pk):
    """Soft-delete a tool — only Tech users for their own workshop."""
    profile = request.user.userprofile
    tool = get_object_or_404(Tools, pk=pk)
    display = f"{tool.name.name if tool.name else 'Unnamed'} ({tool.manufacturer.name if tool.manufacturer else 'Unknown'})"

    if profile.role != 'Tech' or tool.workshop != profile.workshop:
        logger.warning(f"Unauthorized delete_tool by {request.user.username} on tool {pk}")
        messages.error(request, "Unauthorized to delete this tool.")
        return redirect('partstools:accessories_dashboard')

    try:
        tool.pending_delete = True
        tool.active_status = False
        tool.updated_at = timezone.now()
        tool.save(update_fields=['pending_delete', 'active_status', 'updated_at'])
        logger.info(f"Tool '{display}' ({pk}) marked for deletion by {request.user.username}")
        messages.success(request, f'Tool "{display}" marked for deletion.')
    except Exception as e:
        logger.error(f"Error deleting tool {pk}: {e}")
        messages.error(request, f"Failed to mark tool for deletion: {str(e)}")

    return redirect('partstools:accessories_dashboard')


@login_required
def tool_list(request):
    """List all tools (with optional search)."""
    query = request.GET.get('q', '')
    profile = request.user.userprofile

    if profile.role == 'HOD':
        tools = Tools.objects.filter(active_status=True).order_by('-created_at')
    else:
        tools = Tools.objects.filter(workshop=profile.workshop, active_status=True).order_by('-created_at')

    if query:
        tools = tools.filter(Q(name__name__icontains=query))

    return render(request, 'Parts & tools/accessories.html', {
        'show_sidebar': True,
        'tools': tools,
        'query': query,
        'is_hod': profile.role == 'HOD',
    })


@login_required
@require_POST
def ajax_add_tool_name(request):
    """AJAX: add a new tool name."""
    try:
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'Tool name cannot be empty.'}, status=400)

        if Toolname.objects.filter(name__iexact=name).exists():
            return JsonResponse({'error': f'Tool name "{name}" already exists.'}, status=400)

        tool_name = Toolname.objects.create(name=name.title())
        logger.info(f"New tool name '{name}' created by {request.user.username}")
        return JsonResponse({'success': True, 'id': str(tool_name.id), 'name': tool_name.name})

    except Exception as e:
        logger.error(f"Error adding tool name: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def ajax_add_tool_manufacturer(request):
    """AJAX: add a new tool manufacturer."""
    try:
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'Manufacturer name cannot be empty.'}, status=400)

        if ToolsManufacturer.objects.filter(name__iexact=name).exists():
            return JsonResponse({'error': f'Manufacturer "{name}" already exists.'}, status=400)

        manufacturer = ToolsManufacturer.objects.create(name=name.title())
        logger.info(f"New tool manufacturer '{name}' created by {request.user.username}")
        return JsonResponse({'success': True, 'id': str(manufacturer.id), 'name': manufacturer.name})

    except Exception as e:
        logger.error(f"Error adding tool manufacturer: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def delete_tool_name(request, name_id):
    """Soft-delete a tool name — Tech users only."""
    profile = request.user.userprofile
    if profile.role != 'Tech':
        return JsonResponse({'error': 'Only Tech users can delete tool names.'}, status=403)

    try:
        tool_name = get_object_or_404(Toolname, id=name_id)
        in_use = Tools.objects.filter(name=tool_name, active_status=True).count()
        if in_use:
            return JsonResponse({'error': f'Cannot delete "{tool_name.name}" — used by {in_use} active tool(s).'}, status=400)

        tool_name.active_status = False
        tool_name.pending_delete = True
        tool_name.save()
        logger.info(f"Tool name '{tool_name.name}' deleted by {request.user.username}")
        return JsonResponse({'success': True, 'message': f'Tool name "{tool_name.name}" deleted.'})

    except Exception as e:
        logger.error(f"Error deleting tool name {name_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def delete_tool_manufacturer(request, manufacturer_id):
    """Soft-delete a tool manufacturer — Tech users only."""
    profile = request.user.userprofile
    if profile.role != 'Tech':
        return JsonResponse({'error': 'Only Tech users can delete manufacturers.'}, status=403)

    try:
        manufacturer = get_object_or_404(ToolsManufacturer, id=manufacturer_id)
        in_use = Tools.objects.filter(manufacturer=manufacturer, active_status=True).count()
        if in_use:
            return JsonResponse({'error': f'Cannot delete "{manufacturer.name}" — used by {in_use} active tool(s).'}, status=400)

        manufacturer.pending_delete = True
        manufacturer.save()
        logger.info(f"Tool manufacturer '{manufacturer.name}' deleted by {request.user.username}")
        return JsonResponse({'success': True, 'message': f'Manufacturer "{manufacturer.name}" deleted.'})

    except Exception as e:
        logger.error(f"Error deleting tool manufacturer {manufacturer_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)
