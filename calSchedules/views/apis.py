"""calSchedules views — overdue/waiting-group/group/task status JSON endpoints."""
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from datetime import datetime, timedelta, date
import calendar
from dateutil.relativedelta import relativedelta
from workshop.models import Workshop
from ..models import CalibrationSchedule
from Inventory.models import Equipment, Department, EquipmentDescription
from openpyxl import Workbook
from CalSoft.models import CalibrationSession
from django.db import transaction
import logging
from django.db.models.functions import TruncMonth
from django.db.models import Q, Case, When, IntegerField, Count
logger = logging.getLogger(__name__)
from django.utils import timezone
from django.contrib.auth import get_user_model
import uuid
User = get_user_model()
from ..calibration_pdf_generator import create_calibration_pdf_response

# sibling modules in this package
from .helpers import check_overdue_schedules, get_user_access_context


@login_required
def get_overdue_status(request):
    """
    ✅ NEW API ENDPOINT
    API endpoint to get overdue schedule information
    """
    try:
        access_context = get_user_access_context(request)
        if not access_context:
            return JsonResponse({'error': 'Access denied'}, status=403)

        # Get schedules
        if access_context['access_type'] == 'department':
            schedules = CalibrationSchedule.objects.filter(
                equipment__department_id=access_context['department_id'],
                equipment__active_status=True
            )
        else:
            schedules = CalibrationSchedule.objects.filter(
                equipment__active_status=True
            )

        overdue_info = check_overdue_schedules(schedules)

        return JsonResponse({
            'overdue_count': overdue_info['overdue_count'],
            'warning_count': overdue_info['warning_count'],
            'total': schedules.count()
        })

    except Exception as e:
        logger.error(f"Error getting overdue status: {str(e)}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)

