"""Dashboard view — the main accessories & tools management page."""
import logging

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from ..models import (
    Tools, Accessories, AccessoryRequest,
    ToolsManufacturer, AccessoriesManufacturer, Toolname, Accessoriesname,
)
from Inventory.models import Department, EquipmentDescription
from users.models import UserProfile
from users.control import get_user_role
from workshop.models import Workshop

logger = logging.getLogger(__name__)


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

    if get_user_role(request.user) == 'HOD':
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
