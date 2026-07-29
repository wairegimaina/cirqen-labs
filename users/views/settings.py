"""users views — theme, sidebar, and settings preference endpoints."""
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
