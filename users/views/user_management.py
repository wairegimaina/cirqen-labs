"""users views — user create/manage pages and admin password reset."""
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


@require_http_methods(["POST"])
@login_required
@role_required('HOD')
def api_create_user(request):
    """Enhanced API endpoint to create new users with proper initialization"""
    try:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON data'
            }, status=400)

        # Validate required fields
        required_fields = ['firstName', 'lastName', 'email', 'role']
        for field in required_fields:
            if field not in data or not data[field].strip():
                return JsonResponse({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }, status=400)

        # Validate role
        valid_roles = dict(UserProfile.ROLE_CHOICES).keys()
        if data['role'] not in valid_roles:
            return JsonResponse({
                'success': False,
                'error': f'Invalid role. Must be one of: {", ".join(valid_roles)}'
            }, status=400)

        # Validate role-specific fields
        if data['role'] == 'NIC' and not data.get('department'):
            return JsonResponse({
                'success': False,
                'error': 'department is required for NIC role'
            }, status=400)

        if data['role'] == 'Tech':
            if not data.get('workshop'):
                return JsonResponse({
                    'success': False,
                    'error': 'Workshop is required for Tech role'
                }, status=400)
            if not data.get('level'):
                return JsonResponse({
                    'success': False,
                    'error': 'Level is required for Tech role'
                }, status=400)

        with transaction.atomic():
            # Generate username and temporary password
            username = UserManagementUtils.generate_username(data['firstName'])
            temp_password = UserManagementUtils.generate_temp_password()

            # Check if email already exists
            if User.objects.filter(email=data['email']).exists():
                return JsonResponse({
                    'success': False,
                    'error': 'Email already exists'
                }, status=400)

            # Create user
            user = User.objects.create_user(
                username=username,
                first_name=data['firstName'].strip(),
                last_name=data['lastName'].strip(),
                email=data['email'].strip(),
                password=temp_password,
                active_status=True
            )

            # Create profile with first login flags
            profile_data = {
                'user': user,
                'role': data['role'],
                'created_by': request.user,
                'must_change_password': True,  # Force password change
                'has_uploaded_signature': False  # Force signature creation
            }

            # Add role-specific assignments
            if data['role'] == 'NIC' and data.get('department'):
                try:
                    department = Department.objects.get(id=data['department'])
                    profile_data['department'] = department
                except (Department.DoesNotExist, ValueError):
                    return JsonResponse({
                        'success': False,
                        'error': 'Invalid department ID'
                    }, status=400)

            elif data['role'] == 'Tech':
                if data.get('workshop'):
                    try:
                        workshop = Workshop.objects.get(id=data['workshop'])
                        profile_data['workshop'] = workshop
                    except (Workshop.DoesNotExist, ValueError):
                        return JsonResponse({
                            'success': False,
                            'error': 'Invalid workshop ID'
                        }, status=400)

                if data.get('level'):
                    valid_levels = dict(UserProfile.LEVEL_CHOICES).keys()
                    if data['level'] not in valid_levels:
                        return JsonResponse({
                            'success': False,
                            'error': f'Invalid level. Must be one of: {", ".join(valid_levels)}'
                        }, status=400)
                    profile_data['level'] = data['level']

            profile = UserProfile.objects.create(**profile_data)

            # Send welcome email
            email_sent = UserManagementUtils.send_welcome_email(
                user, temp_password, request.user
            )
            logger.info(
                "[CREATE USER] send_welcome_email returned %s for user=%s email=%s",
                email_sent, user.username, user.email,
            )

            response_data = {
                'success': True,
                'message': 'User created successfully',
                'user': {
                    'id': str(user.id),
                    'username': user.username,
                    'firstName': user.first_name,
                    'lastName': user.last_name,
                    'email': user.email,
                    'role': profile.role,
                    'employeeId': profile.employee_id,
                    'department': profile.department.name if profile.department else None,
                    'departmentId': str(profile.department.id) if profile.department else None,
                    'workshop': profile.workshop.name if profile.workshop else None,
                    'workshopId': str(profile.workshop.id) if profile.workshop else None,
                    'level': profile.level,
                    'temporaryPassword': temp_password,
                    'needsSetup': profile.needs_first_login_setup()
                },
                'emailSent': email_sent
            }

            # Log the creation
            UserSecurityLog.log_event(
                user=user,
                event_type='ACCOUNT_CREATED',
                description=f'Account created by {request.user.username}',
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                created_by=request.user.username
            )

            return JsonResponse(response_data, status=201)

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error creating user: {str(e)}'
        }, status=500)


@login_required
@role_required('HOD')
def admin_reset_user_password(request, user_id):
    """Allow HOD to reset any user's password (admin function)"""
    target_user = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        # Generate new temporary password
        temp_password = UserManagementUtils.generate_temp_password()
        target_user.set_password(temp_password)
        target_user.save()

        # Send email with new password
        email_sent = UserManagementUtils.send_welcome_email(
            target_user, temp_password, request.user
        )

        if email_sent:
            messages.success(
                request,
                f'Password reset for {target_user.username}. '
                f'New temporary password: {temp_password}. '
                'User has been notified via email.'
            )
        else:
            messages.success(
                request,
                f'Password reset for {target_user.username}. '
                f'New temporary password: {temp_password}. '
                'Please inform the user manually as email failed to send.'
            )

        return redirect('manage_users')

    return render(request, 'users_login/Admin_Reset.html', {
        'show_sidebar': True,  # Enable hamburger menu
        'target_user': target_user,
        'title': f'Reset Password for {target_user.get_full_name() or target_user.username}'
    })


@login_required
@role_required('HOD', 'NIC')
def manage_users_view(request):
    """View to manage users under current user's authority - Shows only active users"""

    profile = request.user.userprofile

    # Get users based on role - FILTER ONLY ACTIVE USERS
    if profile.role == 'HOD':
        users_list = (
            UserProfile.objects
            .filter(user__active_status=True)  # Only active users
            .select_related('user', 'department', 'workshop', 'created_by')
            .order_by('user__username')
        )

    elif profile.role == 'NIC':
        # NIC can manage Tech users in workshops under their department
        users_list = (
            UserProfile.objects
            .filter(
                role='Tech',
                workshop__department=profile.department,
                user__active_status=True  # Only active users
            )
            .select_related('user', 'workshop', 'created_by')
            .order_by('user__username')
        )

    else:
        users_list = UserProfile.objects.none()

    # Search functionality
    search_query = request.GET.get('search', '').strip()
    if search_query:
        users_list = users_list.filter(
            Q(user__username__icontains=search_query) |
            Q(user__first_name__icontains=search_query) |
            Q(user__last_name__icontains=search_query) |
            Q(employee_id__icontains=search_query)
        )

    # Pagination
    paginator = Paginator(users_list, 20)
    page_number = request.GET.get('page')
    users = paginator.get_page(page_number)

    # Fetch additional context data
    departments = Department.objects.all().order_by('name')
    level_choices = UserProfile.LEVEL_CHOICES

    # Role & Workshop restrictions
    if profile.role == 'HOD':
        role_choices = UserProfile.ROLE_CHOICES
        workshops = Workshop.objects.all().order_by('name')
    elif profile.role == 'NIC':
        role_choices = [('Tech', 'Technologist')]
        workshops = Workshop.objects.filter(department=profile.department).order_by('name')
    else:
        role_choices = []
        workshops = Workshop.objects.none()

    context = {
        'show_sidebar': True,  # Enable hamburger menu
        'users': users,
        'search_query': search_query,
        'title': 'Manage Users',
        'departments': departments,
        'workshops': workshops,
        'level_choices': level_choices,
        'role_choices': role_choices,
        'current_user_role': profile.role,
    }

    return render(request, 'users_login/manage_users.html', context)
