# views.py
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.db.models import Q
from django.core.exceptions import ValidationError
from openpyxl import Workbook
from django.views.decorators.http import require_POST, require_http_methods
from django.db import transaction
from parts_tools.pdf_generators import AccessoriesPDFReport, ToolsPDFGenerator
from .models import (
    Tools, Accessories, AccessoryRequest, AccessoryRequestHistory,
    ToolsManufacturer, AccessoriesManufacturer, Toolname, Accessoriesname
)
from Inventory.models import Department, EquipmentDescription
from users.models import UserProfile
from workshop.models import Workshop
from datetime import datetime

logger = logging.getLogger(__name__)


# ==================== DASHBOARD ====================

@login_required
def accessories_dashboard(request, dept_id=None):
    """
    Main dashboard for accessories and tools management.
    Shows different views based on user role.
    """
    try:
        profile = request.user.userprofile
    except AttributeError:
        logger.warning(f"User {request.user.username} has no profile.")
        messages.error(request, "You do not have a user profile assigned. Please contact the administrator.")
        return redirect('custom_login')

    all_requests = None
    pending_requests = None
    approved_requests = None
    user_requests = None

    if profile.role == 'HOD':
        is_hod = True
        all_tools = Tools.objects.filter(active_status=True).select_related('name', 'manufacturer', 'workshop').order_by('-created_at')
        all_accessories = Accessories.objects.filter(active_status=True).select_related('name', 'manufacturer', 'equipment_description', 'workshop').order_by('-created_at')

        all_requests = AccessoryRequest.objects.all().select_related(
            'existing_accessory__name',
            'equipment_description',
            'requested_by__user',
            'approved_by__user',
            'accepted_by__user',
            'workshop',
        ).order_by('-requested_at')

        pending_requests = all_requests.filter(status='Pending')
        approved_requests = all_requests.filter(status='Approved')

        workshops = Workshop.objects.all()
        selected_workshop_id = request.GET.get('workshop_id') or request.session.get('selected_workshop_id')

        if selected_workshop_id:
            try:
                selected_workshop = Workshop.objects.get(id=selected_workshop_id)
                all_tools = all_tools.filter(workshop=selected_workshop)
                all_accessories = all_accessories.filter(workshop=selected_workshop)
                request.session['selected_workshop_id'] = str(selected_workshop.id)
                selected_workshop_id = selected_workshop.id
            except Workshop.DoesNotExist:
                selected_workshop = None
                selected_workshop_id = None
                messages.warning(request, "Selected workshop not found.")
        else:
            selected_workshop = None

    else:
        is_hod = False
        workshop = profile.workshop

        if not workshop:
            logger.warning(f"User {request.user.username} has no workshop assigned.")
            messages.error(request, "You do not have a workshop assigned. Please contact the administrator.")
            return redirect('custom_login')

        workshop_id = workshop.id
        all_tools = Tools.objects.filter(workshop_id=workshop_id, active_status=True).select_related('name', 'manufacturer', 'workshop').order_by('-created_at')
        all_accessories = Accessories.objects.filter(workshop_id=workshop_id, active_status=True).select_related('name', 'manufacturer', 'equipment_description', 'workshop').order_by('-created_at')

        user_requests = AccessoryRequest.objects.filter(
            requested_by=profile
        ).select_related(
            'existing_accessory__name',
            'equipment_description',
            'approved_by__user',
            'accepted_by__user',
            'workshop',
        ).order_by('-requested_at')

        approved_requests = AccessoryRequest.objects.filter(
            workshop=workshop,
            status='Approved'
        ).select_related(
            'existing_accessory__name',
            'equipment_description',
            'requested_by__user',
            'approved_by__user',
        ).order_by('-approved_at')

        workshops = None
        selected_workshop = workshop
        selected_workshop_id = workshop_id
        request.session['workshop_id'] = str(workshop_id)
        request.session.modified = True

    departments = None
    if selected_workshop_id:
        nurse_department_ids = UserProfile.objects.filter(
            role='Nurse',
            workshop_id=selected_workshop_id
        ).values_list('department_id', flat=True).distinct()
        departments = Department.objects.filter(
            id__in=nurse_department_ids,
            workshop_id=selected_workshop_id
        )

    tool_names = Toolname.objects.filter(active_status=True).order_by('name')
    accessory_names = Accessoriesname.objects.filter(active_status=True).order_by('name')
    tools_manufacturers = ToolsManufacturer.objects.all().order_by('name')
    accessories_manufacturers = AccessoriesManufacturer.objects.filter(active_status=True).order_by('name')
    equipment_descriptions = EquipmentDescription.objects.all()

    existing_accessories = (
        Accessories.objects.filter(workshop=selected_workshop, active_status=True)
        if selected_workshop
        else Accessories.objects.filter(active_status=True)
    )

    context = {
        'show_sidebar': True,
        'tools': all_tools,
        'accessories': all_accessories,
        'departments': departments,
        'selected_department_id': str(dept_id) if dept_id else None,
        'is_hod': is_hod,
        'workshops': workshops,
        'selected_workshop': selected_workshop,
        'selected_workshop_id': str(selected_workshop_id) if selected_workshop_id else None,
        'all_requests': all_requests,
        'pending_requests': pending_requests,
        'approved_requests': approved_requests,
        'user_requests': user_requests,
        'tool_names': tool_names,
        'accessory_names': accessory_names,
        'tools_manufacturers': tools_manufacturers,
        'accessories_manufacturers': accessories_manufacturers,
        'equipment_descriptions': equipment_descriptions,
        'existing_accessories': existing_accessories,
    }

    return render(request, 'Parts & tools/accessories.html', context)


# ==================== JSON API ENDPOINTS ====================
# These power the JS fetch calls in accessories.js

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
    tool = get_object_or_404(Tools, pk=pk)

    if profile.role == 'Tech' and tool.workshop != profile.workshop:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

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
    import json
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


# ==================== TOOLS MANAGEMENT ====================

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
        tool = get_object_or_404(Tools, pk=pk)
        return JsonResponse({
            'name': str(tool.name.id) if tool.name else '',
            'manufacturer': str(tool.manufacturer.id) if tool.manufacturer else '',
            'model': tool.model or '',
            'serial_number': tool.serial_number or '',
        })
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


# ==================== ACCESSORY MANAGEMENT ====================

@login_required
def edit_accessory(request, pk):
    """Edit an existing accessory — HOD only."""
    profile = request.user.userprofile
    accessory = get_object_or_404(Accessories, pk=pk)

    if profile.role != 'HOD':
        logger.warning(f"Unauthorized edit_accessory attempt by {request.user.username} (role={profile.role})")
        messages.error(request, 'Only HOD can directly edit accessories.')
        return redirect('partstools:accessories_dashboard')

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
def get_accessory(request, pk):
    """Return accessory data as JSON for the edit modal — HOD only."""
    profile = request.user.userprofile
    if profile.role != 'HOD':
        return JsonResponse({'error': 'Unauthorized'}, status=403)

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
def delete_accessory(request, pk):
    """Soft-delete an accessory — HOD only."""
    profile = request.user.userprofile
    accessory = get_object_or_404(Accessories, pk=pk)
    display = f"{accessory.name.name if accessory.name else 'Unnamed'} for {accessory.equipment_description.name if accessory.equipment_description else 'Unknown Equipment'}"

    if profile.role != 'HOD':
        logger.warning(f"Unauthorized delete_accessory by {request.user.username} (role={profile.role})")
        messages.error(request, "Only HOD can directly delete accessories.")
        return redirect('partstools:accessories_dashboard')

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


# ==================== REQUEST WORKFLOW ====================

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


@login_required
def view_request_history(request, request_id):
    """View the full history log of a specific accessory request."""
    accessory_request = get_object_or_404(AccessoryRequest, id=request_id)
    profile = request.user.userprofile

    if profile.role != 'HOD' and accessory_request.workshop != profile.workshop:
        messages.error(request, "You don't have permission to view this request.")
        return redirect('partstools:accessories_dashboard')

    history = accessory_request.history.all().select_related('performed_by__user')

    return render(request, 'Parts & tools/request_history.html', {
        'show_sidebar': True,
        'request': accessory_request,
        'history': history,
    })


# ==================== AJAX HELPERS ====================

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


# ==================== DELETE: NAMES & MANUFACTURERS ====================

@login_required
@require_POST
def delete_accessory_name(request, name_id):
    """Soft-delete an accessory name — HOD only."""
    profile = request.user.userprofile
    if profile.role != 'HOD':
        return JsonResponse({'error': 'Only HOD can delete accessory names.'}, status=403)

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
def delete_accessory_manufacturer(request, manufacturer_id):
    """Soft-delete an accessory manufacturer — HOD only."""
    profile = request.user.userprofile
    if profile.role != 'HOD':
        return JsonResponse({'error': 'Only HOD can delete manufacturers.'}, status=403)

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


# ==================== EXPORT ====================

@login_required
def export_excel_tools(request):
    """Export tools to Excel."""
    profile = request.user.userprofile
    if profile.role == 'HOD':
        tools = Tools.objects.filter(active_status=True).select_related('name', 'manufacturer', 'workshop')
    else:
        tools = Tools.objects.filter(workshop=profile.workshop, active_status=True).select_related('name', 'manufacturer', 'workshop')

    wb = Workbook()
    ws = wb.active
    ws.title = "Tools"
    ws.append(["Name", "Model", "Serial Number", "Manufacturer", "Workshop"])

    for tool in tools:
        ws.append([
            tool.name.name if tool.name else '',
            tool.model or '',
            tool.serial_number or '',
            tool.manufacturer.name if tool.manufacturer else '',
            tool.workshop.name if tool.workshop else 'Global',
        ])

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=tools.xlsx'
    wb.save(response)
    return response


@login_required
def export_excel_accessories(request):
    """Export accessories to Excel."""
    profile = request.user.userprofile
    if profile.role == 'HOD':
        accessories = Accessories.objects.filter(active_status=True).select_related('name', 'manufacturer', 'equipment_description', 'workshop')
    else:
        accessories = Accessories.objects.filter(workshop=profile.workshop, active_status=True).select_related('name', 'manufacturer', 'equipment_description', 'workshop')

    wb = Workbook()
    ws = wb.active
    ws.title = "Accessories"
    ws.append(["Name", "Equipment Description", "Manufacturer", "Note", "Stock Count", "Unit Cost", "Workshop"])

    for accessory in accessories:
        ws.append([
            accessory.name.name if accessory.name else '',
            accessory.equipment_description.name if accessory.equipment_description else '',
            accessory.manufacturer.name if accessory.manufacturer else '',
            accessory.note or '',
            accessory.stock_count,
            float(accessory.unit_cost),
            accessory.workshop.name if accessory.workshop else 'Global',
        ])

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=accessories.xlsx'
    wb.save(response)
    return response


@login_required
def export_pdf_tools(request):
    """Export tools to PDF."""
    profile = request.user.userprofile
    workshop_name = None

    if profile.role == 'HOD':
        selected_workshop_id = request.GET.get('workshop_id') or request.session.get('selected_workshop_id')
        if selected_workshop_id:
            try:
                selected_workshop = Workshop.objects.get(id=selected_workshop_id)
                tools = Tools.objects.filter(workshop=selected_workshop, active_status=True)
                workshop_name = selected_workshop.name
            except Workshop.DoesNotExist:
                tools = Tools.objects.filter(active_status=True)
                workshop_name = "All Workshops"
        else:
            tools = Tools.objects.filter(active_status=True)
            workshop_name = "All Workshops"
    else:
        tools = Tools.objects.filter(workshop=profile.workshop, active_status=True)
        workshop_name = profile.workshop.name if profile.workshop else None

    pdf_generator = ToolsPDFGenerator(tools, workshop_name)
    buffer = pdf_generator.generate_pdf()

    timestamp = datetime.now().strftime('%Y%m%d')
    safe_name = workshop_name.replace(' ', '_') if workshop_name else 'report'
    filename = f"tools_report_{safe_name}_{timestamp}.pdf"

    response = HttpResponse(content_type='application/pdf', content=buffer.getvalue())
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    buffer.close()
    return response


@login_required
def export_pdf_accessories(request):
    """Export accessories to PDF."""
    profile = request.user.userprofile
    workshop_name = None

    if profile.role == 'HOD':
        selected_workshop_id = request.GET.get('workshop_id') or request.session.get('selected_workshop_id')
        if selected_workshop_id:
            try:
                selected_workshop = Workshop.objects.get(id=selected_workshop_id)
                accessories = Accessories.objects.filter(workshop=selected_workshop, active_status=True)
                workshop_name = selected_workshop.name
            except Workshop.DoesNotExist:
                accessories = Accessories.objects.filter(active_status=True)
                workshop_name = "All Workshops"
        else:
            accessories = Accessories.objects.filter(active_status=True)
            workshop_name = "All Workshops"
    else:
        accessories = Accessories.objects.filter(workshop=profile.workshop, active_status=True)
        workshop_name = profile.workshop.name if profile.workshop else None

    pdf_generator = AccessoriesPDFReport(accessories, workshop_name)
    buffer = pdf_generator.generate_pdf()

    timestamp = datetime.now().strftime('%Y%m%d')
    safe_name = workshop_name.replace(' ', '_') if workshop_name else 'report'
    filename = f"accessories_report_{safe_name}_{timestamp}.pdf"

    response = HttpResponse(content_type='application/pdf', content=buffer.getvalue())
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    buffer.close()
    return response
