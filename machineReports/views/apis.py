"""machineReports views — equipment count preview JSON endpoint."""
from uuid import UUID
from django.shortcuts import redirect, render, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.db.models import Count, Sum, Avg, Q, F
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.contrib import messages
from datetime import datetime, timedelta
import json
import logging
from openpyxl import Workbook
from collections import defaultdict
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Avg, Count, Q
from Inventory.models import Equipment
from CalSoft.models import CalibrationSession
from ..models import EquipmentStatusReport, MachineRepairHistory, WorkshopEquipmentReport, EquipmentCategory
from Inventory.models import Equipment, Workshop
from jobcard.models import jobcard, SparePartUsed
from CalSoft.models import CalibrationSession, CalibrationSchedule
logger = logging.getLogger(__name__)
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from ..models import EquipmentCategory
from Inventory.models import EquipmentDescription
from django.template.loader import render_to_string
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, PageBreak, BaseDocTemplate, Frame, PageTemplate
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from django.http import HttpResponse
from django.utils import timezone
from django.contrib.auth.decorators import login_required
import os
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.db.models import Q
from ..equipment_pdf_generator import create_equipment_pdf_response
from Inventory.models import Equipment, Workshop
from ..manufacturer_performance_pdf_generator import create_manufacturer_pdf_response
from Inventory.models import Equipment, Workshop

# sibling modules in this package
from .helpers import get_user_workshop_context


@login_required
def equipment_count_preview_api(request):
    """API endpoint to get equipment count preview for export"""
    profile, workshops, selected_workshop, is_hod = get_user_workshop_context(request)

    if profile is None:
        return JsonResponse({'error': 'Access denied'}, status=403)

    # Get base equipment queryset with active_status filter
    equipment_qs = Equipment.objects.select_related("workshop", "category", "description", "manufacturer").filter(active_status=True)

    # Apply workshop filter
    if is_hod:
        workshop_id = request.GET.get("workshop")
        if workshop_id:
            equipment_qs = equipment_qs.filter(workshop_id=workshop_id)
    else:
        equipment_qs = equipment_qs.filter(workshop=selected_workshop)

    # Apply category filter
    category_id = request.GET.get("category")
    if category_id:
        equipment_qs = equipment_qs.filter(category_id=category_id)

    # Apply search filter
    search_query = request.GET.get("search", "")
    if search_query:
        equipment_qs = equipment_qs.filter(
            Q(description__name__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(manufacturer__name__icontains=search_query)
        )

    # Get counts
    total_equipment = equipment_qs.count()

    # Status breakdown
    status_breakdown = {
        'working': equipment_qs.filter(status="Working").count(),
        'under_repair': equipment_qs.filter(status="Under repair").count(),
        'not_working': equipment_qs.filter(status="Not working").count(),
    }

    # Category counts (for updating badges)
    categories = EquipmentCategory.objects.all()
    category_counts = {}
    for category in categories:
        # Apply same filters but for each category
        cat_qs = Equipment.objects.select_related("workshop", "category", "description", "manufacturer").filter(active_status=True)

        # Workshop filter
        if is_hod:
            workshop_id = request.GET.get("workshop")
            if workshop_id:
                cat_qs = cat_qs.filter(workshop_id=workshop_id)
        else:
            cat_qs = cat_qs.filter(workshop=selected_workshop)

        # Category filter
        cat_qs = cat_qs.filter(category=category)

        # Search filter
        if search_query:
            cat_qs = cat_qs.filter(
                Q(description__name__icontains=search_query) |
                Q(serial_number__icontains=search_query) |
                Q(model__icontains=search_query) |
                Q(manufacturer__name__icontains=search_query)
            )

        category_counts[str(category.id)] = cat_qs.count()

    # Get category-specific equipment count if category is selected
    category_equipment = 0
    if category_id:
        category_equipment = total_equipment

    # Get total category count
    category_count = categories.count()

    return JsonResponse({
        'total_equipment': total_equipment,
        'category_equipment': category_equipment,
        'category_count': category_count,
        'status_breakdown': status_breakdown,
        'category_counts': category_counts,
        'filters_applied': {
            'workshop': bool(request.GET.get("workshop")),
            'category': bool(category_id),
            'search': bool(search_query),
        }
    })
