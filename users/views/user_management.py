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


def _validate_user_payload(data):
    """Check the create/restore form. Returns (assignments, None) where
    assignments holds role, department, workshop and level for the profile,
    or (None, error message)."""
    for field in ['firstName', 'lastName', 'email', 'role']:
        if not str(data.get(field) or '').strip():
            return None, f'Missing required field: {field}'

    valid_roles = dict(UserProfile.ROLE_CHOICES).keys()
    if data['role'] not in valid_roles:
        return None, f'Invalid role. Must be one of: {", ".join(valid_roles)}'

    assignments = {'role': data['role'], 'department': None, 'workshop': None, 'level': None}
    if data['role'] == 'NIC':
        if not data.get('department'):
            return None, 'department is required for NIC role'
        try:
            assignments['department'] = Department.objects.get(id=data['department'])
        except (Department.DoesNotExist, ValueError, ValidationError):
            return None, 'Invalid department ID'

    elif data['role'] == 'Tech':
        if not data.get('workshop'):
            return None, 'Workshop is required for Tech role'
        if not data.get('level'):
            return None, 'Level is required for Tech role'
        try:
            assignments['workshop'] = Workshop.objects.get(id=data['workshop'])
        except (Workshop.DoesNotExist, ValueError, ValidationError):
            return None, 'Invalid workshop ID'
        valid_levels = dict(UserProfile.LEVEL_CHOICES).keys()
        if data['level'] not in valid_levels:
            return None, f'Invalid level. Must be one of: {", ".join(valid_levels)}'
        assignments['level'] = data['level']

    return assignments, None


def _user_json(user, profile, temp_password):
    return {
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
    }


def _deleted_user_json(user):
    profile = getattr(user, 'userprofile', None)
    return {
        'id': str(user.id),
        'username': user.username,
        'name': user.get_full_name() or user.username,
        'email': user.email,
        'role': profile.get_role_display() if profile else '',
        'workshop': profile.workshop.name if profile and profile.workshop else '',
        'department': profile.department.name if profile and profile.department else '',
    }


@require_http_methods(["POST"])
@login_required
@role_required('HOD')
def api_create_user(request):
    """Create a user and email them a temporary password.

    When the email belongs to a deleted (deactivated) account the answer is
    409 with ``canRestore`` so the page can offer to restore that account
    instead, the same way a deleted device's serial number is reactivated.
    """
    try:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON data'
            }, status=400)

        assignments, error = _validate_user_payload(data)
        if error:
            return JsonResponse({'success': False, 'error': error}, status=400)

        email = data['email'].strip()
        existing = User.objects.filter(email__iexact=email).select_related('userprofile').first()
        if existing:
            if not existing.active_status:
                return JsonResponse({
                    'success': False,
                    'canRestore': True,
                    'error': 'This email belongs to a deleted user.',
                    'deletedUser': _deleted_user_json(existing),
                }, status=409)
            return JsonResponse({
                'success': False,
                'error': 'Email already exists'
            }, status=400)

        with transaction.atomic():
            username = UserManagementUtils.generate_username(data['firstName'])
            temp_password = UserManagementUtils.generate_temp_password()

            user = User.objects.create_user(
                username=username,
                first_name=data['firstName'].strip(),
                last_name=data['lastName'].strip(),
                email=email,
                password=temp_password,
                active_status=True
            )

            # Created with the first login flags: change the password, add a signature.
            profile = UserProfile.objects.create(
                user=user,
                created_by=request.user,
                must_change_password=True,
                has_uploaded_signature=False,
                **assignments,
            )

            UserSecurityLog.log_event(
                user=user,
                event_type='ACCOUNT_CREATED',
                description=f'Account created by {request.user.username}',
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                created_by=request.user.username
            )

        # After the commit, so a slow mail server never holds the transaction open.
        email_sent = UserManagementUtils.send_welcome_email(user, temp_password, request.user)
        logger.info(
            "[CREATE USER] send_welcome_email returned %s for user=%s email=%s",
            email_sent, user.username, user.email,
        )

        return JsonResponse({
            'success': True,
            'message': 'User created successfully',
            'user': _user_json(user, profile, temp_password),
            'emailSent': email_sent
        }, status=201)

    except Exception as e:
        logger.exception("[CREATE USER] failed")
        return JsonResponse({
            'success': False,
            'error': f'Error creating user: {str(e)}'
        }, status=500)


@require_http_methods(["POST"])
@login_required
@role_required('HOD')
def api_restore_user(request, user_id):
    """Bring a deleted user back with the details on the create form.

    The account keeps its username and history; it gets a new temporary
    password (emailed like a new user's) and must change it on first login.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)

    user = get_object_or_404(User, id=user_id)
    if user.active_status:
        return JsonResponse({'success': False, 'error': 'This user is already active.'}, status=400)

    assignments, error = _validate_user_payload(data)
    if error:
        return JsonResponse({'success': False, 'error': error}, status=400)

    temp_password = UserManagementUtils.generate_temp_password()
    with transaction.atomic():
        user.first_name = data['firstName'].strip()
        user.last_name = data['lastName'].strip()
        user.active_status = True
        user.set_password(temp_password)
        user.save()

        profile, _ = UserProfile.objects.get_or_create(
            user=user, defaults={'role': assignments['role'], 'created_by': request.user})
        for field, value in assignments.items():
            setattr(profile, field, value)
        profile.active_status = True
        profile.pending_delete = False
        profile.must_change_password = True
        profile.save()

        UserSecurityLog.log_event(
            user=user,
            event_type='ACCOUNT_RESTORED',
            description=f'Account restored by {request.user.username}',
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            restored_by=request.user.username
        )

    email_sent = UserManagementUtils.send_welcome_email(user, temp_password, request.user)
    logger.info("[RESTORE USER] %s restored by %s; email sent=%s", user.username, request.user.username, email_sent)

    return JsonResponse({
        'success': True,
        'message': 'User restored successfully',
        'user': _user_json(user, profile, temp_password),
        'emailSent': email_sent
    })


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
