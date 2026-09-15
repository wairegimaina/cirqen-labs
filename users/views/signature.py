"""users views — signature download / view / (re)generate endpoints."""
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
def download_signature(request, user_id):
    """Download user's signature image"""
    user = get_object_or_404(User, id=user_id)

    try:
        signature = user.signature
        image_bytes = signature.get_signature_bytes()
        if image_bytes:
            response = HttpResponse(image_bytes, content_type='image/png')
            response['Content-Disposition'] = f'attachment; filename="signature_{user.username}.png"'
            return response
        else:
            messages.error(request, "Signature image not found.")
            return redirect('user_profile', user_id=user_id)
    except UserSignature.DoesNotExist:
        messages.error(request, "User signature not found.")
        return redirect('user_profile', user_id=user_id)


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
            signature_url = signature.get_signature_as_base64()
            if signature_url:
                return JsonResponse({
                    'success': True,
                    'hasSignature': True,
                    'signatureUrl': signature_url,
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
            'signatureUrl': signature.get_signature_as_base64(),
            'signatureId': str(signature.signature_id)
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
