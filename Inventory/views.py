import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from Inventory.models import Department, Equipment, EquipmentDescription, Manufacturer
from workshop.models import Workshop
from users.models import UserProfile
from collections import Counter
import openpyxl
from django.db import transaction, models
from openpyxl import Workbook
from django.db.models import Q, Count, Case, When, IntegerField
from django.http import HttpResponse, JsonResponse
from django.core.exceptions import ValidationError
from django.conf import settings
import os
from openpyxl import load_workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.db.models import Q
from django.core.paginator import Paginator
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from collections import Counter
from .models import Equipment, Department, Workshop, EquipmentDescription, Manufacturer
import json
import uuid
from django.db import transaction, connection
from django.utils import timezone
from .equipment_pdf_generator import create_equipment_pdf_response, generate_equipment_pdf_report
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.apps import apps
from django.contrib.auth import get_user_model
from audit_log.models import AuditLog
User = get_user_model()
from .equipment_dependencies import (
    discover_equipment_dependencies,
    get_equipment_dependencies_count,
    transfer_equipment_dependencies,
    validate_equipment_transfer,
    verify_dependency_transfer,
    check_ppm_location,
)

@login_required
@require_http_methods(["GET"])
def check_equipment_ppm_locations(request, equipment_id):
    """
    API endpoint to check PPM locations for equipment
    Useful for debugging transfer issues
    """
    try:
        equipment = get_object_or_404(Equipment, pk=equipment_id)

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

    except Exception as e:
        logger.error(f"Error checking PPM locations: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@login_required
def inventory_summary_api(request):
    """API endpoint for summary data (filtered) - FIXED FOR NIC"""
    profile = request.user.userprofile
    selected_workshop = None
    selected_department = None

    # Determine workshop and department based on role
    if profile.role == 'HOD':
        requested_workshop_id = request.GET.get('workshop')
        if requested_workshop_id:
            try:
                selected_workshop = Workshop.objects.get(id=requested_workshop_id)
            except Workshop.DoesNotExist:
                pass
        if not selected_workshop:
            selected_workshop = Workshop.objects.first()

    elif profile.role == 'NIC':
        # NIC: Get their department and workshop
        if hasattr(profile, 'department') and profile.department:
            selected_department = profile.department
            selected_workshop = profile.department.workshop
        else:
            return JsonResponse({
                'error': 'No department assigned',
                'overall_totals': {
                    'total_equipment': 0,
                    'total_working': 0,
                    'total_not_working': 0,
                    'total_under_repair': 0,
                },
                'summary_data': []
            })

    else:  # Tech
        selected_workshop = profile.workshop

    if not selected_workshop:
        return JsonResponse({
            'error': 'No workshop available',
            'overall_totals': {
                'total_equipment': 0,
                'total_working': 0,
                'total_not_working': 0,
                'total_under_repair': 0,
            },
            'summary_data': []
        })

    # --- Base queryset based on role ---
    if profile.role == 'NIC' and selected_department:
        # NIC sees only their department's equipment
        filtered_queryset = Equipment.objects.filter(
            department=selected_department,
            department__active_status=True,
            active_status=True
        )
    else:
        # HOD and Tech see workshop equipment
        filtered_queryset = Equipment.objects.filter(
            department__workshop=selected_workshop,
            department__active_status=True,
            active_status=True
        )

    # --- Apply filters ---
    department_filter = request.GET.get('department', '').strip()
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()

    # Only apply department filter for non-NIC or if NIC manages multiple departments
    if department_filter and profile.role != 'NIC':
        filtered_queryset = filtered_queryset.filter(department_id=department_filter)

    if search_query:
        filtered_queryset = filtered_queryset.filter(
            Q(description__name__icontains=search_query) |
            Q(manufacturer__name__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(department__name__icontains=search_query)
        )

    if status_filter:
        filtered_queryset = filtered_queryset.filter(status=status_filter)

    # --- Calculate summary data ---
    summary_data = filtered_queryset.values(
        'description__name',
        'description__id'
    ).annotate(
        total_count=Count('id'),
        working_count=Count(
            Case(When(status='Working', then=1), output_field=IntegerField())
        ),
        not_working_count=Count(
            Case(When(status='Not working', then=1), output_field=IntegerField())
        ),
        under_repair_count=Count(
            Case(When(status='Under repair', then=1), output_field=IntegerField())
        )
    ).order_by('description__name')

    # --- Overall totals ---
    overall_totals = {
        'total_equipment': filtered_queryset.count(),
        'total_working': filtered_queryset.filter(status='Working').count(),
        'total_not_working': filtered_queryset.filter(status='Not working').count(),
        'total_under_repair': filtered_queryset.filter(status='Under repair').count(),
    }

    return JsonResponse({
        'overall_totals': overall_totals,
        'summary_data': list(summary_data)
    })


@login_required
def equipment_analytics_api(request):
    """API endpoint for dashboard charts data (filtered) - FIXED FOR NIC"""
    profile = request.user.userprofile
    selected_workshop = None
    selected_department = None

    # Determine workshop and department based on role
    if profile.role == 'HOD':
        requested_workshop_id = request.GET.get('workshop')
        if requested_workshop_id:
            try:
                selected_workshop = Workshop.objects.get(id=requested_workshop_id)
            except Workshop.DoesNotExist:
                pass
        if not selected_workshop:
            selected_workshop = Workshop.objects.first()

    elif profile.role == 'NIC':
        # NIC: Get their department and workshop
        if hasattr(profile, 'department') and profile.department:
            selected_department = profile.department
            selected_workshop = profile.department.workshop
        else:
            return JsonResponse({
                'error': 'No department assigned',
                'status_summary': {'Working': 0, 'Not_working': 0, 'Under_repair': 0},
                'department_counts': {}
            })

    else:  # Tech
        selected_workshop = profile.workshop

    if not selected_workshop:
        return JsonResponse({
            'error': 'No workshop available',
            'status_summary': {'Working': 0, 'Not_working': 0, 'Under_repair': 0},
            'department_counts': {}
        })

    # --- Base queryset based on role ---
    if profile.role == 'NIC' and selected_department:
        # NIC sees only their department's equipment
        equipments_queryset = Equipment.objects.filter(
            department=selected_department,
            department__active_status=True,
            active_status=True
        )
    else:
        # HOD and Tech see workshop equipment
        equipments_queryset = Equipment.objects.filter(
            department__workshop=selected_workshop,
            department__active_status=True,
            active_status=True
        )

    # --- Apply filters (same as inventory view) ---
    department_filter = request.GET.get('department', '').strip()
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()

    # Only apply department filter for non-NIC
    if department_filter and profile.role != 'NIC':
        equipments_queryset = equipments_queryset.filter(department_id=department_filter)

    if search_query:
        equipments_queryset = equipments_queryset.filter(
            Q(description__name__icontains=search_query) |
            Q(manufacturer__name__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(department__name__icontains=search_query)
        )

    if status_filter:
        equipments_queryset = equipments_queryset.filter(status=status_filter)

    # Get filtered equipment
    equipments = list(equipments_queryset.select_related('department'))

    # --- Status summary ---
    status_counts = Counter(e.status for e in equipments)
    status_summary = {
        'Working': status_counts.get('Working', 0),
        'Not_working': status_counts.get('Not working', 0),
        'Under_repair': status_counts.get('Under repair', 0),
    }

    # --- Department breakdown (always populate for chart) ---
    department_counts = {}
    if profile.role == 'NIC':
        description_counts = Counter(e.description.name for e in equipments)
        department_counts = dict(description_counts)
    else:
        dept_counts = Counter(e.department.name for e in equipments)
        department_counts = dict(dept_counts)
        if department_filter:
            filtered_dept_counts = Counter(
                e.department.name for e in equipments if str(e.department.id) == department_filter
            )
            if filtered_dept_counts:
                department_counts = dict(filtered_dept_counts)
            else:
                status_labels = {'Working': 0, 'Not working': 0, 'Under repair': 0}
                for e in equipments:
                    status_labels[e.status] = status_labels.get(e.status, 0) + 1
                department_counts = status_labels

    return JsonResponse({
        'status_summary': status_summary,
        'department_counts': department_counts,
        'total_equipment': len(equipments)
    })

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
        ).select_related('description', 'department', 'manufacturer').order_by('id')

    elif profile.role == 'NIC':
        # In-charge sees only ACTIVE equipment in their department
        equipments = Equipment.objects.filter(
            department=selected_department,
            department__active_status=True,  # ✅ FILTER ACTIVE DEPARTMENTS
            active_status=True
        ).select_related('description', 'department', 'manufacturer').order_by('id')

    else:  # Technician
        # Technician sees all ACTIVE equipment in their workshop
        equipments = Equipment.objects.filter(
            department__workshop=selected_workshop,
            department__active_status=True,  # ✅ FILTER ACTIVE DEPARTMENTS
            active_status=True
        ).select_related('description', 'department', 'manufacturer').order_by('id')

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
            equipments = equipments.filter(department=current_department,active_status=True)
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
    page_number = request.GET.get("page", 1)
    per_page = request.GET.get("per_page", 10)

    try:
        per_page = int(per_page)
        if per_page not in [10, 25, 50, 100]:
            per_page = 1
    except (ValueError, TypeError):
        per_page = 100

    try:
        page_number = int(page_number)
        if page_number < 1:
            page_number = 1
    except (ValueError, TypeError):
        page_number = 1

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
            equipment_data = []
            for e in page_obj:
                equipment_data.append({
                    'id': e.id,
                    'description': e.description.name,
                    'description_id': e.description.id,
                    'manufacturer': e.manufacturer.name if e.manufacturer else "",
                    'manufacturer_id': e.manufacturer.id if e.manufacturer else "",
                    'model': e.model if e.model else "",
                    'serial': e.serial_number if e.serial_number else "",
                    'department': e.department.name if e.department else "",
                    'department_id': e.department.id if e.department else "",
                    'workshop': e.department.workshop.name if e.department and e.department.workshop else "",
                    'workshop_id': str(e.department.workshop.id) if e.department and e.department.workshop else "",
                    'status': e.status,
                })

            # Build pagination data
            if total_count > 0:
                pagination_data = {
                    'has_next': page_obj.has_next(),
                    'has_previous': page_obj.has_previous(),
                    'next_page_number': page_obj.next_page_number() if page_obj.has_next() else None,
                    'previous_page_number': page_obj.previous_page_number() if page_obj.has_previous() else None,
                    'num_pages': paginator.num_pages,
                    'current_page': page_obj.number,
                    'start_index': page_obj.start_index(),
                    'end_index': page_obj.end_index(),
                    'total_count': total_count,
                    'per_page': per_page,
                    'has_other_pages': page_obj.has_other_pages(),
                    'page_range': [1],
                }
            else:
                pagination_data = {
                    'has_next': False,
                    'has_previous': False,
                    'next_page_number': None,
                    'previous_page_number': None,
                    'num_pages': 1,
                    'current_page': 1,
                    'start_index': 0,
                    'end_index': 0,
                    'total_count': 0,
                    'per_page': per_page,
                    'has_other_pages': False,
                    'page_range': [1],
                }

            # Add page range for pagination controls
            if paginator.num_pages > 1:
                try:
                    page_range = list(paginator.get_elided_page_range(
                        page_obj.number,
                        on_each_side=2,
                        on_ends=1
                    ))
                except AttributeError:
                    # Fallback for older Django versions
                    page_range = list(paginator.page_range)
            else:
                page_range = [1]

            pagination_data['page_range'] = page_range

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
    ).select_related('description', 'department', 'manufacturer').order_by('id')

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
    page_number = request.GET.get("page", 1)
    per_page = request.GET.get("per_page", 10)

    try:
        per_page = int(per_page)
        if per_page not in [10, 25, 50, 100]:
            per_page = 10
    except (ValueError, TypeError):
        per_page = 10

    try:
        page_number = int(page_number)
        if page_number < 1:
            page_number = 1
    except (ValueError, TypeError):
        page_number = 1

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

            equipment_data = []
            for e in page_obj:
                equipment_data.append({
                    'id': e.id,
                    'description': e.description.name,
                    'description_id': e.description.id,
                    'manufacturer': e.manufacturer.name if e.manufacturer else "",
                    'manufacturer_id': e.manufacturer.id if e.manufacturer else "",
                    'model': e.model if e.model else "",
                    'serial': e.serial_number if e.serial_number else "",
                    'department': e.department.name if e.department else "",
                    'department_id': e.department.id if e.department else "",
                    'workshop': e.department.workshop.name if e.department and e.department.workshop else "",
                    'workshop_id': str(e.department.workshop.id) if e.department and e.department.workshop else "",
                    'status': e.status,
                })

            # Build pagination data
            if total_count > 0:
                pagination_data = {
                    'has_next': page_obj.has_next(),
                    'has_previous': page_obj.has_previous(),
                    'next_page_number': page_obj.next_page_number() if page_obj.has_next() else None,
                    'previous_page_number': page_obj.previous_page_number() if page_obj.has_previous() else None,
                    'num_pages': paginator.num_pages,
                    'current_page': page_obj.number,
                    'start_index': page_obj.start_index(),
                    'end_index': page_obj.end_index(),
                    'total_count': total_count,
                    'per_page': per_page,
                    'has_other_pages': page_obj.has_other_pages(),
                    'page_range': [1],
                }
            else:
                pagination_data = {
                    'has_next': False,
                    'has_previous': False,
                    'next_page_number': None,
                    'previous_page_number': None,
                    'num_pages': 1,
                    'current_page': 1,
                    'start_index': 0,
                    'end_index': 0,
                    'total_count': 0,
                    'per_page': per_page,
                    'has_other_pages': False,
                    'page_range': [1],
                }

            # Add page range for pagination controls
            if paginator.num_pages > 1:
                try:
                    page_range = list(paginator.get_elided_page_range(
                        page_obj.number,
                        on_each_side=2,
                        on_ends=1
                    ))
                except AttributeError:
                    page_range = list(paginator.page_range)
            else:
                page_range = [1]

            pagination_data['page_range'] = page_range

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
            department = Department.objects.get(id=department_id)
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

        return redirect("inventory")


@login_required
@require_http_methods(["POST"])
def transfer_equipment(request, equipment_id):
    """
    Transfer equipment between departments (same or different workshops)
    ✅ NOW INCLUDES: Automatic dependency transfer + verification
    """
    try:
        equipment = get_object_or_404(Equipment, pk=equipment_id, active_status=True)
        profile = request.user.userprofile

        # Permission check - only Tech can transfer
        if profile.role != 'Tech':
            return JsonResponse({
                'success': False,
                'error': 'Only Technologists can transfer equipment.'
            }, status=403)

        # Get target department
        target_department_id = request.POST.get('target_department_id')
        if not target_department_id:
            return JsonResponse({
                'success': False,
                'error': 'Target department is required.'
            }, status=400)

        try:
            target_department = Department.objects.get(
                id=target_department_id,
                active_status=True
            )
        except Department.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Target department not found or inactive.'
            }, status=404)

        # Validate target department has an active workshop
        if not target_department.workshop or not target_department.workshop.active_status:
            return JsonResponse({
                'success': False,
                'error': 'Target department does not belong to an active workshop.'
            }, status=400)

        target_workshop = target_department.workshop

        # Get new status (optional)
        new_status = request.POST.get('status', equipment.status)
        if new_status not in ['Working', 'Not working', 'Under repair']:
            new_status = equipment.status

        # Store old values for audit
        old_department = equipment.department
        old_workshop = old_department.workshop if old_department else None
        old_status = equipment.status

        # Check if transferring to same department
        if old_department and old_department.id == target_department.id:
            return JsonResponse({
                'success': False,
                'error': 'Equipment is already in this department.'
            }, status=400)

        # Determine transfer type
        is_cross_workshop = old_workshop and old_workshop.id != target_workshop.id

        logger.info("=" * 80)
        logger.info(f"🔄 EQUIPMENT TRANSFER initiated by {request.user.username}")
        logger.info(f"Equipment: {equipment.serial_number} ({equipment.id})")
        logger.info(f"FROM: {old_workshop.name if old_workshop else 'Unknown'} / {old_department.name if old_department else 'Unknown'}")
        logger.info(f"TO: {target_workshop.name} / {target_department.name}")
        logger.info(f"Cross-workshop: {is_cross_workshop}")

        # ✅ CHECK FOR DEPENDENCIES
        dep_count, dep_breakdown = get_equipment_dependencies_count(equipment)

        if dep_count > 0:
            logger.info(f"📦 Found {dep_count} dependencies to transfer")

            # Validate transfer
            can_transfer, error_msg, dep_info = validate_equipment_transfer(
                equipment, target_department, target_workshop
            )

            if not can_transfer:
                logger.error(f"❌ Transfer validation failed: {error_msg}")
                return JsonResponse({
                    'success': False,
                    'error': error_msg,
                    'dependency_info': dep_info
                }, status=400)

        logger.info("=" * 80)

        # Perform transfer with dependencies
        try:
            with transaction.atomic():
                # ✅ STEP 1: Transfer dependencies FIRST
                dependencies_transferred = 0
                transfer_results = []

                if dep_count > 0:
                    logger.info(f"🔄 STEP 1: Transferring {dep_count} dependencies...")
                    dependencies_transferred, transfer_results = transfer_equipment_dependencies(
                        equipment, target_department, target_workshop
                    )
                    logger.info(f"✅ Dependencies transferred: {dependencies_transferred}")

                # ✅ STEP 2: Transfer the equipment itself
                logger.info(f"🔄 STEP 2: Transferring equipment...")
                equipment.department = target_department
                equipment.workshop = target_workshop  # ✅ FIX: update workshop so PPM init can find this equipment
                equipment.status = new_status
                equipment.updated_at = timezone.now()
                equipment.save(update_fields=['department', 'workshop', 'status', 'updated_at'])

                logger.info(f"✅ Equipment transferred successfully")

                # ✅ STEP 3: Create audit log (FIXED)
                try:
                    now = timezone.now()

                    audit_entry = AuditLog.objects.create(
                        table_name='public.Inventory_equipment',
                        row_id=equipment.id,
                        operation=AuditLog.OPERATION_TRANSFER,
                        source='django-backend',
                        user_id=request.user.id if request.user.is_authenticated else None,
                        received_at=now,
                        created_at=now,
                        metadata={
                            'old_department': old_department.name if old_department else None,
                            'new_department': target_department.name,
                            'old_workshop': old_workshop.name if old_workshop else None,
                            'new_workshop': target_workshop.name,
                            'is_cross_workshop': is_cross_workshop,
                            'dependencies_transferred': dependencies_transferred,
                            'old_status': old_status,
                            'new_status': new_status
                        }
                    )
                    logger.info(f"✅ Audit log created (event_id: {audit_entry.event_id})")

                except Exception as audit_error:
                    logger.error(f"⚠️ Audit log failed: {audit_error}")
                    # Don't raise - we don't want audit failure to block transfer

        except Exception as e:
            logger.error(f"❌ Transfer transaction failed: {str(e)}")
            logger.exception(e)
            raise

        # ✅ STEP 4: VERIFY all dependencies transferred correctly
        logger.info(f"🔍 STEP 4: Verifying dependency transfer...")

        all_correct, issues = verify_dependency_transfer(
            equipment, target_workshop, target_department
        )

        if not all_correct:
            logger.error(f"❌ Verification found {len(issues)} issues!")
            for issue in issues:
                logger.error(f"   • {issue['model']} {issue['record_id']}: {issue['field']} = {issue['actual']} (expected {issue['expected']})")

        logger.info("=" * 80)
        logger.info(f"🎉 TRANSFER COMPLETE")
        logger.info(f"   Equipment transferred: {equipment.serial_number}")
        logger.info(f"   Dependencies transferred: {dependencies_transferred}")
        logger.info(f"   Verification: {'✅ PASSED' if all_correct else f'❌ FAILED ({len(issues)} issues)'}")
        logger.info("=" * 80)

        # Success message
        if is_cross_workshop:
            message = f"✅ Equipment and {dependencies_transferred} dependencies transferred from {old_workshop.name} to {target_workshop.name} ({target_department.name})"
        else:
            message = f"✅ Equipment and {dependencies_transferred} dependencies moved to {target_department.name}"

        return JsonResponse({
            'success': True,
            'message': message,
            'equipment': {
                'id': str(equipment.id),
                'serial_number': equipment.serial_number,
                'old_department': old_department.name if old_department else 'Unknown',
                'old_workshop': old_workshop.name if old_workshop else 'Unknown',
                'new_department': target_department.name,
                'new_workshop': target_workshop.name,
                'status': equipment.status,
                'is_cross_workshop': is_cross_workshop,
                'dependencies_transferred': dependencies_transferred,
                'transfer_results': transfer_results,
                'verification_passed': all_correct,
                'verification_issues': issues if not all_correct else []
            }
        })

    except Exception as e:
        logger.error(f"❌ Transfer failed: {str(e)}")
        logger.exception(e)
        return JsonResponse({
            'success': False,
            'error': f'Transfer failed: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["POST"])
def reactivate_equipment(request, equipment_id):
    """
    Enhanced reactivation with cross-workshop transfer support
    ✅ NOW INCLUDES: Automatic dependency transfer + verification
    """
    try:
        equipment = Equipment.objects.select_related(
            'department',
            'department__workshop',
            'description',
            'manufacturer'
        ).get(pk=equipment_id)

    except Equipment.DoesNotExist:
        logger.error(f"❌ Equipment not found: {equipment_id}")
        return JsonResponse({
            'success': False,
            'error': 'Equipment not found.'
        }, status=404)

    try:
        profile = request.user.userprofile
    except AttributeError:
        logger.error(f"❌ User profile not found for user: {request.user.username}")
        return JsonResponse({
            'success': False,
            'error': 'User profile not found.'
        }, status=403)

    # Permission checks
    if profile.role != 'Tech':
        logger.warning(f"❌ Unauthorized reactivation attempt by {request.user.username} (role: {profile.role})")
        return JsonResponse({
            'success': False,
            'error': 'Only Technologists can reactivate equipment.'
        }, status=403)

    if not profile.workshop:
        logger.error(f"❌ No workshop assigned to user: {request.user.username}")
        return JsonResponse({
            'success': False,
            'error': 'Your profile is not associated with a workshop.'
        }, status=403)

    # Must specify target department
    new_department_id = request.POST.get('department_id')
    if not new_department_id:
        return JsonResponse({
            'success': False,
            'error': 'Department is required.'
        }, status=400)

    # Validate target department
    try:
        new_department = Department.objects.select_related('workshop').get(
            id=new_department_id,
            active_status=True
        )
    except Department.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Invalid or inactive department selected.'
        }, status=400)

    # Validate target department's workshop
    if not new_department.workshop or not new_department.workshop.active_status:
        return JsonResponse({
            'success': False,
            'error': 'Target department does not belong to an active workshop.'
        }, status=400)

    target_workshop = new_department.workshop

    # Tech can only reactivate TO their workshop
    if target_workshop.id != profile.workshop.id:
        return JsonResponse({
            'success': False,
            'error': f'You can only reactivate equipment to your workshop ({profile.workshop.name}).'
        }, status=403)

    # Determine new status
    new_status = request.POST.get('status', 'Working')
    if new_status not in ['Working', 'Not working', 'Under repair']:
        new_status = 'Working'

    # Store old data for audit
    old_department = equipment.department
    old_workshop = old_department.workshop if old_department else None
    old_active = equipment.active_status
    old_pending = equipment.pending_delete
    old_status = equipment.status

    # Determine operation type
    is_cross_workshop_transfer = old_workshop and old_workshop.id != target_workshop.id
    is_cross_department_transfer = old_department and old_department.id != new_department.id
    is_transfer = is_cross_workshop_transfer or is_cross_department_transfer

    logger.info("=" * 80)
    logger.info(f"🔄 REACTIVATION ATTEMPT by {request.user.username}")
    logger.info(f"Equipment: {equipment.serial_number} (ID: {equipment.id})")
    logger.info(f"Description: {equipment.description.name if equipment.description else 'N/A'}")
    logger.info(f"FROM: {old_workshop.name if old_workshop else 'Unknown'} / {old_department.name if old_department else 'Unknown'}")
    logger.info(f"TO: {target_workshop.name} / {new_department.name}")
    logger.info(f"Cross-workshop transfer: {is_cross_workshop_transfer}")
    logger.info(f"BEFORE: active={equipment.active_status}, pending_delete={equipment.pending_delete}, status={old_status}")

    # ✅ CHECK FOR DEPENDENCIES
    dep_count, dep_breakdown = get_equipment_dependencies_count(equipment)

    if dep_count > 0:
        logger.info(f"📦 Found {dep_count} dependencies to transfer")

        # Validate transfer
        can_transfer, error_msg, dep_info = validate_equipment_transfer(
            equipment, new_department, target_workshop
        )

        if not can_transfer:
            logger.error(f"❌ Reactivation validation failed: {error_msg}")
            return JsonResponse({
                'success': False,
                'error': error_msg,
                'dependency_info': dep_info
            }, status=400)

    logger.info("=" * 80)

    # Perform reactivation/transfer with dependencies
    try:
        with transaction.atomic():
            # ✅ STEP 1: Transfer dependencies FIRST (if any)
            dependencies_transferred = 0
            transfer_results = []

            if dep_count > 0:
                logger.info(f"🔄 STEP 1: Transferring {dep_count} dependencies...")
                dependencies_transferred, transfer_results = transfer_equipment_dependencies(
                    equipment, new_department, target_workshop
                )
                logger.info(f"✅ Dependencies transferred: {dependencies_transferred}")

            # ✅ STEP 2: Reactivate and transfer the equipment
            logger.info(f"🔄 STEP 2: Reactivating equipment...")
            equipment.department = new_department
            equipment.workshop = target_workshop  # ✅ FIX: update workshop so PPM init can find this equipment
            equipment.status = new_status
            equipment.active_status = True
            equipment.pending_delete = False
            equipment.updated_at = timezone.now()

            equipment.save(update_fields=[
                'department', 'workshop', 'status', 'active_status', 'pending_delete', 'updated_at'
            ])

            # Verify save
            equipment.refresh_from_db()

            if equipment.pending_delete or not equipment.active_status:
                raise Exception("Failed to update equipment status correctly")

            logger.info(f"✅ Equipment reactivated successfully")

            # ✅ STEP 3: Create audit log using Django ORM
            try:
                audit_entry = AuditLog.objects.create(
                    table_name='public.Inventory_equipment',
                    row_id=equipment.id,
                    operation=AuditLog.OPERATION_RESTORE,  # 'r' for reactivate
                    source='django-backend',
                    user_id=request.user.id if request.user.is_authenticated else None,
                    changed_fields=[
                        'department', 'status', 'active_status',
                        'pending_delete', 'updated_at'
                    ],
                    old_values={
                        'department_id': str(old_department.id) if old_department else None,
                        'department_name': old_department.name if old_department else None,
                        'workshop_id': str(old_workshop.id) if old_workshop else None,
                        'workshop_name': old_workshop.name if old_workshop else None,
                        'status': old_status,
                        'active_status': old_active,
                        'pending_delete': old_pending,
                    },
                    new_values={
                        'department_id': str(new_department.id),
                        'department_name': new_department.name,
                        'workshop_id': str(target_workshop.id),
                        'workshop_name': target_workshop.name,
                        'status': new_status,
                        'active_status': True,
                        'pending_delete': False,
                    },
                    metadata={
                        'operation_type': 'transfer' if is_transfer else 'reactivate',
                        'is_cross_workshop': is_cross_workshop_transfer,
                        'is_cross_department': is_cross_department_transfer,
                        'dependencies_transferred': dependencies_transferred,
                        'technologist': request.user.username,
                        'technologist_workshop': profile.workshop.name,
                    }
                )
                logger.info(f"✅ Audit log created (event_id: {audit_entry.event_id})")

            except Exception as audit_error:
                logger.error(f"⚠️ Audit log failed: {audit_error}")
                logger.exception(audit_error)
                # Don't raise - we don't want audit failure to block reactivation

        # ✅ STEP 4: VERIFY all dependencies transferred correctly
        logger.info(f"🔍 STEP 4: Verifying dependency transfer...")

        all_correct, issues = verify_dependency_transfer(
            equipment, target_workshop, new_department
        )

        if not all_correct:
            logger.error(f"❌ Verification found {len(issues)} issues!")
            for issue in issues:
                logger.error(f"   • {issue['model']} {issue['record_id']}: {issue['field']} = {issue['actual']} (expected {issue['expected']})")

        logger.info("=" * 80)
        logger.info(f"🎉 REACTIVATION COMPLETE")
        logger.info(f"   Equipment: {equipment.serial_number}")
        logger.info(f"   AFTER: active={equipment.active_status}, pending_delete={equipment.pending_delete}, status={equipment.status}")
        logger.info(f"   Department: {equipment.department.name}")
        logger.info(f"   Workshop: {equipment.department.workshop.name}")
        logger.info(f"   Dependencies transferred: {dependencies_transferred}")
        logger.info(f"   Verification: {'✅ PASSED' if all_correct else f'❌ FAILED ({len(issues)} issues)'}")
        logger.info("=" * 80)

        # Success message
        if is_cross_workshop_transfer:
            message = f"✅ Equipment and {dependencies_transferred} dependencies TRANSFERRED from {old_workshop.name} to {target_workshop.name} and reactivated in {new_department.name}"
        elif is_cross_department_transfer:
            message = f"✅ Equipment and {dependencies_transferred} dependencies moved from {old_department.name} to {new_department.name} and reactivated"
        else:
            message = f"✅ Equipment and {dependencies_transferred} dependencies reactivated in {new_department.name}"

        return JsonResponse({
            'success': True,
            'message': message,
            'operation_type': 'transfer' if is_transfer else 'reactivate',
            'equipment': {
                'id': str(equipment.id),
                'serial_number': equipment.serial_number,
                'description': equipment.description.name if equipment.description else 'N/A',
                'old_department': old_department.name if old_department else 'Unknown',
                'old_workshop': old_workshop.name if old_workshop else 'Unknown',
                'new_department': new_department.name,
                'new_workshop': target_workshop.name,
                'status': equipment.status,
                'active_status': equipment.active_status,
                'pending_delete': equipment.pending_delete,
                'is_cross_workshop': is_cross_workshop_transfer,
                'is_transfer': is_transfer,
                'dependencies_transferred': dependencies_transferred,
                'transfer_results': transfer_results,
                'verification_passed': all_correct,
                'verification_issues': issues if not all_correct else []
            }
        })

    except Exception as e:
        logger.error(f"❌ Reactivation failed: {str(e)}")
        logger.exception(e)
        return JsonResponse({
            'success': False,
            'error': f'Failed to reactivate: {str(e)}'
        }, status=500)

@login_required
@require_http_methods(["GET"])
def get_equipment_dependency_info(request, equipment_id):
    """
    API endpoint to get dependency information for equipment
    Useful for showing users what will be transferred
    """
    try:
        equipment = get_object_or_404(Equipment, pk=equipment_id)
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
def get_available_departments_for_transfer(request):
    """
    Get ALL available departments for equipment transfer, grouped by workshop
    """
    profile = request.user.userprofile

    if not profile.workshop:
        return JsonResponse({
            'success': False,
            'error': 'No workshop assigned to your profile'
        }, status=403)

    # ✅ Get ALL active departments from ALL active workshops
    departments = Department.objects.filter(
        active_status=True,
        workshop__active_status=True
    ).select_related('workshop').order_by('workshop__name', 'name')

    # Group departments by workshop
    departments_by_workshop = {}
    for dept in departments:
        workshop_name = dept.workshop.name
        workshop_id = str(dept.workshop.id)

        if workshop_name not in departments_by_workshop:
            departments_by_workshop[workshop_name] = {
                'workshop_id': workshop_id,
                'workshop_name': workshop_name,
                'departments': []
            }

        departments_by_workshop[workshop_name]['departments'].append({
            'id': dept.id,
            'name': dept.name,
            'workshop_id': workshop_id,
            'workshop_name': workshop_name
        })

    # Flatten for response
    all_departments = []
    for workshop_data in departments_by_workshop.values():
        all_departments.extend(workshop_data['departments'])

    return JsonResponse({
        'success': True,
        'departments': all_departments,
        'departments_by_workshop': departments_by_workshop,
        'current_workshop': {
            'id': str(profile.workshop.id),
            'name': profile.workshop.name
        }
    })
@login_required
def check_equipment_availability(request):
    """
    ENHANCED: Check equipment availability with transfer detection
    Always returns JSON response
    """
    serial_number = request.GET.get('serial_number', '').strip().upper()

    if not serial_number:
        return JsonResponse({
            'exists': False,
            'can_reactivate': False,
            'message': 'Serial number required'
        })

    try:
        # Get current user's workshop
        profile = request.user.userprofile

        if not profile.workshop:
            return JsonResponse({
                'exists': False,
                'can_reactivate': False,
                'error': 'Your profile is not associated with a workshop',
                'message': 'Profile not configured properly'
            })

        current_workshop = profile.workshop

        # Find equipment with this serial number
        equipment = Equipment.objects.filter(
            serial_number__iexact=serial_number
        ).select_related(
            'description',
            'manufacturer',
            'department',
            'department__workshop'
        ).first()

        if not equipment:
            return JsonResponse({
                'exists': False,
                'can_reactivate': False,
                'message': 'Serial number available'
            })

        # Check if equipment department has a workshop
        if not equipment.department or not equipment.department.workshop:
            logger.warning(f"Equipment {serial_number} has no valid department/workshop")
            return JsonResponse({
                'exists': True,
                'can_reactivate': False,
                'error': 'Equipment has invalid department/workshop assignment',
                'message': 'Please contact system administrator'
            })

        # Check if equipment is currently active
        if equipment.active_status and not equipment.pending_delete:
            is_same_workshop = equipment.department.workshop.id == current_workshop.id

            return JsonResponse({
                'exists': True,
                'can_reactivate': False,
                'is_active': True,
                'message': f'Equipment already exists in {equipment.department.name} ({equipment.department.workshop.name})',
                'department': equipment.department.name,
                'workshop': equipment.department.workshop.name,
                'is_same_workshop': is_same_workshop
            })

        # Equipment is deactivated - determine if it's a transfer
        equipment_workshop_id = equipment.department.workshop.id
        is_same_workshop = equipment_workshop_id == current_workshop.id
        will_transfer_workshop = not is_same_workshop
        will_transfer_department = True  # Always to a new department when reactivating

        # Create appropriate message
        if will_transfer_workshop:
            reactivation_type = "Cross-Workshop Transfer"
            message = (
                f'This equipment was in {equipment.department.workshop.name}. '
                f'Reactivating it will TRANSFER it to your workshop ({current_workshop.name}).'
            )
        else:
            reactivation_type = "Same-Workshop Reactivation"
            message = (
                f'Equipment was previously in {equipment.department.name}. '
                f'You can reactivate it to a department in your workshop.'
            )

        logger.info(f"🔍 Equipment check: {serial_number}")
        logger.info(f"   Current state: active={equipment.active_status}, pending_delete={equipment.pending_delete}")
        logger.info(f"   Last location: {equipment.department.workshop.name} / {equipment.department.name}")
        logger.info(f"   Target workshop: {current_workshop.name}")
        logger.info(f"   Will transfer: {will_transfer_workshop}")

        return JsonResponse({
            'exists': True,
            'can_reactivate': True,
            'is_active': False,
            'message': message,
            'reactivation_type': reactivation_type,

            # Equipment details
            'equipment_id': equipment.id,
            'description': equipment.description.name if equipment.description else 'N/A',
            'manufacturer': equipment.manufacturer.name if equipment.manufacturer else 'N/A',
            'model': equipment.model or 'N/A',
            'serial_number': equipment.serial_number,
            'last_status': equipment.status,

            # Location information
            'last_department': equipment.department.name,
            'last_department_id': str(equipment.department.id),
            'last_workshop': equipment.department.workshop.name,
            'last_workshop_id': str(equipment.department.workshop.id),

            'current_user_workshop': current_workshop.name,
            'current_user_workshop_id': str(current_workshop.id),

            # Transfer detection flags
            'is_same_workshop': is_same_workshop,
            'will_transfer_workshop': will_transfer_workshop,
            'will_transfer_department': will_transfer_department,
            'is_transfer_operation': will_transfer_workshop or will_transfer_department
        })

    except Exception as e:
        logger.error(f"❌ Error checking equipment availability: {str(e)}")
        logger.exception(e)
        return JsonResponse({
            'exists': False,
            'can_reactivate': False,
            'error': f'Server error: {str(e)}',
            'message': 'Failed to check equipment availability'
        }, status=500)
logger = logging.getLogger(__name__)

@login_required
def edit_inventory(request, equipment_id):
    equipment = get_object_or_404(Equipment, id=equipment_id)

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
            department = Department.objects.get(id=department_id)
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

            messages.success(request, "Equipment updated successfully.",extra_tags="success  task created")
        except Exception as e:
            messages.error(request, f"Error updating equipment: {e}")

        return redirect("inventory")

    # GET request → load edit form
    equipment_descriptions = EquipmentDescription.objects.all().order_by("name")
    manufacturers = Manufacturer.objects.all().order_by("name")
    departments = Department.objects.all().order_by("name")

    return render(request, "Inventory/edit_inventory.html", {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "equipment": equipment,
        "equipment_descriptions": equipment_descriptions,
        "manufacturers": manufacturers,
        "departments": departments,
    })

import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from .models import Equipment

logger = logging.getLogger(__name__)

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

@login_required
def dashboard_view(request):
    profile = request.user.userprofile
    target_workshop = None

    if profile.role == 'HOD':
        status_counts = Equipment.objects.filter(
            active_status=True  # ✅ FILTER ACTIVE EQUIPMENT
        ).values('status').annotate(total=Count('status'))
    else: # Tech
        if not profile.workshop:
            messages.error(request, "Your profile is not associated with a workshop.")
            return redirect('inventory')
        target_workshop = profile.workshop
        status_counts = Equipment.objects.filter(department__workshop=target_workshop,active_status=True).values('status').annotate(total=Count('status'))

    status_summary = {
        'Working': 0,
        'Not working': 0,
        'Under repair': 0,
    }

    for entry in status_counts:
        status_summary[entry['status']] = entry['total']

    return render(request, '/dashboard.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'status_summary': status_summary
    })


@login_required
def inventory_by_department(request, dept_id):
    profile = request.user.userprofile
    target_workshop = None
    template_name = 'Inventory/inventory.html' # Default for Techs

    if profile.role == 'HOD':
        template_name = 'Inventory/hod_inventory.html'
        requested_workshop_id = request.GET.get('workshop')
        if requested_workshop_id:
            target_workshop = get_object_or_404(Workshop, id=requested_workshop_id)
        else:
            messages.error(request, "Please select a workshop to view departments.")
            return redirect('inventory')
    else: # Tech
        if not profile.workshop:
            messages.error(request, "Your profile is not associated with a workshop.")
            return redirect('inventory')
        target_workshop = profile.workshop
        requested_workshop_id = request.GET.get('workshop') # Check if tech tried to view another workshop
        if requested_workshop_id and int(requested_workshop_id) != target_workshop.id:
            messages.error(request, "Access denied to selected workshop.")
            return redirect('inventory')

    department = get_object_or_404(Department, id=dept_id, workshop=target_workshop)
    equipments = Equipment.objects.filter(department=department,active_status=True).select_related('description', 'department').order_by('description__name')

    status_counts = Counter(e.status for e in equipments)
    status_summary = {
        'Working': status_counts.get('Working', 0),
        'Not_working': status_counts.get('Not working', 0),
        'Under_repair': status_counts.get('Under repair', 0),
    }

    all_workshops = []
    if profile.role == 'HOD':
        all_workshops = Workshop.objects.all().order_by('name')
    else: # Tech
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


@login_required
def export_equipment_to_excel(request):
    profile = request.user.userprofile
    workshop_to_export = None

    requested_workshop_id = request.GET.get('workshop')

    if profile.role == 'HOD':
        if requested_workshop_id:
            workshop_to_export = get_object_or_404(Workshop, id=requested_workshop_id)
        else:
            messages.error(request, "Please select a workshop to export from.")
            return redirect('inventory')
    else: # Tech
        if not profile.workshop:
            messages.error(request, "Your profile is not associated with a workshop. Cannot export.")
            return redirect('inventory')
        workshop_to_export = profile.workshop
        if requested_workshop_id and int(requested_workshop_id) != workshop_to_export.id:
            messages.error(request, "Unauthorized to export from selected workshop.")
            return redirect('inventory')

    department_id = request.GET.get('department')
    filename = f"equipment_inventory_{workshop_to_export.name}.xlsx" if workshop_to_export else "equipment_inventory.xlsx"
    export_title = f"{workshop_to_export.name} Equipment Inventory" if workshop_to_export else "Equipment Inventory"

    try:
        if department_id:
            department = get_object_or_404(Department, id=department_id, workshop=workshop_to_export)
            equipments = Equipment.objects.filter(
                department=department,active_status=True
            ).select_related('department', 'description')
            filename = f"{department.name}_equipment_inventory.xlsx"
            export_title = f"{department.name} - Equipment Inventory ({workshop_to_export.name})"
        else:
            equipments = Equipment.objects.filter(
                department__workshop=workshop_to_export,active_status=True
            ).select_related('department', 'description')

    except Exception as e:
        messages.error(request, "Error fetching equipment data for export.")
        return redirect('inventory')

    if not equipments.exists():
        messages.warning(request, "No equipment found to export.")
        return redirect('inventory')

    template_path = os.path.join(settings.STATIC_ROOT, 'equipment_template.xlsx')
    if not os.path.exists(template_path):
        messages.error(request, "Excel template not found.")
        return redirect('inventory')

    try:
        wb = load_workbook(template_path)
        ws = wb.active
        ws['A1'].value = export_title
    except Exception as e:
        messages.error(request, "Error loading Excel template.")
        return redirect('inventory')

    row = 3
    for item in equipments:
        ws.cell(row=row, column=1).value = item.description.name if item.description else ""
        ws.cell(row=row, column=2).value = str(item.manufacturer) if item.manufacturer else ""
        ws.cell(row=row, column=3).value = item.model or ""
        ws.cell(row=row, column=4).value = item.serial_number or ""
        ws.cell(row=row, column=5).value = item.department.name if item.department else ""
        ws.cell(row=row, column=6).value = item.get_status_display() if hasattr(item, 'get_status_display') else str(item.status or "")

        row += 1

    user_name = request.user.first_name or request.user.username
    ws.cell(row=row, column=1).value = f"Generated by: {user_name}"
    ws.merge_cells(f'A{row}:F{row}')
    ws[f'A{row}'].font = Font(bold=True, size=12)
    ws[f'A{row}'].alignment = Alignment(horizontal='center')

    headers = ["Description", "Manufacturer", "Model", "Serial Number", "Department", "Status"]
    for col_num in range(1, len(headers) + 1):
        max_length = 0
        column_letter = get_column_letter(col_num)
        for row_num in range(2, ws.max_row + 1):
            cell = ws.cell(row=row_num, column=col_num)
            if cell.value:
                try:
                    max_length = max(max_length, len(str(cell.value)))
                except:
                    pass
            adjusted_width = max(max_length + 2, 3)
            ws.column_dimensions[column_letter].width = adjusted_width

    try:
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        wb.save(response)
        return response
    except Exception as e:
        messages.error(request, "Error generating Excel file.")
        return redirect('inventory')


@login_required
def inventory_summary(request):
    profile = request.user.userprofile
    workshop_to_summarize = None
    template_name = 'Inventory/inventory_summary.html' # Assuming a separate summary template

    requested_workshop_id = request.GET.get('workshop')

    if profile.role == 'HOD':
        # HODs can view summary for any workshop
        if requested_workshop_id:
            workshop_to_summarize = get_object_or_404(Workshop, id=requested_workshop_id)
        else:
            messages.error(request, "Please select a workshop to view the summary.")
            return redirect('inventory') # Redirect to inventory, which will handle HOD view
    else: # Tech
        if not profile.workshop:
            messages.error(request, "Your profile is not associated with a workshop.")
            return redirect('inventory')
        workshop_to_summarize = profile.workshop
        if requested_workshop_id and int(requested_workshop_id) != workshop_to_summarize.id:
            messages.error(request, "Unauthorized to view summary for selected workshop.")
            return redirect('inventory')

    department_filter = request.GET.get('department', '')
    description_filter = request.GET.get('description', '')

    base_queryset = Equipment.objects.filter(department__workshop=workshop_to_summarize,active_status=True)

    filtered_queryset = base_queryset
    if department_filter:
        filtered_queryset = filtered_queryset.filter(department__id=department_filter)
    if description_filter:
        filtered_queryset = filtered_queryset.filter(description__id=description_filter)

    summary_data = filtered_queryset.values(
        'description__name',
        'description__id'
    ).annotate(
        total_count=Count('id'),
        working_count=Count(
            Case(When(status='Working', then=1), output_field=IntegerField())
        ),
        not_working_count=Count(
            Case(When(status='Not working', then=1), output_field=IntegerField())
        ),
        under_repair_count=Count(
            Case(When(status='Under repair', then=1), output_field=IntegerField())
        )
    ).order_by('description__name')

    overall_totals = {
        'total_equipment': filtered_queryset.count(),
        'total_working': filtered_queryset.filter(status='Working').count(),
        'total_not_working': filtered_queryset.filter(status='Not working').count(),
        'total_under_repair': filtered_queryset.filter(status='Under repair').count(),
    }

    departments = Department.objects.filter(workshop=workshop_to_summarize).order_by('name')
    equipment_descriptions = EquipmentDescription.objects.filter(
    equipment__department__workshop=workshop_to_summarize,equipment__active_status=True
    ).distinct().order_by('name')

    current_department = None
    current_description = None

    if department_filter:
        try:
            current_department = get_object_or_404(Department, id=department_filter, workshop=workshop_to_summarize)
        except Department.DoesNotExist:
            pass

    if description_filter:
        try:
            current_description = get_object_or_404(EquipmentDescription, id=description_filter)
        except EquipmentDescription.DoesNotExist:
            pass

    all_workshops = []
    if profile.role == 'HOD':
        all_workshops = Workshop.objects.all().order_by('name')
    else: # Tech
        all_workshops = [profile.workshop] if profile.workshop else []


    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'summary_data': summary_data,
        'overall_totals': overall_totals,
        'departments': departments,
        'equipment_descriptions': equipment_descriptions,
        'current_department': current_department,
        'current_description': current_description,
        'department_filter': department_filter,
        'description_filter': description_filter,
        'all_workshops': all_workshops,
        'selected_workshop': workshop_to_summarize,
    }

    return render(request, template_name, context)


@login_required
def create_department(request, workshop_id=None):
    profile = request.user.userprofile
    target_workshop = None

    if profile.role == 'HOD':
        if workshop_id:
            target_workshop = get_object_or_404(Workshop, id=workshop_id)
        else:
            messages.error(request, "HODs must select a workshop to create a department.")
            return redirect('hod_dashboard')
    elif profile.role == 'Tech':
        target_workshop = profile.workshop
        if not target_workshop:
            messages.error(request, "Your profile is not associated with a workshop. Cannot create department.")
            return redirect('create_department')
    else:
        messages.error(request, "Unauthorized to create departments.")
        return redirect('create_department')

    if request.method == 'POST':
        name = request.POST.get('department_name')

        # Check if department already exists in the selected workshop
        if Department.objects.filter(name__iexact=name, workshop=target_workshop).exists():
            messages.error(request, f"Department '{name}' already exists in {target_workshop.name}.")
        else:
            try:
                # Create the department, assigning it to the determined workshop
                Department.objects.create(name=name, workshop=target_workshop)
                messages.success(
                    request,
                    f"Department '{name}' created successfully in {target_workshop.name}.",
                    extra_tags="success create task"
                )

                # Redirect based on user role
                if profile.role == 'HOD':
                    return redirect('create_department_for_hod', workshop_id=target_workshop.id)
                else:  # Tech
                    return redirect('create_department')
            except ValidationError as e:
                error_msg = e.message_dict.get('__all__', e.messages)[0] if hasattr(e, 'message_dict') else str(e)
                messages.error(request, error_msg)
            except Exception as e:
                messages.error(request, f"An unexpected error occurred: {e}")

    # ✅ MOVED OUTSIDE POST BLOCK - Get departments with equipment count for the template
    from django.db.models import Prefetch

    departments_with_count = Department.objects.filter(
        workshop=target_workshop
    ).prefetch_related(
        Prefetch(
            'equipment_set',
            queryset=Equipment.objects.filter(active_status=True),
            to_attr='active_equipment'
        )
    ).order_by('name')

    # For GET request or if there was an error
    return render(request, 'Inventory/create_department.html', {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'selected_workshop': target_workshop,
        'departments_in_workshop': departments_with_count
    })
@login_required
def edit_department(request, dept_id):
    profile = request.user.userprofile
    department = get_object_or_404(Department, id=dept_id)
    target_workshop = department.workshop

    # Permission checks
    if profile.role == 'HOD':
        if not target_workshop:
            messages.error(request, "Department is not associated with a valid workshop.")
            return redirect('inventory')
    elif profile.role == 'Tech':
        if not profile.workshop or profile.workshop != target_workshop:
            messages.error(request, "Unauthorized to edit departments in this workshop.")
            return redirect('inventory')
    else:
        messages.error(request, "Unauthorized to edit departments.")
        return redirect('create_department')

    if request.method == 'POST':
        name = request.POST.get('department_name', '').strip()

        if not name:
            messages.error(request, "Department name is required.")
        elif Department.objects.filter(name__iexact=name, workshop=target_workshop).exclude(id=dept_id).exists():
            messages.error(request, f"Department '{name}' already exists in {target_workshop.name}.")
        else:
            try:
                old_name = department.name
                department.name = name
                department.full_clean()
                department.save()
                messages.success(
                    request,
                    f"Department '{old_name}' updated to '{name}' successfully.",
                    extra_tags="success update task"
                )


            except ValidationError as e:
                error_msg = e.message_dict.get('__all__', e.messages)[0] if hasattr(e, 'message_dict') else str(e)
                messages.error(request, error_msg)
            except Exception as e:
                messages.error(request, f"An unexpected error occurred: {e}")

    # Always redirect back to the appropriate page after edit attempt - FIXED THIS PART
    if profile.role == 'HOD':
        return redirect('create_department_for_hod', workshop_id=target_workshop.id)
    else:
        return redirect('create_department')

@login_required
@require_http_methods(["POST"])
def delete_department(request, dept_id):
    profile = request.user.userprofile
    department = get_object_or_404(Department, id=dept_id)
    target_workshop = department.workshop

    # Permission checks
    if profile.role == 'HOD':
        if not target_workshop:
            messages.error(request, "Department is not associated with a valid workshop.")
            return redirect('create_department')
    elif profile.role == 'Tech':
        if not profile.workshop or profile.workshop != target_workshop:
            messages.error(request, "Unauthorized to delete departments in this workshop.")
            return redirect('create_department')
    else:
        messages.error(request, "Unauthorized to delete departments.")
        return redirect('create_department')

    try:
        department_name = department.name

        # Count associated equipment before marking for deletion
        equipment_count = Equipment.objects.filter(department=department, pending_delete=False,active_status=True ).count()

        if equipment_count > 0:
            # Mark all equipment in the department as pending_delete
            equipment_to_delete = Equipment.objects.filter(department=department, pending_delete=False)
            equipment_names = [f"{eq.description.name} ({eq.serial_number})" for eq in equipment_to_delete[:5]]  # Show first 5

            # Update equipment to pending_delete
            equipment_to_delete.update(pending_delete=True, updated_at=timezone.now())

            if equipment_count <= 5:
                equipment_list = ", ".join(equipment_names)
                messages.success(
                    request,
                    f"Department '{department_name}' and {equipment_count} equipment items marked for deletion (will sync soon): {equipment_list}",
                    extra_tags="success delete task"
                )
            else:
                equipment_list = ", ".join(equipment_names)
                messages.success(
                    request,
                    f"Department '{department_name}' and {equipment_count} equipment items marked for deletion (will sync soon). First 5: {equipment_list}...",
                    extra_tags="success delete task"
                )
        else:
            messages.success(
                request,
                f"Department '{department_name}' marked for deletion (will sync soon).",
                extra_tags="success delete task"
            )

        # Instead of deleting, mark department as pending_delete
        department.pending_delete = True
        department.updated_at = timezone.now()
        department.save(update_fields=['pending_delete', 'updated_at'])

    except Exception as e:
        messages.error(request, f"An error occurred while marking the department for deletion: {e}")

    # Redirect based on user role
    if profile.role == 'HOD':
        return redirect('create_department_for_hod', workshop_id=target_workshop.id)
    return redirect('create_department')
@login_required
@require_http_methods(["POST"])
def transfer_department(request, dept_id):
    """Transfer a department to another workshop"""
    profile = request.user.userprofile
    department = get_object_or_404(Department, id=dept_id)
    current_workshop = department.workshop

    # Permission checks - only HODs can transfer departments
    if profile.role != 'HOD':
        messages.error(request, "Only Heads of Department can transfer departments between workshops.")
        return redirect('create_department')

    target_workshop_id = request.POST.get('target_workshop')

    if not target_workshop_id:
        messages.error(request, "Please select a target workshop.")
        return redirect('create_department_for_hod', workshop_id=current_workshop.id)

    try:
        target_workshop = get_object_or_404(Workshop, id=target_workshop_id)

        # Check if department with same name already exists in target workshop
        if Department.objects.filter(name__iexact=department.name, workshop=target_workshop).exists():
            messages.error(request,
                f"A department named '{department.name}' already exists in {target_workshop.name}. "
                f"Please rename the department before transferring or choose a different workshop.")
            return redirect('create_department_for_hod', workshop_id=current_workshop.id)

        # ✅ Count ALL equipment before transfer (including inactive)
        equipment_count = Equipment.objects.filter(department=department).count()

        # Perform the transfer
        old_workshop_name = current_workshop.name
        department.workshop = target_workshop
        department.full_clean()
        department.save()

        # Success message
        if equipment_count > 0:
            messages.success(
                request,
                f"Department '{department.name}' and {equipment_count} equipment items "
                f"successfully transferred from {old_workshop_name} to {target_workshop.name}.",
                extra_tags="success task"
            )
        else:
            messages.success(
                request,
                f"Department '{department.name}' successfully transferred from "
                f"{old_workshop_name} to {target_workshop.name}.",
                extra_tags="success task"
            )

    except ValidationError as e:
        error_msg = e.message_dict.get('__all__', e.messages)[0] if hasattr(e, 'message_dict') else str(e)
        messages.error(request, f"Transfer failed: {error_msg}")
    except Exception as e:
        messages.error(request, f"An unexpected error occurred during transfer: {e}")

    # Redirect back to the original workshop's department management page
    return redirect('create_department_for_hod', workshop_id=current_workshop.id)

@login_required
def export_inventory_summary_excel(request):
    profile = request.user.userprofile
    workshop_to_export = None

    requested_workshop_id = request.GET.get('workshop')

    if profile.role == 'HOD':
        if requested_workshop_id:
            workshop_to_export = get_object_or_404(Workshop, id=requested_workshop_id)
        else:
            messages.error(request, "Please select a workshop to export summary from.")
            return redirect('inventory_summary')
    else: # Tech
        if not profile.workshop:
            messages.error(request, "Your profile is not associated with a workshop. Cannot export summary.")
            return redirect('inventory_summary')
        workshop_to_export = profile.workshop
        if requested_workshop_id and int(requested_workshop_id) != workshop_to_export.id:
            messages.error(request, "Unauthorized to export summary from selected workshop.")
            return redirect('inventory_summary')

    department_filter = request.GET.get('department', '')
    description_filter = request.GET.get('description', '')

    base_queryset = Equipment.objects.filter(department__workshop=workshop_to_export,active_status=True)
    filtered_queryset = base_queryset
    filename = f"inventory_summary_{workshop_to_export.name}.xlsx" if workshop_to_export else "inventory_summary.xlsx"

    if department_filter:
        try:
            department = get_object_or_404(Department, id=department_filter, workshop=workshop_to_export)
            filtered_queryset = filtered_queryset.filter(department=department)
            filename = f"{department.name}_inventory_summary.xlsx"
        except Department.DoesNotExist:
            pass

    if description_filter:
        try:
            description = get_object_or_404(EquipmentDescription, id=description_filter)
            filtered_queryset = filtered_queryset.filter(description=description)
            if department_filter:
                filename = f"{department.name}_{description.name}_summary.xlsx"
            else:
                filename = f"{description.name}_inventory_summary.xlsx"
        except EquipmentDescription.DoesNotExist:
            pass

    summary_data = filtered_queryset.values(
        'description__name'
    ).annotate(
        total_count=Count('id'),
        working_count=Count(
            Case(When(status='Working', then=1), output_field=IntegerField())
        ),
        not_working_count=Count(
            Case(When(status='Not working', then=1), output_field=IntegerField())
        ),
        under_repair_count=Count(
            Case(When(status='Under repair', then=1), output_field=IntegerField())
        )
    ).order_by('description__name')

    wb = Workbook()
    ws = wb.active
    ws.title = "Inventory Summary"

    headers = ['Equipment Description', 'Working', 'Not Working', 'Under Repair', 'Total']
    for col_num, header in enumerate(headers, 1):
        col_letter = get_column_letter(col_num)
        ws[f'{col_letter}1'] = header
        ws[f'{col_letter}1'].font = Font(bold=True)
        ws[f'{col_letter}1'].alignment = Alignment(horizontal='center')

    row_num = 2
    total_working = 0
    total_not_working = 0
    total_under_repair = 0
    total_equipment = 0

    for item in summary_data:
        ws[f'A{row_num}'] = item['description__name']
        ws[f'B{row_num}'] = item['working_count']
        ws[f'C{row_num}'] = item['not_working_count']
        ws[f'D{row_num}'] = item['under_repair_count']
        ws[f'E{row_num}'] = item['total_count']

        total_working += item['working_count']
        total_not_working += item['not_working_count']
        total_under_repair += item['under_repair_count']
        total_equipment += item['total_count']

        row_num += 1

    ws[f'A{row_num}'] = "TOTAL"
    ws[f'B{row_num}'] = total_working
    ws[f'C{row_num}'] = total_not_working
    ws[f'D{row_num}'] = total_under_repair
    ws[f'E{row_num}'] = total_equipment

    for col in range(1, 6):
        col_letter = get_column_letter(col)
        ws[f'{col_letter}{row_num}'].font = Font(bold=True)

    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = (max_length + 2) * 1.2
        ws.column_dimensions[column].width = adjusted_width

    row_num += 2
    user_name = request.user.first_name or request.user.username
    ws[f'A{row_num}'] = f"Generated by: {user_name}"
    ws[f'A{row_num}'].font = Font(bold=True)

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)

    return response

@login_required
def export_equipment_to_pdf(request):
    profile = request.user.userprofile
    workshop_to_export = None

    # Determine workshop
    requested_workshop_id = request.GET.get('workshop')
    if profile.role == 'HOD':
        if requested_workshop_id:
            workshop_to_export = get_object_or_404(Workshop, id=requested_workshop_id)
        else:
            messages.error(request, "Please select a workshop to export from.")
            return redirect('inventory')
    else:
        if not profile.workshop:
            messages.error(request, "Your profile is not associated with a workshop.")
            return redirect('inventory')
        workshop_to_export = profile.workshop
        # ✅ FIX: Compare UUID strings directly, don't convert to int
        if requested_workshop_id and str(requested_workshop_id) != str(workshop_to_export.id):
            messages.error(request, "Unauthorized to export from selected workshop.")
            return redirect('inventory')

    # --- Apply same filters as inventory view ---
    equipments = Equipment.objects.filter(
        department__workshop=workshop_to_export,
        active_status=True
    ).select_related('department', 'description', 'manufacturer')

    department_filter = request.GET.get('department', '').strip()
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()

    current_department = None
    if department_filter:
        try:
            current_department = Department.objects.get(id=department_filter, workshop=workshop_to_export)
            equipments = equipments.filter(department=current_department)
        except Department.DoesNotExist:
            pass

    if search_query:
        equipments = equipments.filter(
            Q(description__name__icontains=search_query) |
            Q(manufacturer__name__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(department__name__icontains=search_query)
        )

    if status_filter and status_filter in ['Working', 'Not working', 'Under repair']:
        equipments = equipments.filter(status=status_filter)

    # --- If no results ---
    if not equipments.exists():
        messages.warning(request, "No equipment found to export.")
        return redirect('inventory')

    # --- Build context ---
    context = {
        'workshop': workshop_to_export,
        'department': current_department,
        'status_filter': status_filter,
        'total_equipment': equipments.count(),
        'generated_by': f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username,
        'notes': request.GET.get('notes', ''),
    }

    # --- Generate PDF ---
    try:
        return create_equipment_pdf_response(
            workshop_to_export,
            equipments,
            request.GET.get('report_type', 'detailed'),
            context,
            request
        )
    except Exception as e:
        logger.error(f"PDF generation failed: {str(e)}")
        messages.error(request, "Failed to generate PDF report. Please try again.")
        return redirect('inventory')

@login_required
def export_inventory_summary_to_pdf(request):
    """Export inventory summary to PDF format"""
    profile = request.user.userprofile
    workshop_to_export = None

    # Workshop selection logic (same as existing summary export)
    requested_workshop_id = request.GET.get('workshop')

    if profile.role == 'HOD':
        if requested_workshop_id:
            workshop_to_export = get_object_or_404(Workshop, id=requested_workshop_id)
        else:
            messages.error(request, "Please select a workshop to export summary from.")
            return redirect('inventory_summary')
    else: # Tech
        if not profile.workshop:
            messages.error(request, "Your profile is not associated with a workshop. Cannot export summary.")
            return redirect('inventory_summary')
        workshop_to_export = profile.workshop
        if requested_workshop_id and int(requested_workshop_id) != workshop_to_export.id:
            messages.error(request, "Unauthorized to export summary from selected workshop.")
            return redirect('inventory_summary')

    # Get filters
    department_filter = request.GET.get('department', '')
    description_filter = request.GET.get('description', '')

    # Build queryset with filters
    base_queryset = Equipment.objects.filter(department__workshop=workshop_to_export,active_status=True)
    filtered_queryset = base_queryset

    current_department = None
    current_description = None

    if department_filter:
        try:
            current_department = get_object_or_404(Department, id=department_filter, workshop=workshop_to_export)
            filtered_queryset = filtered_queryset.filter(department=current_department)
        except Department.DoesNotExist:
            pass

    if description_filter:
        try:
            current_description = get_object_or_404(EquipmentDescription, id=description_filter)
            filtered_queryset = filtered_queryset.filter(description=current_description)
        except EquipmentDescription.DoesNotExist:
            pass

    if not filtered_queryset.exists():
        messages.warning(request, "No equipment found matching the selected criteria.")
        return redirect('inventory_summary')

    # Prepare context
    context = {
        'workshop': workshop_to_export,
        'department': current_department,
        'description': current_description,
        'generated_by': f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username,
    }

    # Generate PDF using summary report type
    try:
        return create_equipment_pdf_response(
            workshop_to_export,
            filtered_queryset,
            'summary',
            context,
            request
        )
    except Exception as e:
        logger.error(f"Summary PDF generation failed: {str(e)}")
        messages.error(request, "Failed to generate summary PDF report. Please try again.")
        return redirect('inventory_summary')


@login_required
def equipment_reports_dashboard(request):
    """Dashboard for choosing different PDF report types"""
    profile = request.user.userprofile

    # Determine available workshops
    if profile.role == 'HOD':
        all_workshops = Workshop.objects.all().order_by('name')
        selected_workshop_id = request.GET.get('workshop')
        if selected_workshop_id:
            try:
                selected_workshop = get_object_or_404(Workshop, id=selected_workshop_id)
            except (Workshop.DoesNotExist, ValueError):
                selected_workshop = all_workshops.first() if all_workshops.exists() else None
        else:
            selected_workshop = all_workshops.first() if all_workshops.exists() else None
    else: # Tech
        if not profile.workshop:
            messages.error(request, "Your profile is not associated with a workshop.")
            return redirect('inventory')
        selected_workshop = profile.workshop
        all_workshops = [selected_workshop]

    if not selected_workshop:
        messages.warning(request, "No workshops available.")
        return render(request, 'Inventory/equipment_reports.html', {
            'show_sidebar': True,  # Enable sidebar with hamburger menu
            'all_workshops': [],
            'selected_workshop': None,
            'departments': [],
            'report_types': [],
        })

    # Get departments for the selected workshop
    departments = Department.objects.filter(workshop=selected_workshop).order_by('name')

    # Get equipment statistics for the dashboard
    total_equipment = Equipment.objects.filter(department__workshop=selected_workshop,active_status=True).count()
    status_stats = Equipment.objects.filter(
        department__workshop=selected_workshop,active_status=True
    ).values('status').annotate(count=Count('status'))

    # Define available report types
    report_types = [
        {
            'key': 'detailed',
            'name': 'Detailed Equipment List',
            'description': 'Complete listing of all equipment with full details',
            'icon': 'fas fa-list-ul'
        },
        {
            'key': 'summary',
            'name': 'Equipment Summary',
            'description': 'Summary report grouped by equipment type',
            'icon': 'fas fa-chart-pie'
        },
        {
            'key': 'department',
            'name': 'Department Analysis',
            'description': 'Equipment distribution and status by department',
            'icon': 'fas fa-building'
        },
        {
            'key': 'status',
            'name': 'Status Analysis',
            'description': 'Comprehensive analysis of equipment conditions',
            'icon': 'fas fa-stethoscope'
        }
    ]

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        'all_workshops': all_workshops,
        'selected_workshop': selected_workshop,
        'departments': departments,
        'report_types': report_types,
        'total_equipment': total_equipment,
        'status_stats': {item['status']: item['count'] for item in status_stats},
        'user_role': profile.role,
    }

    return render(request, 'Inventory/equipment_reports.html', context)

# Helper view for AJAX PDF generation status
@login_required
def check_pdf_generation_status(request):
    """Check if PDF generation is possible for current filters"""
    profile = request.user.userprofile
    workshop_id = request.GET.get('workshop')
    department_id = request.GET.get('department')
    status_filter = request.GET.get('status')

    try:
        # Determine workshop
        if profile.role == 'HOD':
            if not workshop_id:
                return JsonResponse({'available': False, 'message': 'Please select a workshop'})
            workshop = get_object_or_404(Workshop, id=workshop_id)
        else:
            workshop = profile.workshop
            if not workshop:
                return JsonResponse({'available': False, 'message': 'No workshop assigned to your profile'})

        # Build queryset to check availability
        queryset = Equipment.objects.filter(department__workshop=workshop,active_status=True)

        if department_id:
            queryset = queryset.filter(department_id=department_id)

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        count = queryset.count()

        return JsonResponse({
            'available': count > 0,
            'equipment_count': count,
            'message': f'{count} equipment items available for export' if count > 0 else 'No equipment found matching criteria'
        })

    except Exception as e:
        return JsonResponse({'available': False, 'message': 'Error checking availability'})


# Bulk PDF generation for multiple departments (HOD only)
@login_required
def bulk_export_departments_pdf(request):
    """Generate separate PDF reports for each department in a workshop"""
    profile = request.user.userprofile

    if profile.role != 'HOD':
        messages.error(request, "Only Heads of Department can perform bulk exports.")
        return redirect('inventory')

    workshop_id = request.GET.get('workshop')
    if not workshop_id:
        messages.error(request, "Please select a workshop for bulk export.")
        return redirect('equipment_reports_dashboard')

    try:
        workshop = get_object_or_404(Workshop, id=workshop_id)
        departments = Department.objects.filter(workshop=workshop).order_by('name')

        if not departments.exists():
            messages.warning(request, f"No departments found in {workshop.name}.")
            return redirect('equipment_reports_dashboard')

        # Create a ZIP file containing multiple PDFs
        import zipfile
        from django.http import HttpResponse
        import tempfile

        # Create temporary file for ZIP
        with tempfile.NamedTemporaryFile(delete=False) as tmp_zip:
            with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED) as zip_file:

                for department in departments:
                    equipment_queryset = Equipment.objects.filter(
                        department=department,active_status=True
                    ).select_related('description', 'manufacturer')

                    if equipment_queryset.exists():
                        # Generate PDF for this department
                        context = {
                            'workshop': workshop,
                            'department': department,
                            'generated_by': f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username,
                        }

                        pdf_buffer = generate_equipment_pdf_report(
                            workshop, equipment_queryset, 'detailed', context
                        )

                        # Add to ZIP
                        filename = f"{department.name.replace(' ', '_')}_equipment_report.pdf"
                        zip_file.writestr(filename, pdf_buffer.getvalue())
                        pdf_buffer.close()

            # Prepare response
            tmp_zip.seek(0)
            response = HttpResponse(tmp_zip.read(), content_type='application/zip')
            response['Content-Disposition'] = f'attachment; filename="{workshop.name.replace(" ", "_")}_all_departments_reports.zip"'

            # Clean up
            import os
            os.unlink(tmp_zip.name)

            return response

    except Exception as e:
        logger.error(f"Bulk PDF export failed: {str(e)}")
        messages.error(request, "Failed to generate bulk PDF reports. Please try again.")
        return redirect('equipment_reports_dashboard')




def discover_department_dependencies():
    """Automatically discover all models that have ForeignKey to Department"""
    department_models = []

    for app_config in apps.get_app_configs():
        for model in app_config.get_models():
            for field in model._meta.get_fields():
                if isinstance(field, (models.ForeignKey, models.OneToOneField)):
                    if hasattr(field, 'related_model') and field.related_model == Department:
                        is_nullable = field.null if hasattr(field, 'null') else False

                        department_models.append({
                            'app_label': app_config.label,
                            'model_name': model.__name__,
                            'field_name': field.name,
                            'verbose_name': model._meta.verbose_name,
                            'model': model,
                            'is_nullable': is_nullable,
                            'on_delete': getattr(field.remote_field, 'on_delete', None).__name__ if hasattr(field.remote_field, 'on_delete') else 'UNKNOWN'
                        })
                        logger.debug(f"✅ Found FK: {app_config.label}.{model.__name__}.{field.name} (nullable={is_nullable})")

    logger.info(f"🔍 Total discovered models with Department FK/O2O: {len(department_models)}")
    return department_models


def get_department_dependencies_count(department):
    """Calculate total dependencies for a department using auto-discovery"""
    count = 0
    logger.info(f"🔍 Counting dependencies for department: {department.name} (ID: {department.id})")

    department_models = discover_department_dependencies()

    for model_info in department_models:
        try:
            model = model_info['model']
            field_name = model_info['field_name']

            # ✅ Count ALL records (including inactive) for transfer purposes
            field_count = model.objects.filter(**{field_name: department}).count()

            count += field_count

            if field_count > 0:
                logger.info(f"   📊 {model_info['app_label']}.{model_info['model_name']}.{field_name}: {field_count} records")

        except Exception as e:
            logger.warning(f"   ⚠️ Error checking {model_info['app_label']}.{model_info['model_name']}: {e}")
            continue

    logger.info(f"📈 Total dependencies: {count}")
    return count

def transfer_department_dependencies_auto(from_department, to_department):
    """Automatically discover and transfer all Department dependencies"""
    logger.info(f"🔄 AUTO TRANSFER START: {from_department.name} → {to_department.name if to_department else 'NULL'}")

    transferred_count = 0
    set_null_count = 0
    transfer_results = []
    updated_records = []

    department_models = discover_department_dependencies()
    logger.info(f"🔍 Discovered {len(department_models)} models with Department FK")

    for model_info in department_models:
        try:
            model = model_info['model']
            field_name = model_info['field_name']
            is_nullable = model_info['is_nullable']
            on_delete_behavior = model_info['on_delete']

            # ❌ REMOVE active_status filter for transfers - transfer ALL records
            before_count = model.objects.filter(**{field_name: from_department}).count()
            affected_records = list(model.objects.filter(**{field_name: from_department}))

            if before_count > 0:
                logger.info(f"📊 {model_info['model_name']}.{field_name}: {before_count} records found")

                if is_nullable and on_delete_behavior == 'SET_NULL' and not to_department:
                    logger.info(f"   🔄 Setting {field_name} to NULL for {before_count} records")
                    updated = model.objects.filter(**{field_name: from_department}).update(
                        **{field_name: None, 'updated_at': timezone.now()}
                    )
                    set_null_count += updated
                    action = 'SET_NULL'
                else:
                    if to_department:
                        logger.info(f"   🔄 Transferring {before_count} records to {to_department.name}")
                        # ✅ Transfer ALL records (active and inactive)
                        updated = model.objects.filter(**{field_name: from_department}).update(
                            **{field_name: to_department, 'updated_at': timezone.now()}
                        )
                        transferred_count += updated
                        action = 'TRANSFER'
                    else:
                        logger.error(f"   ❌ Cannot transfer non-nullable field without target department")
                        updated = 0
                        action = 'ERROR'

                if updated > 0:
                    for record in affected_records:
                        updated_records.append((model, record))

                after_count = model.objects.filter(**{field_name: from_department}).count()

                transfer_results.append({
                    'model': f"{model_info['app_label']}.{model_info['model_name']}",
                    'field': field_name,
                    'before': before_count,
                    'updated': updated,
                    'after': after_count,
                    'action': action,
                    'success': updated == before_count and after_count == 0
                })

                if updated == before_count and after_count == 0:
                    logger.info(f"   ✅ SUCCESS: {action} {updated} records")
                else:
                    logger.error(f"   ❌ FAILED: Expected {before_count}, got {updated}, remaining {after_count}")

        except Exception as e:
            logger.error(f"   💥 Error processing {model_info['model_name']}: {e}")
            transfer_results.append({
                'model': f"{model_info['app_label']}.{model_info['model_name']}",
                'field': model_info['field_name'],
                'before': 0,
                'updated': 0,
                'after': 0,
                'action': 'ERROR',
                'success': False,
                'error': str(e)
            })

    # Save all updated records to trigger sync
    logger.info(f"💾 Saving {len(updated_records)} updated records to trigger sync...")
    for model, record in updated_records:
        try:
            record.updated_at = timezone.now()
            record.save(update_fields=['updated_at'])
        except Exception as e:
            logger.warning(f"   ⚠️ Failed to save {model.__name__} record {record.pk}: {e}")

    logger.info(f"🎉 AUTO TRANSFER COMPLETE: {transferred_count} transferred, {set_null_count} set to NULL")
    return transferred_count + set_null_count, transfer_results

@login_required
@require_http_methods(["GET"])
def get_department_dependency_count(request, dept_id):
    """API endpoint to get dependency count for a department"""
    try:
        department = get_object_or_404(Department, id=dept_id)
        count = get_department_dependencies_count(department)

        return JsonResponse({
            'count': count,
            'department_name': department.name,
            'status': 'success'
        })
    except Exception as e:
        logger.error(f"Error getting dependency count: {str(e)}")
        return JsonResponse({
            'count': 0,
            'status': 'error',
            'message': str(e)
        })


@login_required
@require_http_methods(["POST"])
def transfer_department_dependencies(request, dept_id):
    """Transfer all dependencies from one department to another"""
    profile = request.user.userprofile
    source_department = get_object_or_404(Department, id=dept_id)
    target_workshop = source_department.workshop

    # Permission checks
    if profile.role == 'HOD':
        if not target_workshop:
            return JsonResponse({
                'success': False,
                'error': 'Department is not associated with a valid workshop.'
            }, status=400)
    elif profile.role == 'Tech':
        if not profile.workshop or profile.workshop != target_workshop:
            return JsonResponse({
                'success': False,
                'error': 'Unauthorized to transfer dependencies in this workshop.'
            }, status=403)
    else:
        return JsonResponse({
            'success': False,
            'error': 'Unauthorized to transfer dependencies.'
        }, status=403)

    target_department_id = request.POST.get('target_department_id')

    logger.info(f"🔄 TRANSFER REQUEST for: {source_department.name} (ID: {source_department.id})")
    logger.info(f"🎯 Transfer to: {target_department_id or 'NULL (for nullable fields)'}")

    # Count dependencies
    dependency_count = get_department_dependencies_count(source_department)
    logger.info(f"🧮 Dependencies found: {dependency_count}")

    if dependency_count == 0:
        return JsonResponse({
            'success': True,
            'message': 'No dependencies to transfer',
            'transferred_count': 0
        })

    department_models = discover_department_dependencies()
    nullable_fields_exist = any(m['is_nullable'] and m['on_delete'] == 'SET_NULL' for m in department_models)

    if dependency_count > 0 and not target_department_id and not nullable_fields_exist:
        return JsonResponse({
            'success': False,
            'error': f'Department has {dependency_count} dependencies. Please select a department to transfer them to.'
        }, status=400)

    try:
        transfer_start_time = timezone.now()

        with transaction.atomic():
            if target_department_id:
                target_department = get_object_or_404(
                    Department,
                    id=target_department_id,
                    workshop=target_workshop
                )

                # Prevent transferring to the same department
                if source_department.id == target_department.id:
                    return JsonResponse({
                        'success': False,
                        'error': 'Cannot transfer to the same department.'
                    }, status=400)

                logger.info(f"🔄 STARTING AUTO-TRANSFER: {source_department.name} → {target_department.name}")
                transferred_count, transfer_results = transfer_department_dependencies_auto(
                    source_department,
                    target_department
                )
            else:
                target_department = None
                logger.info(f"🔄 STARTING AUTO-CLEANUP: {source_department.name} → NULL")
                transferred_count = 0
                transfer_results = []

                for model_info in department_models:
                    if model_info['is_nullable'] and model_info['on_delete'] == 'SET_NULL':
                        model = model_info['model']
                        field_name = model_info['field_name']
                        before_count = model.objects.filter(**{field_name: source_department}).count()

                        if before_count > 0:
                            affected_records = list(model.objects.filter(**{field_name: source_department}))
                            updated = model.objects.filter(**{field_name: source_department}).update(
                                **{field_name: None, 'updated_at': timezone.now()}
                            )
                            transferred_count += updated

                            for record in affected_records:
                                record.updated_at = timezone.now()
                                record.save(update_fields=['updated_at'])

        # Verify transfer
        remaining_deps = get_department_dependencies_count(source_department)

        if remaining_deps > 0:
            return JsonResponse({
                'success': False,
                'error': f'Transfer incomplete! {remaining_deps} dependencies still exist.'
            }, status=400)

        logger.info(f"✅ Transfer successful! {transferred_count} records transferred")

        return JsonResponse({
            'success': True,
            'message': f'Successfully transferred {transferred_count} dependencies',
            'transferred_count': transferred_count,
            'transfer_time': transfer_start_time.isoformat()
        })

    except Department.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Target department not found.'
        }, status=404)
    except Exception as e:
        logger.error(f"❌ Transfer failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Transfer failed: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["POST"])
def delete_department(request, dept_id):
    """Delete department - expects dependencies already transferred and synced"""
    profile = request.user.userprofile
    department = get_object_or_404(Department, id=dept_id)
    target_workshop = department.workshop

    # Permission checks
    if profile.role == 'HOD':
        if not target_workshop:
            messages.error(request, "Department is not associated with a valid workshop.")
            return redirect('create_department')
    elif profile.role == 'Tech':
        if not profile.workshop or profile.workshop != target_workshop:
            messages.error(request, "Unauthorized to delete departments in this workshop.")
            return redirect('create_department')
    else:
        messages.error(request, "Unauthorized to delete departments.")
        return redirect('create_department')

    logger.info(f"🗑️ DELETE REQUEST for: {department.name} (ID: {department.id})")

    # Verify no dependencies remain locally
    dependency_count = get_department_dependencies_count(department)

    if dependency_count > 0:
        error_msg = f'Department "{department.name}" still has {dependency_count} dependencies. Please transfer them first.'
        messages.error(request, error_msg)
        logger.error(f"❌ {error_msg}")

        if profile.role == 'HOD':
            return redirect('create_department_for_hod', workshop_id=target_workshop.id)
        return redirect('create_department')

    try:
        deletion_timestamp = timezone.now()

        with transaction.atomic():
            department_name = department.name

            # Final safety check
            final_check = get_department_dependencies_count(department)
            if final_check > 0:
                error_msg = f'Safety check failed! {final_check} dependencies still exist.'
                messages.error(request, error_msg)
                logger.error(f"❌ {error_msg}")

                if profile.role == 'HOD':
                    return redirect('create_department_for_hod', workshop_id=target_workshop.id)
                return redirect('create_department')

            # Mark for deletion
            department.refresh_from_db()
            department.pending_delete = True
            department.updated_at = deletion_timestamp
            department.save(update_fields=['pending_delete', 'updated_at'])

            logger.info(f"=" * 60)
            logger.info(f"🎉 SUCCESS: Department marked for deletion!")
            logger.info(f"=" * 60)
            logger.info(f"   📛 Department: {department_name}")
            logger.info(f"   🗑️ Deletion time: {deletion_timestamp}")
            logger.info(f"   ⏱️ Frontend ensured 45s+ gap from transfers")
            logger.info(f"=" * 60)

            messages.success(
                request,
                f'Department "{department_name}" marked for deletion successfully!',
                extra_tags="success delete task"
            )

    except Exception as e:
        error_msg = f'Error marking department for deletion: {str(e)}'
        logger.error(f"❌ {error_msg}")
        import traceback
        logger.error(traceback.format_exc())
        messages.error(request, error_msg)

    # Redirect based on user role
    if profile.role == 'HOD':
        return redirect('create_department_for_hod', workshop_id=target_workshop.id)
    return redirect('create_department')

@login_required
@require_http_methods(["GET"])
def get_models_by_description(request, description_id):
    """
    API endpoint to get unique models for a specific equipment description
    Returns list of models that have been used with this description
    """
    try:
        description = get_object_or_404(EquipmentDescription, id=description_id)

        # Get unique models for this description (only active equipment)
        models = Equipment.objects.filter(
            description=description,
            active_status=True
        ).exclude(
            model__isnull=True
        ).exclude(
            model__exact=''
        ).values_list('model', flat=True).distinct().order_by('model')

        # Convert to list and remove any empty strings
        models_list = [model.strip() for model in models if model and model.strip()]

        return JsonResponse({
            'success': True,
            'description_id': str(description.id),
            'description_name': description.name,
            'models': models_list,
            'count': len(models_list)
        })

    except EquipmentDescription.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Equipment description not found'
        }, status=404)

    except Exception as e:
        logger.error(f"Error fetching models for description {description_id}: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to fetch models: {str(e)}'
        }, status=500)
