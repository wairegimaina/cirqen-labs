"""users views — user CRUD + department/workshop lookup JSON endpoints."""
import json
import base64
import logging
import uuid
import traceback
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib.auth import login, logout, update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST, require_http_methods
from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from workshop.models import Workshop
from Inventory.models import Department
from ..forms import CustomLoginForm, UserCreationForm, ForgotPasswordForm, VerifyResetCodeForm, CustomSetPasswordForm
from ..models import UserProfile, UserSecurityLog, UserSignature, UserPasswordReset
from ..utils import UserManagementUtils
from ..control import hod_required, role_required
User = get_user_model()
logger = logging.getLogger(__name__)


@login_required
@role_required('HOD', 'NIC')
@require_http_methods(["POST", "PUT"])
def api_update_user(request, user_id):
    """API endpoint to update user information (supports both JSON and multipart form data)"""
    try:

        user = get_object_or_404(User, id=user_id)
        profile = user.userprofile

        # Check permissions
        current_profile = request.user.userprofile
        if current_profile.role == 'NIC':
            if profile.role != 'Tech' or profile.workshop.department != current_profile.department:
                return JsonResponse({
                    'success': False,
                    'error': 'Permission denied'
                }, status=403)

        # Handle both JSON and multipart form data
        if request.content_type and 'multipart/form-data' in request.content_type:
            # Form data with file upload
            data = request.POST
            files = request.FILES
        else:
            # JSON data (backward compatibility)
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError as je:
                logger.warning("api_update_user: invalid JSON body: %s", je)
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid JSON data'
                }, status=400)
            files = {}

        with transaction.atomic():
            # Update user basic info
            if data.get("firstName"):
                user.first_name = data["firstName"].strip()

            if data.get("lastName"):
                user.last_name = data["lastName"].strip()

            if data.get("email"):
                email = data["email"].strip()
                # Check if email is taken by another user
                if User.objects.filter(email=email).exclude(id=user.id).exists():
                    return JsonResponse({
                        'success': False,
                        'error': 'Email already exists'
                    }, status=400)
                user.email = email

            user.save()

            # Update profile
            if data.get("role"):
                profile.role = data["role"]

            if data.get("phone"):
                profile.phone_number = data["phone"]

            # Reset role-specific fields when role changes
            old_role = profile.role
            new_role = data.get("role", old_role)

            if old_role != new_role:
                profile.department = None
                profile.workshop = None
                profile.level = None

            # Role-specific assignments
            if profile.role == "NIC":
                if data.get("department"):
                    dept_value = data["department"]

                    dept = None
                    # Try to determine if it's a UUID or a name
                    try:
                        # Check if it looks like a UUID
                        uuid.UUID(str(dept_value))
                        # It's a valid UUID, look up by ID
                        dept = Department.objects.get(id=dept_value)
                    except (ValueError, Department.DoesNotExist):
                        # Not a UUID or not found by ID, try by name
                        try:
                            dept = Department.objects.get(name=dept_value)
                        except Department.DoesNotExist:
                            return JsonResponse({
                                'success': False,
                                'error': f'Department "{dept_value}" not found'
                            }, status=400)

                    if dept:
                        profile.department = dept

            elif profile.role == "Tech":
                if data.get("level"):
                    profile.level = data["level"]

                if data.get("workshop"):
                    workshop_value = data["workshop"]

                    workshop = None
                    # Try to determine if it's a UUID or a name
                    try:
                        # Check if it looks like a UUID
                        uuid.UUID(str(workshop_value))
                        # It's a valid UUID, look up by ID
                        workshop = Workshop.objects.get(id=workshop_value)
                    except (ValueError, Workshop.DoesNotExist):
                        # Not a UUID or not found by ID, try by name
                        try:
                            workshop = Workshop.objects.get(name=workshop_value)
                        except Workshop.DoesNotExist:
                            return JsonResponse({
                                'success': False,
                                'error': f'Workshop "{workshop_value}" not found'
                            }, status=400)

                    if workshop:
                        profile.workshop = workshop

            profile.save()

            # Handle signature upload if present
            if "signature" in files:
                signature_file = files["signature"]

                # Validate file type
                if not signature_file.content_type.startswith('image/'):
                    return JsonResponse({
                        'success': False,
                        'error': 'Invalid file type. Only images are allowed.'
                    }, status=400)

                signature, _ = UserSignature.objects.get_or_create(user=user)
                try:
                    signature.save_user_drawn_signature(signature_file)
                except Exception:
                    return JsonResponse({
                        'success': False,
                        'error': 'Could not read the signature image.'
                    }, status=400)

                # Update profile flag
                profile.has_uploaded_signature = True
                profile.save()

                # Log the event
                UserSecurityLog.log_event(
                    user=user,
                    event_type="SIGNATURE_UPLOADED",
                    description=f"Signature uploaded by {request.user.username}",
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    uploaded_by=request.user.username,
                )

            # Log the update
            UserSecurityLog.log_event(
                user=user,
                event_type='ACCOUNT_UPDATED',
                description=f'Account updated by {request.user.username}',
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                updated_by=request.user.username
            )

        return JsonResponse({
            'success': True,
            'message': 'User updated successfully',
            'user': {
                'id': str(user.id),
                'firstName': user.first_name,
                'lastName': user.last_name,
                'email': user.email,
                'username': user.username,
                'role': profile.role,
                'department': profile.department.name if profile.department else None,
                'departmentId': str(profile.department.id) if profile.department else None,
                'workshop': profile.workshop.name if profile.workshop else None,
                'workshopId': str(profile.workshop.id) if profile.workshop else None,
                'level': profile.level,
                'phone': profile.phone_number or ''
            }
        })

    except ValidationError as e:
        logger.warning("api_update_user validation error: %s", e)
        return JsonResponse({
            'success': False,
            'error': 'Validation error',
            'details': str(e)
        }, status=400)
    except Exception as e:
        logger.exception("api_update_user failed")
        return JsonResponse({
            'success': False,
            'error': f'Error updating user: {str(e)}'
        }, status=500)


@login_required
def api_check_setup_status(request):
    """API endpoint to check if current user needs setup"""
    try:
        profile = request.user.userprofile
        return JsonResponse({
            'success': True,
            'needsSetup': profile.needs_first_login_setup(),
            'mustChangePassword': profile.must_change_password,
            'hasUploadedSignature': profile.has_uploaded_signature
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def get_workshops_by_department(request):
    """AJAX view to get workshops filtered by department"""
    department_id = request.GET.get('department_id')
    if department_id:
        workshops = Workshop.objects.filter(department__id=department_id).values('id', 'name')
        return JsonResponse({'workshops': list(workshops)})
    return JsonResponse({'workshops': []})


@login_required
@role_required('HOD', 'NIC')
def api_get_users(request):
    """API endpoint to get all users (JSON) with filtering - Returns only active users"""
    try:
        profile = request.user.userprofile

        # Get users based on role permissions - FILTER ONLY ACTIVE USERS
        if profile.role == 'HOD':
            users_list = UserProfile.objects.filter(
                user__active_status=True  # Only active users
            ).select_related(
                'user', 'department', 'workshop', 'created_by'
            )
        elif profile.role == 'NIC':
            users_list = UserProfile.objects.filter(
                role='Tech',
                workshop__department=profile.department,
                user__active_status=True  # Only active users
            ).select_related('user', 'department', 'workshop', 'created_by')
        else:
            users_list = UserProfile.objects.none()

        # Apply search filter
        search_query = request.GET.get('search', '').strip()
        if search_query:
            users_list = users_list.filter(
                Q(user__username__icontains=search_query) |
                Q(user__first_name__icontains=search_query) |
                Q(user__last_name__icontains=search_query) |
                Q(user__email__icontains=search_query) |
                Q(employee_id__icontains=search_query)
            )

        # Apply role filter
        role_filter = request.GET.get('role', '').strip()
        if role_filter:
            users_list = users_list.filter(role=role_filter)

        # Apply department filter
        department_filter = request.GET.get('department', '').strip()
        if department_filter:
            users_list = users_list.filter(department__id=department_filter)

        # Apply workshop filter
        workshop_filter = request.GET.get('workshop', '').strip()
        if workshop_filter:
            users_list = users_list.filter(workshop__id=workshop_filter)

        # Apply level filter (for Tech users)
        level_filter = request.GET.get('level', '').strip()
        if level_filter:
            users_list = users_list.filter(level=level_filter)

        # Sorting
        sort_by = request.GET.get('sort_by', 'username')
        sort_order = request.GET.get('sort_order', 'asc')

        valid_sort_fields = {
            'username': 'user__username',
            'firstName': 'user__first_name',
            'lastName': 'user__last_name',
            'email': 'user__email',
            'role': 'role',
            'createdAt': 'created_at',
            'employeeId': 'employee_id'
        }

        sort_field = valid_sort_fields.get(sort_by, 'user__username')
        if sort_order == 'desc':
            sort_field = f'-{sort_field}'

        users_list = users_list.order_by(sort_field)

        # Pagination
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))

        paginator = Paginator(users_list, per_page)
        users_page = paginator.get_page(page)

        # Convert to JSON format
        users_data = []
        for user_profile in users_page:
            user_data = {
                'id': str(user_profile.user.id),
                'firstName': user_profile.user.first_name,
                'lastName': user_profile.user.last_name,
                'username': user_profile.user.username,
                'email': user_profile.user.email,
                'role': user_profile.role,
                'roleDisplay': user_profile.get_role_display(),
                'employeeId': user_profile.employee_id,
                'phone': user_profile.phone_number or '',
                'isActive': user_profile.user.active_status,  # Use active_status
                'department': user_profile.department.name if user_profile.department else None,
                'departmentId': str(user_profile.department.id) if user_profile.department else None,
                'workshop': user_profile.workshop.name if user_profile.workshop else None,
                'workshopId': str(user_profile.workshop.id) if user_profile.workshop else None,
                'level': user_profile.level,
                'levelDisplay': user_profile.get_level_display() if user_profile.level else None,
                'createdAt': user_profile.created_at.isoformat(),
                'createdBy': user_profile.created_by.username if user_profile.created_by else None,
                'needsSetup': user_profile.needs_first_login_setup(),
                'hasSignature': user_profile.has_uploaded_signature
            }
            users_data.append(user_data)

        return JsonResponse({
            'success': True,
            'users': users_data,
            'pagination': {
                'total': paginator.count,
                'page': page,
                'per_page': per_page,
                'total_pages': paginator.num_pages,
                'has_next': users_page.has_next(),
                'has_previous': users_page.has_previous()
            },
            'filters': {
                'search': search_query,
                'role': role_filter,
                'department': department_filter,
                'workshop': workshop_filter,
                'level': level_filter
            }
        })

    except Exception as e:
        logger.exception("api_get_users failed")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@role_required('HOD')
@require_http_methods(["DELETE"])
def api_delete_user(request, user_id):
    """API endpoint to deactivate user account (soft delete)"""
    try:
        user = get_object_or_404(User, id=user_id)

        # Prevent self-deletion
        if user == request.user:
            return JsonResponse({
                'success': False,
                'error': 'Cannot deactivate your own account'
            }, status=400)

        username = user.username

        # Soft delete - mark as inactive using active_status field
        user.active_status = False
        user.save()

        # Log the deactivation
        UserSecurityLog.log_event(
            user=user,
            event_type='ACCOUNT_DEACTIVATED',
            description=f'Account deactivated by {request.user.username}',
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            deactivated_by=request.user.username
        )

        return JsonResponse({
            'success': True,
            'message': f'User {username} deactivated successfully'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@role_required('HOD')
@require_http_methods(["POST"])
def api_toggle_user_status(request, user_id):
    """API endpoint to activate/deactivate user account"""
    try:
        user = get_object_or_404(User, id=user_id)

        # Prevent self-deactivation
        if user == request.user:
            return JsonResponse({
                'success': False,
                'error': 'Cannot change status of your own account'
            }, status=400)

        # Toggle active status using active_status field
        user.active_status = not user.active_status
        user.save()

        # Log the status change
        UserSecurityLog.log_event(
            user=user,
            event_type='ACCOUNT_STATUS_CHANGED',
            description=f'Account {"activated" if user.active_status else "deactivated"} by {request.user.username}',
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            changed_by=request.user.username
        )

        return JsonResponse({
            'success': True,
            'message': f'User {user.username} {"activated" if user.active_status else "deactivated"} successfully',
            'isActive': user.active_status
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@role_required('HOD', 'NIC')
def api_get_departments(request):
    """API endpoint to get departments list (JSON)"""
    try:
        departments = Department.objects.all().values('id', 'name')
        # Convert UUIDs to strings
        departments_list = [
            {'id': str(dept['id']), 'name': dept['name']}
            for dept in departments
        ]
        return JsonResponse({
            'success': True,
            'departments': departments_list
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@role_required('HOD', 'NIC')
def api_get_workshops(request):
    """API endpoint to get workshops list (JSON)"""
    try:
        department_id = request.GET.get('department_id')
        if department_id:
            workshops = Workshop.objects.filter(
                department__id=department_id
            ).values('id', 'name')
        else:
            workshops = Workshop.objects.all().values('id', 'name')

        # Convert UUIDs to strings
        workshops_list = [
            {'id': str(workshop['id']), 'name': workshop['name']}
            for workshop in workshops
        ]

        return JsonResponse({
            'success': True,
            'workshops': workshops_list
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
