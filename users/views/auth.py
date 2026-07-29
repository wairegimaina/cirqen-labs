"""users views — login/logout, force-setup, and the password-reset flow."""
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
from workshop.models import Workshop
from Inventory.models import Department
from ..forms import CustomLoginForm, UserCreationForm, ForgotPasswordForm, VerifyResetCodeForm, CustomSetPasswordForm
from ..models import UserProfile, UserSecurityLog, UserSignature, UserPasswordReset
from ..utils import UserManagementUtils
from ..control import hod_required, role_required
User = get_user_model()
logger = logging.getLogger(__name__)


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


@never_cache
@csrf_protect
def forgot_password_view(request):
    """Handle forgot password request"""
    if request.method == 'POST':
        form = ForgotPasswordForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']

            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                messages.error(request, 'No account found with that email address.')
                return render(request, 'users_login/forgot_password.html', {
                    'form': form,
                    'title': 'Forgot Password'
                })

            try:
                # Delete any existing reset requests for this user
                UserPasswordReset.objects.filter(user=user).delete()

                # Create new reset request (self-service, no created_by)
                reset_request = UserPasswordReset.objects.create(user=user)

                # Send email
                email_sent = UserManagementUtils.send_password_reset_email(
                    user, reset_request.reset_code
                )

                logger.info(
                    "[FORGOT PASSWORD] send_password_reset_email returned %s for user=%s email=%s",
                    email_sent, user.username, email,
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
            except Exception as e:
                logger.error(f"Password reset error for {email}: {e}")
                messages.error(
                    request,
                    'An error occurred while processing your request. Please try again.'
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
