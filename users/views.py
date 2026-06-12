# views.py - Enhanced with first login setup flow and active users filter

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
from django.views.decorators.csrf import csrf_protect, csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods
from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

# Local Model/Form Imports
from workshop.models import Workshop
from Inventory.models import Department
from .forms import (
    CustomLoginForm, UserCreationForm, ForgotPasswordForm,
    VerifyResetCodeForm, CustomSetPasswordForm
)
from .models import (
    UserProfile, UserSecurityLog, UserSignature, UserPasswordReset
)
from .utils import UserManagementUtils
from .control import hod_required, role_required

User = get_user_model()
logger = logging.getLogger(__name__)

@csrf_exempt
@login_required
@role_required('HOD', 'NIC')
@require_http_methods(["POST", "PUT"])
def api_update_user(request, user_id):
    """API endpoint to update user information (supports both JSON and multipart form data)"""
    try:
        print(f"\n=== API UPDATE USER CALLED ===")
        print(f"User ID: {user_id}")
        print(f"Content-Type: {request.content_type}")
        print(f"Method: {request.method}")

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
            print("Processing as multipart/form-data")
            data = request.POST
            files = request.FILES
            print(f"POST data: {dict(data)}")
            print(f"FILES: {list(files.keys())}")
        else:
            # JSON data (backward compatibility)
            print("Processing as JSON")
            try:
                data = json.loads(request.body)
                print(f"JSON data: {data}")
            except json.JSONDecodeError as je:
                print(f"JSON decode error: {str(je)}")
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
                    print(f"Looking up department: {dept_value}")

                    dept = None
                    # Try to determine if it's a UUID or a name
                    try:
                        # Check if it looks like a UUID
                        uuid.UUID(str(dept_value))
                        # It's a valid UUID, look up by ID
                        dept = Department.objects.get(id=dept_value)
                        print(f"Found department by ID: {dept.name}")
                    except (ValueError, Department.DoesNotExist):
                        # Not a UUID or not found by ID, try by name
                        print(f"Not a valid UUID or not found by ID, trying by name...")
                        try:
                            dept = Department.objects.get(name=dept_value)
                            print(f"Found department by name: {dept.name}")
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
                    print(f"Looking up workshop: {workshop_value}")

                    workshop = None
                    # Try to determine if it's a UUID or a name
                    try:
                        # Check if it looks like a UUID
                        uuid.UUID(str(workshop_value))
                        # It's a valid UUID, look up by ID
                        workshop = Workshop.objects.get(id=workshop_value)
                        print(f"Found workshop by ID: {workshop.name}")
                    except (ValueError, Workshop.DoesNotExist):
                        # Not a UUID or not found by ID, try by name
                        print(f"Not a valid UUID or not found by ID, trying by name...")
                        try:
                            workshop = Workshop.objects.get(name=workshop_value)
                            print(f"Found workshop by name: {workshop.name}")
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
                signature.signature_image = signature_file
                signature.is_active = True
                signature.save()

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
        print(f"Validation Error: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': 'Validation error',
            'details': str(e)
        }, status=400)
    except Exception as e:
        print(f"Exception in api_update_user: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error updating user: {str(e)}'
        }, status=500)

def custom_login_view(request):
    """Enhanced login view with active status check and first login setup redirect"""
    if request.method == 'POST':
        form = CustomLoginForm(request, data=request.POST)

        if form.is_valid():
            user = form.get_user()

            # Check if user account is active
            if not user.active_status:
                form.add_error(None, 'Your account has been deactivated. Please contact the administrator.')

                # Log failed login attempt for deactivated account
                try:
                    UserSecurityLog.log_event(
                        user=user,
                        event_type='LOGIN_FAILURE',
                        description='Login attempt with deactivated account',
                        ip_address=request.META.get('REMOTE_ADDR'),
                        user_agent=request.META.get('HTTP_USER_AGENT', '')
                    )
                except Exception as e:
                    pass

                return render(request, 'users_login/login.html', {'form': form})

            try:
                profile = UserProfile.objects.get(user=user)
                user_role = profile.role
            except UserProfile.DoesNotExist:
                form.add_error(None, 'User profile not found. Please contact administrator.')
                return render(request, 'users_login/login.html', {'form': form})

            # Login user first
            login(request, user)
            request.session['role'] = user_role

            # Log successful login
            try:
                UserSecurityLog.log_event(
                    user=user,
                    event_type='LOGIN_SUCCESS',
                    description='User logged in successfully',
                    ip_address=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', '')
                )
            except Exception as e:
                # Don't fail login if logging fails
                logger.error(f"Failed to log login event: {e}")

            # Check if user needs first login setup
            if profile.needs_first_login_setup():
                messages.info(
                    request,
                    'Welcome! Please complete your account setup by changing your password and creating your digital signature.'
                )
                return redirect('force_setup')

            # Redirect based on role if setup is complete
            if user_role == 'HOD':
                return redirect('dashboard:hod_dashboard')
            elif user_role == 'Tech':
                try:
                    workshop = profile.workshop
                    if workshop and workshop.category == 'maintenance':
                        return redirect('dashboard:dashboard-main')
                    elif workshop and workshop.category == 'calibration_center':
                        return redirect('calibration:cal-dashboard')
                    else:
                        messages.warning(request, 'No workshop category assigned. Redirecting to home.')
                        return redirect('home')
                except (Workshop.DoesNotExist, AttributeError):
                    messages.error(request, 'No workshop assigned. Please contact administrator.')
                    return redirect('home')
            elif user_role == 'NIC':
                return redirect('dashboard:nic_dashboard')

            return redirect('home')
        else:
            # Handle form validation errors
            username = request.POST.get('username', '')

            if username:
                # Try to log failed login attempt
                try:
                    if User.objects.filter(username=username).exists():
                        user = User.objects.get(username=username)
                        UserSecurityLog.log_event(
                            user=user,
                            event_type='LOGIN_FAILURE',
                            description='Failed login attempt - incorrect credentials',
                            ip_address=request.META.get('REMOTE_ADDR'),
                            user_agent=request.META.get('HTTP_USER_AGENT', '')
                        )
                except Exception as e:
                    # Silently fail - logging is not critical
                    pass
    else:
        form = CustomLoginForm()

    return render(request, 'users_login/login.html', {'form': form})



@login_required
def force_setup(request):
    """Force setup view with mandatory signature and password change"""
    profile = request.user.userprofile

    # Redirect if setup already complete
    if not profile.needs_first_login_setup():
        messages.info(request, 'Your account setup is already complete.')
        return _redirect_based_on_role(profile)

    if request.method == "POST":
        new_password = request.POST.get("new_password")
        confirm_password = request.POST.get("confirm_password")
        signature_data = request.POST.get("signature_data")

        errors = []

        # Validate password
        if not new_password or len(new_password.strip()) < 8:
            errors.append("Password must be at least 8 characters long.")
        if new_password and new_password != confirm_password:
            errors.append("Passwords do not match.")

        # Validate signature (MANDATORY)
        if not signature_data or not signature_data.strip():
            errors.append("Digital signature is required.")

        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, "users_login/force_setup.html", {
                'profile': profile,
                'needs_password': profile.must_change_password,
                'needs_signature': not profile.has_uploaded_signature,
                'user_full_name': profile.get_full_name()
            })

        try:
            with transaction.atomic():
                # Handle password change
                if new_password and profile.must_change_password:
                    request.user.set_password(new_password)
                    profile.must_change_password = False

                    # Log password change
                    UserSecurityLog.log_event(
                        user=request.user,
                        event_type='PASSWORD_CHANGE',
                        description='Password changed during first login setup',
                        ip_address=request.META.get('REMOTE_ADDR'),
                        user_agent=request.META.get('HTTP_USER_AGENT', '')
                    )

                # Handle signature (overwrite always)
                format, imgstr = signature_data.split(';base64,')
                ext = format.split('/')[-1]
                if ext.lower() not in ['png', 'jpg', 'jpeg']:
                    ext = 'png'

                file_content = base64.b64decode(imgstr)
                file = ContentFile(file_content, name=f"signature_{request.user.username}.{ext}")

                signature, _ = UserSignature.objects.get_or_create(user=request.user)
                signature.save_user_drawn_signature(file)

                # Log signature upload
                UserSecurityLog.log_event(
                    user=request.user,
                    event_type='SIGNATURE_UPLOADED',
                    description='Digital signature created during first login setup',
                    ip_address=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', '')
                )

                profile.has_uploaded_signature = True

                # Save profile changes
                profile.save()
                request.user.save()

                # Keep session alive after password change
                update_session_auth_hash(request, request.user)

                # Log completion of setup
                UserSecurityLog.log_event(
                    user=request.user,
                    event_type='FIRST_LOGIN_SETUP',
                    description='First login setup completed successfully',
                    ip_address=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                    password_changed=bool(new_password),
                    signature_uploaded=True
                )

                messages.success(request, 'Account setup completed successfully! Welcome to the system.')

                return _redirect_based_on_role(profile)

        except Exception as e:
            messages.error(request, f'Error during setup: {str(e)}')

    context = {
        'profile': profile,
        'needs_password': profile.must_change_password,
        'needs_signature': not profile.has_uploaded_signature,
        'user_full_name': profile.get_full_name()
    }
    return render(request, "users_login/force_setup.html", context)


def _redirect_based_on_role(profile):
    """Shared redirection logic for both login and force setup"""
    if profile.role == 'HOD':
        return redirect('dashboard:hod_dashboard')
    elif profile.role == 'Tech':
        try:
            workshop = profile.workshop
            if workshop.category == 'maintenance':
                return redirect('dashboard:dashboard-main')
            elif workshop.category == 'calibration_center':
                return redirect('calibration:cal-dashboard')
        except Workshop.DoesNotExist:
            messages.error(profile.user, 'No workshop assigned. Please contact administrator.')
            return redirect('home')
    elif profile.role == 'NIC':
        return redirect('dashboard:nic_dashboard')
    return redirect('home')

def logout_view(request):
    """Enhanced logout with logging and session cleanup."""
    if request.user.is_authenticated:
        try:
            UserSecurityLog.log_event(
                user=request.user,
                event_type='LOGOUT',
                description='User logged out',
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        except Exception:
            pass

    logout(request)
    # Explicitly flush to avoid residual session data
    if hasattr(request, 'session'):
        request.session.flush()

    messages.info(request, 'You have been logged out successfully.')
    return redirect('custom_login')


@csrf_exempt
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

            # Prepare response data
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
@hod_required
def create_user_view(request):
    """Enhanced view for HOD to create new users with first login setup"""

    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # Generate username and temporary password
                    username = UserManagementUtils.generate_username(
                        form.cleaned_data['first_name']
                    )
                    temp_password = UserManagementUtils.generate_temp_password()

                    # Create user
                    user = User.objects.create_user(
                        username=username,
                        first_name=form.cleaned_data['first_name'],
                        last_name=form.cleaned_data['last_name'],
                        email=form.cleaned_data['email'],
                        password=temp_password,
                        active_status=True
                    )

                    # Create profile with first login setup requirements
                    profile_data = {
                        'user': user,
                        'role': form.cleaned_data['role'],
                        'created_by': request.user,
                        'must_change_password': True,
                        'has_uploaded_signature': False
                    }

                    # Add role-specific assignments
                    if form.cleaned_data['role'] == 'NIC':
                        profile_data['department'] = form.cleaned_data['department']
                    elif form.cleaned_data['role'] == 'Tech':
                        profile_data['level'] = form.cleaned_data['level']
                        if form.cleaned_data.get('workshop'):
                            profile_data['workshop'] = form.cleaned_data['workshop']

                    profile = UserProfile.objects.create(**profile_data)

                    # Send welcome email
                    email_sent = UserManagementUtils.send_welcome_email(
                        user, temp_password, request.user
                    )

                    success_message = f"""
                    User '{username}' created successfully!
                    Role: {profile.get_role_display()}
                    """

                    if profile.level:
                        success_message += f"\nLevel: {profile.get_level_display()}"
                    if profile.department:
                        success_message += f"\ndepartment: {profile.department.name}"
                    if profile.workshop:
                        success_message += f"\nWorkshop: {profile.workshop.name}"

                    success_message += f"\nEmployee ID: {profile.employee_id}"
                    success_message += f"\nTemporary password: {temp_password}"
                    success_message += "\n\nUser will be required to change password and create digital signature on first login."

                    if email_sent:
                        success_message += "\nWelcome email sent successfully."
                    else:
                        success_message += "\nNote: Welcome email could not be sent."

                    messages.success(request, success_message)
                    return redirect('createUser')

            except Exception as e:
                messages.error(request, f"Error creating user: {str(e)}")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = UserCreationForm()

    recent_users = UserProfile.objects.filter(
        created_by=request.user,
        user__active_status=True
    ).select_related('user', 'department', 'workshop').order_by('-created_at')[:5]

    context = {
        'show_sidebar': True,  # Enable hamburger menu
        'form': form,
        'title': 'Create New User',
        'recent_users': recent_users
    }
    return render(request, 'users_login/create_user.html', context)


# Enhanced signature management endpoints
@login_required
@role_required('HOD', 'NIC')
@require_http_methods(["POST"])
def api_force_signature_reset(request, user_id):
    """Force a user to re-create their signature (admin function)"""
    try:
        user = get_object_or_404(User, id=user_id)

        # Check permissions
        current_profile = request.user.userprofile
        if current_profile.role == 'NIC':
            target_profile = user.userprofile
            if target_profile.role != 'Tech' or target_profile.workshop.department != current_profile.department:
                return JsonResponse({
                    'success': False,
                    'error': 'Permission denied'
                }, status=403)

        # Reset signature flags
        profile = user.userprofile
        profile.has_uploaded_signature = False
        profile.save()

        # Reset signature to system-generated
        try:
            signature = user.signature
            signature.regenerate_signature()
        except UserSignature.DoesNotExist:
            UserSignature.objects.create(user=user)

        # Log the action
        UserSecurityLog.log_event(
            user=user,
            event_type='SIGNATURE_REGENERATED',
            description=f'Signature reset forced by {request.user.username} - user must recreate signature on next login',
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            forced_by=request.user.username
        )

        return JsonResponse({
            'success': True,
            'message': f'Signature reset for {user.username}. User will be required to create a new signature on next login.'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
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
def download_signature(request, user_id):
    """Download user's signature image"""
    user = get_object_or_404(User, id=user_id)

    try:
        signature = user.signature
        if signature.signature_image:
            response = HttpResponse(
                signature.signature_image.read(),
                content_type='image/png'
            )
            response['Content-Disposition'] = f'attachment; filename="signature_{user.username}.png"'
            return response
        else:
            messages.error(request, "Signature image not found.")
            return redirect('user_profile', user_id=user_id)
    except UserSignature.DoesNotExist:
        messages.error(request, "User signature not found.")
        return redirect('user_profile', user_id=user_id)


@login_required
def get_workshops_by_department(request):
    """AJAX view to get workshops filtered by department"""
    department_id = request.GET.get('department_id')
    if department_id:
        workshops = Workshop.objects.filter(department__id=department_id).values('id', 'name')
        return JsonResponse({'workshops': list(workshops)})
    return JsonResponse({'workshops': []})


# Complete Password Reset Views

@never_cache
@csrf_protect
def forgot_password_view(request):
    """Handle forgot password request"""
    if request.method == 'POST':
        form = ForgotPasswordForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            user = User.objects.get(email=email)

            # Delete any existing reset requests for this user (to avoid unique constraint violation)
            UserPasswordReset.objects.filter(user=user).delete()

            # Create new reset request
            reset_request = UserPasswordReset.objects.create(user=user)

            # Send email
            email_sent = UserManagementUtils.send_password_reset_email(
                user, reset_request.reset_code
            )

            if email_sent:
                request.session['reset_email'] = email
                messages.success(
                    request,
                    f'A 6-digit verification code has been sent to {email}. '
                    'Please check your email and enter the code below. '
                    'The code will expire in 30 minutes.'
                )
                return redirect('verify_reset_code')
            else:
                messages.error(
                    request,
                    'Failed to send reset email. Please try again or contact support.'
                )
    else:
        form = ForgotPasswordForm()

    return render(request, 'users_login/forgot_password.html', {
        'form': form,
        'title': 'Forgot Password'
    })


@never_cache
@csrf_protect
def verify_reset_code_view(request):
    """Verify the reset code entered by user"""
    reset_email = request.session.get('reset_email')
    if not reset_email:
        messages.error(request, 'Session expired. Please start the password reset process again.')
        return redirect('forgot_password')

    try:
        user = User.objects.get(email=reset_email)
    except User.DoesNotExist:
        messages.error(request, 'Invalid session. Please start again.')
        return redirect('forgot_password')

    if request.method == 'POST':
        form = VerifyResetCodeForm(request.POST, user=user)
        if form.is_valid():
            # Mark the code as used
            reset_request = form.reset_request
            reset_request.use_code()

            # Store user ID in session for password reset
            request.session['reset_user_id'] = str(user.id)
            request.session['reset_token_id'] = str(reset_request.id)

            # Clear the email from session
            del request.session['reset_email']

            messages.success(request, 'Code verified successfully! Please enter your new password.')
            return redirect('reset_password')
    else:
        form = VerifyResetCodeForm(user=user)

    # Use verify_reset_code.html template (default mode)
    return render(request, 'users_login/verify_reset_code.html', {
        'form': form,
        'title': 'Enter Verification Code',
        'email': reset_email
    })

@never_cache
@csrf_protect
def reset_password_view(request):
    """Allow user to set new password after code verification"""
    reset_user_id = request.session.get('reset_user_id')
    reset_token_id = request.session.get('reset_token_id')

    if not reset_user_id or not reset_token_id:
        messages.error(request, 'Invalid session. Please start the password reset process again.')
        return redirect('forgot_password')

    try:
        user = User.objects.get(id=reset_user_id)
        reset_request = UserPasswordReset.objects.get(id=reset_token_id)

        if not reset_request.is_used:
            messages.error(request, 'Invalid reset session. Please start again.')
            return redirect('forgot_password')

    except (User.DoesNotExist, UserPasswordReset.DoesNotExist):
        messages.error(request, 'Invalid session. Please start again.')
        return redirect('forgot_password')

    if request.method == 'POST':
        form = CustomSetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()

            del request.session['reset_user_id']
            del request.session['reset_token_id']

            UserSecurityLog.log_event(
                user=user,
                event_type='PASSWORD_RESET',
                description='Password reset via email verification',
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )

            messages.success(
                request,
                'Password reset successfully! You can now log in with your new password.'
            )
            return redirect('custom_login')
    else:
        form = CustomSetPasswordForm(user)

    # CRITICAL: Use verify_reset_code.html with mode='reset_password'
    return render(request, 'users_login/verify_reset_code.html', {
        'form': form,
        'title': 'Set New Password',
        'user': user,
        'mode': 'reset_password'
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

    return render(request, 'users_login/admin_reset_password.html', {
        'show_sidebar': True,  # Enable hamburger menu
        'target_user': target_user,
        'title': f'Reset Password for {target_user.get_full_name() or target_user.username}'
    })


@login_required
@role_required('HOD', 'NIC')
def user_management_page(request):
    """Render the user management HTML page"""
    return render(request, 'users_login/user_management.html', {
        'show_sidebar': True,  # Enable hamburger menu
        'title': 'User Management System'
    })


@login_required
@role_required('HOD', 'NIC')
def manage_users_view(request):
    """View to manage users under current user's authority - Shows only active users"""

    profile = request.user.userprofile

    # Get users based on role - FILTER ONLY ACTIVE USERS
    if profile.role == 'HOD':
        from Inventory.models import Department
        from workshop.models import Workshop

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
        print(f"Error in api_get_users: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
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


@csrf_exempt
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
def api_get_user_signature(request, user_id):
    """API endpoint to get user signature (JSON)"""
    try:
        user = get_object_or_404(User, id=user_id)

        # Check permissions
        current_profile = request.user.userprofile
        if current_profile.role == 'NIC':
            target_profile = user.userprofile
            if target_profile.role != 'Tech' or target_profile.workshop.department != current_profile.department:
                return JsonResponse({
                    'success': False,
                    'error': 'Permission denied'
                }, status=403)
        elif current_profile.role not in ['HOD', 'NIC'] and user != request.user:
            return JsonResponse({
                'success': False,
                'error': 'Permission denied'
            }, status=403)

        try:
            signature = user.signature
            if signature.signature_image:
                return JsonResponse({
                    'success': True,
                    'hasSignature': True,
                    'signatureUrl': signature.signature_image.url,
                    'signatureId': str(signature.signature_id),
                    'createdAt': signature.created_at.isoformat(),
                    'updatedAt': signature.updated_at.isoformat()
                })
            else:
                return JsonResponse({
                    'success': True,
                    'hasSignature': False,
                    'message': 'No signature image available'
                })

        except UserSignature.DoesNotExist:
            return JsonResponse({
                'success': True,
                'hasSignature': False,
                'message': 'No signature found'
            })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
@login_required
@role_required('HOD', 'NIC')
@require_http_methods(["POST"])
def api_regenerate_signature(request, user_id):
    """API endpoint to regenerate user signature (JSON)"""
    try:
        user = get_object_or_404(User, id=user_id)

        # Check permissions
        current_profile = request.user.userprofile
        if current_profile.role == 'NIC':
            target_profile = user.userprofile
            if target_profile.role != 'Tech' or target_profile.workshop.department != current_profile.department:
                return JsonResponse({
                    'success': False,
                    'error': 'Permission denied'
                }, status=403)

        # Get or create signature
        signature, created = UserSignature.objects.get_or_create(user=user)

        # Regenerate signature
        signature.regenerate_signature()

        # Log the event
        UserSecurityLog.log_event(
            user=user,
            event_type='SIGNATURE_REGENERATED',
            description=f'Signature regenerated by {request.user.username}',
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            regenerated_by=request.user.username
        )

        return JsonResponse({
            'success': True,
            'message': 'Signature regenerated successfully',
            'signatureUrl': signature.signature_image.url if signature.signature_image else None,
            'signatureId': str(signature.signature_id)
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


@login_required
@require_POST
def update_theme(request):
    """Update user's theme preference"""
    try:
        data = json.loads(request.body)
        theme = data.get('theme', 'light')

        # Validate theme choice
        valid_themes = ['light', 'dark', 'auto']
        if theme not in valid_themes:
            return JsonResponse({
                'success': False,
                'error': 'Invalid theme choice'
            }, status=400)

        # Update user profile
        profile = request.user.userprofile
        profile.theme_mode = theme
        profile.save(update_fields=['theme_mode'])

        return JsonResponse({
            'success': True,
            'theme': theme
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def update_sidebar(request):
    """Update user's sidebar collapsed preference"""
    try:
        data = json.loads(request.body)
        collapsed = data.get('collapsed', False)

        # Update user profile
        profile = request.user.userprofile
        profile.sidebar_collapsed = collapsed
        profile.save(update_fields=['sidebar_collapsed'])

        return JsonResponse({
            'success': True,
            'collapsed': collapsed
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def update_settings(request):
    """Update user's theme and sidebar settings"""
    try:
        data = json.loads(request.body)
        theme = data.get('theme', 'light')
        sidebar_collapsed = data.get('sidebar_collapsed', False)

        # Validate theme choice
        valid_themes = ['light', 'dark', 'auto']
        if theme not in valid_themes:
            return JsonResponse({
                'success': False,
                'error': 'Invalid theme choice'
            }, status=400)

        # Update user profile
        profile = request.user.userprofile
        profile.theme_mode = theme
        profile.sidebar_collapsed = sidebar_collapsed
        profile.save(update_fields=['theme_mode', 'sidebar_collapsed'])

        return JsonResponse({
            'success': True,
            'theme': theme,
            'sidebar_collapsed': sidebar_collapsed
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
