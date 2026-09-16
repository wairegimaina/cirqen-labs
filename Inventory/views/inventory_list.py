"""Inventory list / summary page views.

These are the HTML page views (with AJAX branches) for browsing equipment. The
duplicated inline logic for equipment serialization, pagination metadata and
page/per-page parsing now lives in :mod:`Inventory.views.helpers`.
"""
import logging
from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Count, Case, When, IntegerField
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect

from Inventory.models import Department, Equipment, EquipmentDescription, Manufacturer
from workshop.models import Workshop

from .helpers import (
    parse_page_number,
    normalize_per_page,
    serialize_equipment,
    build_pagination_data,
)

logger = logging.getLogger(__name__)


@login_required
def inventory(request):
    """Enhanced Inventory view with full AJAX support for all roles"""
    profile = request.user.userprofile
    selected_workshop = None
    selected_department = None
    template_name = 'Inventory/inventory.html'  # Default template for Technicians

    # --- Role-based access control ---
    if profile.role == 'HOD':
        template_name = 'Inventory/hod_inventory.html'
        all_workshops = Workshop.objects.filter(active_status=True).order_by('name')  # ✅ FILTER ACTIVE
        requested_workshop_id = request.GET.get('workshop')
        if requested_workshop_id:
            selected_workshop = get_object_or_404(Workshop, id=requested_workshop_id, active_status=True)  # ✅ FILTER ACTIVE
        elif all_workshops.exists():
            selected_workshop = all_workshops.first()
        else:
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({
                    'success': False,
                    'error': 'No workshops available',
                    'message': 'No workshops found in the system'
                }, status=404)

            return render(request, template_name, {
                'show_sidebar': True,  # Enable sidebar with hamburger menu
                'equipments': [],
                'departments': [],
                'equipment_descriptions': [],
                'status_summary': {},
                'all_workshops': [],
                'selected_workshop': None
            })

    elif profile.role == 'NIC':
        # Department In-Charge can only see equipment in their department
        template_name = 'Inventory/nurse_inventory.html'

        # Get the department associated with this NIC's profile
        if hasattr(profile, 'department') and profile.department:
            selected_department = profile.department
            selected_workshop = profile.department.workshop
        else:
            messages.error(request, "Your profile is not associated with any department.")
            return redirect('dashboard:dashboard-main')

    else:  # Technician
        if profile.workshop:
            selected_workshop = profile.workshop
            all_workshops = [profile.workshop]
        else:
            messages.error(request, "Your profile is not associated with a workshop.")
            return redirect('dashboard:dashboard-main')

    # --- Base queryset based on role (FILTER BY active_status=True) ---
    if profile.role == 'HOD':
        # HOD sees all ACTIVE equipment in selected workshop
        equipments = Equipment.objects.filter(
            department__workshop=selected_workshop,
            department__active_status=True,  # ✅ FILTER ACTIVE DEPARTMENTS
            active_status=True
        ).select_related('description', 'department__workshop', 'manufacturer').order_by('id')

    elif profile.role == 'NIC':
        # In-charge sees only ACTIVE equipment in their department
        equipments = Equipment.objects.filter(
            department=selected_department,
            department__active_status=True,  # ✅ FILTER ACTIVE DEPARTMENTS
            active_status=True
        ).select_related('description', 'department__workshop', 'manufacturer').order_by('id')

    else:  # Technician
        # Technician sees all ACTIVE equipment in their workshop
        equipments = Equipment.objects.filter(
            department__workshop=selected_workshop,
            department__active_status=True,  # ✅ FILTER ACTIVE DEPARTMENTS
            active_status=True
        ).select_related('description', 'department__workshop', 'manufacturer').order_by('id')

    # --- Apply filters ---
    department_filter = request.GET.get('department', '').strip()
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()

    current_department = None

    # Handle department filter based on role
    if department_filter and profile.role != 'NIC':
        try:
            if profile.role == 'HOD':
                current_department = Department.objects.get(
                    id=department_filter,
                    workshop=selected_workshop,
                    active_status=True  # ✅ FILTER ACTIVE DEPARTMENTS
                )
            else:  # Technician
                current_department = Department.objects.get(
                    id=department_filter,
                    workshop=selected_workshop,
                    active_status=True  # ✅ FILTER ACTIVE DEPARTMENTS
                )
            equipments = equipments.filter(department=current_department, active_status=True)
        except (Department.DoesNotExist, ValueError):
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid department filter',
                    'message': 'Selected department not found'
                }, status=400)

            messages.warning(request, "Selected department not found.")
            department_filter = ''

    # Search filter
    if search_query:
        equipments = equipments.filter(
            Q(description__name__icontains=search_query) |
            Q(manufacturer__name__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(department__name__icontains=search_query)
        )

    # Status filter
    if status_filter:
        if status_filter not in ['Working', 'Not working', 'Under repair']:
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid status filter',
                    'message': 'Invalid status value provided'
                }, status=400)
        else:
            equipments = equipments.filter(status=status_filter)

    # --- Get total count and status summary BEFORE pagination ---
    total_count = equipments.count()
    status_summary = dict(Counter(equipments.values_list('status', flat=True)))

    # --- Pagination ---
    per_page = normalize_per_page(
        request.GET.get("per_page", 10), invalid_fallback=1, error_fallback=100
    )
    page_number = parse_page_number(request.GET.get("page", 1))

    paginator = Paginator(equipments, per_page)

    # Handle page out of range
    if page_number > paginator.num_pages and paginator.num_pages > 0:
        page_number = paginator.num_pages

    try:
        page_obj = paginator.get_page(page_number)
    except Exception:
        page_obj = paginator.get_page(1)

    # --- AJAX JSON response ---
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        try:
            # Get departments based on role
            if profile.role == 'HOD':
                departments = Department.objects.filter(
                    workshop=selected_workshop,
                    active_status=True  # ✅ FILTER ACTIVE DEPARTMENTS
                ).order_by('name')
            elif profile.role == 'NIC':
                # NIC only sees their own department (if active)
                departments = [selected_department] if selected_department and selected_department.active_status else []
            else:  # Technician
                departments = Department.objects.filter(
                    workshop=selected_workshop,
                    active_status=True  # ✅ FILTER ACTIVE DEPARTMENTS
                ).order_by('name')

            # Get unique statuses from accessible ACTIVE equipment
            if profile.role == 'NIC':
                status_choices = Equipment.objects.filter(
                    department=selected_department,
                    department__active_status=True,  # ✅ FILTER ACTIVE DEPARTMENTS
                    active_status=True
                ).values_list('status', flat=True).distinct()
            else:
                status_choices = Equipment.objects.filter(
                    department__workshop=selected_workshop,
                    department__active_status=True,  # ✅ FILTER ACTIVE DEPARTMENTS
                    active_status=True
                ).values_list('status', flat=True).distinct()

            # Build equipment data
            equipment_data = [serialize_equipment(e) for e in page_obj]

            # Build pagination data
            pagination_data = build_pagination_data(page_obj, paginator, per_page, total_count)

            # Response data structure
            response_data = {
                'success': True,
                'equipments': equipment_data,
                'pagination': pagination_data,
                'summary': {
                    'total': total_count,
                    'working': status_summary.get('Working', 0),
                    'not_working': status_summary.get('Not working', 0),
                    'under_repair': status_summary.get('Under repair', 0),
                },
                'filters': {
                    'departments': [
                        {'id': d.id, 'name': d.name}
                        for d in departments
                    ] if isinstance(departments, list) else [
                        {'id': d.id, 'name': d.name}
                        for d in departments
                    ],
                    'statuses': list(status_choices),
                    'current_department': department_filter,
                    'current_status': status_filter,
                    'current_search': search_query,
                    'current_per_page': per_page
                },
                'message': f'Found {total_count} equipment item{"s" if total_count != 1 else ""}'
            }

            return JsonResponse(response_data)

        except Exception as e:
            logger.error(f"AJAX inventory error: {str(e)}", exc_info=True)
            return JsonResponse({
                'success': False,
                'error': f'Server error: {str(e)}',
                'message': 'Failed to load equipment data. Please try again.'
            }, status=500)

    # --- Normal render for initial page load ---
    # Get departments based on role
    if profile.role == 'HOD':
        departments = Department.objects.filter(
            workshop=selected_workshop,
            active_status=True  # ✅ FILTER ACTIVE DEPARTMENTS
        ).order_by('name')
        all_workshops_context = all_workshops
    elif profile.role == 'NIC':
        # NIC only sees their own department (if active)
        departments = [selected_department] if selected_department and selected_department.active_status else []
        all_workshops_context = [selected_workshop] if selected_workshop else []
    else:  # Technician
        departments = Department.objects.filter(
            workshop=selected_workshop,
            active_status=True  # ✅ FILTER ACTIVE DEPARTMENTS
        ).order_by('name')
        all_workshops_context = [selected_workshop]

    equipment_descriptions = EquipmentDescription.objects.all().order_by('name')
    manufacturers = Manufacturer.objects.all().order_by('name')

    # Get status choices for filters based on role (only ACTIVE equipment)
    if profile.role == 'NIC':
        status_choices = Equipment.objects.filter(
            department=selected_department,
            department__active_status=True,  # ✅ FILTER ACTIVE DEPARTMENTS
            active_status=True
        ).values_list('status', flat=True).distinct()
    else:
        status_choices = Equipment.objects.filter(
            department__workshop=selected_workshop,
            department__active_status=True,  # ✅ FILTER ACTIVE DEPARTMENTS
            active_status=True
        ).values_list('status', flat=True).distinct()

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'equipments': page_obj,
        'departments': departments,
        'equipment_descriptions': equipment_descriptions,
        'manufacturers': manufacturers,
        'all_workshops': all_workshops_context,
        'selected_workshop': selected_workshop,
        'selected_department': selected_department,  # For in-charge users
        'user_departments': departments if profile.role == 'NIC' else None,  # For in-charge users
        'search_query': search_query,
        'department_filter': department_filter,
        'current_department': current_department,
        'status_filter': status_filter,
        'status_choices': list(status_choices),
        'status_summary': status_summary,
        'total_equipment': total_count,
        'per_page': per_page,
        'total_count': total_count,
        'is_paginated': page_obj.has_other_pages(),
        'page_obj': page_obj,
        'user_role': profile.role,
    }

    return render(request, template_name, context)


@login_required
def inventory_for_hod(request, workshop_id):
    """HOD-specific view for viewing equipment in a specific workshop"""
    profile = request.user.userprofile

    if profile.role != 'HOD':
        messages.error(request, "Access denied. Only Heads of Department can view other workshops.")
        return redirect('inventory')

    # Get the target workshop
    target_workshop = get_object_or_404(Workshop, id=workshop_id, active_status=True)

    # Get all workshops for HOD
    all_workshops = Workshop.objects.filter(active_status=True).order_by('name')

    # Get only ACTIVE departments in this workshop
    departments = Department.objects.filter(
        workshop=target_workshop,
        active_status=True
    ).order_by('name')

    # Get equipment descriptions
    equipment_descriptions = EquipmentDescription.objects.all().order_by('name')

    # Get manufacturers
    manufacturers = Manufacturer.objects.all().order_by('name')

    # --- Base queryset: ONLY ACTIVE equipment in this workshop ---
    equipments = Equipment.objects.filter(
        department__workshop=target_workshop,
        department__active_status=True,  # Department must be active
        active_status=True  # Equipment must be active
    ).select_related('description', 'department__workshop', 'manufacturer').order_by('id')

    # --- Apply filters ---
    department_filter = request.GET.get('department', '').strip()
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()

    current_department = None

    # Department filter
    if department_filter:
        try:
            current_department = Department.objects.get(
                id=department_filter,
                workshop=target_workshop,
                active_status=True
            )
            equipments = equipments.filter(department=current_department)
        except (Department.DoesNotExist, ValueError):
            messages.warning(request, "Selected department not found or inactive.")
            department_filter = ''

    # Search filter
    if search_query:
        equipments = equipments.filter(
            Q(description__name__icontains=search_query) |
            Q(manufacturer__name__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(department__name__icontains=search_query)
        )

    # Status filter
    if status_filter and status_filter in ['Working', 'Not working', 'Under repair']:
        equipments = equipments.filter(status=status_filter)

    # Get total count and status summary BEFORE pagination
    total_count = equipments.count()
    status_summary = dict(Counter(equipments.values_list('status', flat=True)))

    # --- Pagination ---
    per_page = normalize_per_page(
        request.GET.get("per_page", 10), invalid_fallback=10, error_fallback=10
    )
    page_number = parse_page_number(request.GET.get("page", 1))

    paginator = Paginator(equipments, per_page)

    if page_number > paginator.num_pages and paginator.num_pages > 0:
        page_number = paginator.num_pages

    try:
        page_obj = paginator.get_page(page_number)
    except Exception:
        page_obj = paginator.get_page(1)

    # --- AJAX response ---
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        try:
            # Get unique statuses from accessible ACTIVE equipment
            status_choices = Equipment.objects.filter(
                department__workshop=target_workshop,
                department__active_status=True,
                active_status=True
            ).values_list('status', flat=True).distinct()

            # Build equipment data
            equipment_data = [serialize_equipment(e) for e in page_obj]

            # Build pagination data
            pagination_data = build_pagination_data(page_obj, paginator, per_page, total_count)

            # Response data structure
            response_data = {
                'success': True,
                'equipments': equipment_data,
                'pagination': pagination_data,
                'summary': {
                    'total': total_count,
                    'working': status_summary.get('Working', 0),
                    'not_working': status_summary.get('Not working', 0),
                    'under_repair': status_summary.get('Under repair', 0),
                },
                'filters': {
                    'departments': [
                        {
                            'id': d.id,
                            'name': d.name,
                            'workshop_id': str(d.workshop.id)  # Add workshop_id
                        }
                        for d in departments
                    ],
                    'statuses': list(status_choices),
                    'current_department': department_filter,
                    'current_status': status_filter,
                    'current_search': search_query,
                    'current_per_page': per_page
                },
                'message': f'Found {total_count} equipment item{"s" if total_count != 1 else ""}'
            }

            return JsonResponse(response_data)

        except Exception as e:
            logger.error(f"AJAX inventory error: {str(e)}", exc_info=True)
            return JsonResponse({
                'success': False,
                'error': f'Server error: {str(e)}',
                'message': 'Failed to load equipment data. Please try again.'
            }, status=500)

    # --- Normal render for initial page load ---
    # Get status choices for filters (only ACTIVE equipment)
    status_choices = Equipment.objects.filter(
        department__workshop=target_workshop,
        department__active_status=True,
        active_status=True
    ).values_list('status', flat=True).distinct()

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'equipments': page_obj,
        'departments': departments,
        'equipment_descriptions': equipment_descriptions,
        'manufacturers': manufacturers,
        'all_workshops': all_workshops,
        'selected_workshop': target_workshop,
        'search_query': search_query,
        'department_filter': department_filter,
        'current_department': current_department,
        'status_filter': status_filter,
        'status_choices': list(status_choices),
        'status_summary': status_summary,
        'total_equipment': total_count,
        'per_page': per_page,
        'total_count': total_count,
        'is_paginated': page_obj.has_other_pages(),
        'page_obj': page_obj,
        'user_role': profile.role,
    }

    return render(request, 'Inventory/hod_inventory.html', context)


@login_required
def inventory_by_department(request, dept_id):
    profile = request.user.userprofile
    target_workshop = None
    template_name = 'Inventory/inventory.html'  # Default for Techs

    if profile.role == 'HOD':
        template_name = 'Inventory/hod_inventory.html'
        requested_workshop_id = request.GET.get('workshop')
        if requested_workshop_id:
            target_workshop = get_object_or_404(Workshop, id=requested_workshop_id)
        else:
            messages.error(request, "Please select a workshop to view departments.")
            return redirect('inventory')
    else:  # Tech
        if not profile.workshop:
            messages.error(request, "Your profile is not associated with a workshop.")
            return redirect('inventory')
        target_workshop = profile.workshop
        requested_workshop_id = request.GET.get('workshop')  # Check if tech tried to view another workshop
        if requested_workshop_id and int(requested_workshop_id) != target_workshop.id:
            messages.error(request, "Access denied to selected workshop.")
            return redirect('inventory')

    department = get_object_or_404(Department, id=dept_id, workshop=target_workshop)
    equipments = Equipment.objects.filter(department=department, active_status=True).select_related('description', 'department').order_by('description__name')

    status_counts = Counter(e.status for e in equipments)
    status_summary = {
        'Working': status_counts.get('Working', 0),
        'Not_working': status_counts.get('Not working', 0),
        'Under_repair': status_counts.get('Under repair', 0),
    }

    all_workshops = []
    if profile.role == 'HOD':
        all_workshops = Workshop.objects.all().order_by('name')
    else:  # Tech
        all_workshops = [profile.workshop] if profile.workshop else []

    departments = Department.objects.filter(workshop=target_workshop).order_by('name')
    equipment_descriptions = EquipmentDescription.objects.all().order_by('name')

    return render(request, template_name, {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'equipments': equipments,
        'departments': departments,
        'equipment_descriptions': equipment_descriptions,
        'status_summary': status_summary,
        'selected_department': department.name,
        'current_department': department,
        'all_workshops': all_workshops,
        'selected_workshop': target_workshop,
    })


