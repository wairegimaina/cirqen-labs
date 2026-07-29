"""JSON API endpoints for the Inventory app.

Split out of the original monolithic ``views.py``. Behaviour is unchanged; only
the location of the code moved. Analytics/summary endpoints and generic lookups
live here.
"""
import logging
from collections import Counter

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count, Case, When, IntegerField
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from Inventory.models import Department, Equipment, EquipmentDescription
from workshop.models import Workshop

logger = logging.getLogger(__name__)


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
        queryset = Equipment.objects.filter(department__workshop=workshop, active_status=True)

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
