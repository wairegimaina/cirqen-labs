"""machineReports views — the equipment dashboard page."""
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
from .helpers import calculate_manufacturer_performance, get_user_workshop_context
from core import aggregate_cache


@login_required
def equipment_dashboard(request):
    """Main dashboard: grouped equipment, repair overview, and category/criticality assignment."""

    profile, workshops, selected_workshop, is_hod = get_user_workshop_context(request)
    if profile is None:
        messages.error(request, "You do not have a user profile assigned. Please contact the administrator.")
        return redirect("custom_login")

    descriptions = EquipmentDescription.objects.all()
    categories = EquipmentCategory.objects.all()

    # ---- Handle category assignment ----
    if request.method == "POST" and "assign_categories" in request.POST:
        updated_count = 0
        for desc in descriptions:
            category_id = request.POST.get(f"category_{desc.id}")
            if category_id:
                try:
                    new_category = EquipmentCategory.objects.get(id=category_id)
                    if desc.category != new_category:
                        desc.category = new_category
                        desc.save()
                        updated_count += 1
                except EquipmentCategory.DoesNotExist:
                    pass
            else:
                if desc.category is not None:
                    desc.category = None
                    desc.save()
                    updated_count += 1

        if updated_count > 0:
            messages.success(request, f"Successfully updated {updated_count} equipment descriptions!")
        else:
            messages.info(request, "No changes were made.")
        return redirect("equipment_dashboard")

    # ---- Equipment Query with active_status filter ----
    equipment_qs = Equipment.objects.select_related("workshop", "category", "description", "manufacturer").filter(active_status=True)

    # Workshop filter
    if is_hod:
        workshop_id = request.GET.get("workshop")
        if workshop_id:
            equipment_qs = equipment_qs.filter(workshop_id=workshop_id)
            selected_workshop = get_object_or_404(Workshop, id=workshop_id)
    else:
        equipment_qs = equipment_qs.filter(workshop=selected_workshop)

    # Category filter
    category_id = request.GET.get("category")
    if category_id:
        equipment_qs = equipment_qs.filter(category_id=category_id)

    # Search filter
    search_query = request.GET.get("search", "")
    if search_query:
        equipment_qs = equipment_qs.filter(
            Q(description__name__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(manufacturer__name__icontains=search_query)
        )

    # ---- Pagination (20 per page for AJAX, 10 for full page) ----
    items_per_page = 20 if request.headers.get('Accept') == 'application/json' else 10
    paginator = Paginator(equipment_qs.order_by("id"), items_per_page)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    # ---- Return JSON if Accept header is application/json ----
    if request.headers.get('Accept') == 'application/json':
        equipment_list = []
        for eq in page_obj:
            equipment_list.append({
                'id': str(eq.id),
                'name': eq.description.name if eq.description else 'N/A',
                'description': {
                    'name': eq.description.name if eq.description else 'N/A'
                },
                'manufacturer': eq.manufacturer.name if eq.manufacturer else 'Unknown',
                'serial_number': eq.serial_number or 'N/A',
                'model': eq.model or 'N/A',
                'category': {
                    'name': eq.category.name if eq.category else 'Uncategorized'
                },
                'status': eq.status or 'Unknown',
            })

        return JsonResponse({
            'results': equipment_list,
            'current_page': page_obj.number,
            'total_pages': paginator.num_pages,
            'total_count': paginator.count,
            'has_previous': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'previous': page_obj.previous_page_number() if page_obj.has_previous() else None,
            'next': page_obj.next_page_number() if page_obj.has_next() else None,
        })

    # ---- Group Equipment by Category ----
    categorized_equipment = {}
    for category in categories:
        category_equipment = equipment_qs.filter(category=category)
        categorized_equipment[category] = {
            "equipment": category_equipment,
            "total": category_equipment.count(),
            "working": category_equipment.filter(status="Working").count(),
            "under_repair": category_equipment.filter(status="Under repair").count(),
            "not_working": category_equipment.filter(status="Not working").count(),
            "due_calibration": category_equipment.filter(status="Due calibration").count()
            if hasattr(category_equipment.first(), "status") else 0,
        }

    # ---- Manufacturer Performance ----
    # One job card query per device, so the unfiltered view is cached per
    # workshop; a search or category filter is computed fresh.
    if not category_id and not search_query:
        scope = (request.GET.get("workshop") or "all") if is_hod else getattr(selected_workshop, "id", "none")
        manufacturer_performance = aggregate_cache.get_or_compute(
            "inv", ["manufacturer", scope],
            lambda: calculate_manufacturer_performance(equipment_qs),
        )
    else:
        manufacturer_performance = calculate_manufacturer_performance(equipment_qs)

    # ---- Repair Job Cards ----
    approved_job_cards = jobcard.objects.filter(
        equipment__in=equipment_qs,
        action_taken="Repair",
        status="Approved",
    ).select_related(
        "equipment__category", "equipment__workshop", "performed_by"
    ).prefetch_related("spare_parts__part")

    # ---- Date Filters ----
    year = request.GET.get("year")
    month = request.GET.get("month")
    if year:
        approved_job_cards = approved_job_cards.filter(date_issued__year=year)
    if month:
        approved_job_cards = approved_job_cards.filter(date_issued__month=month)

    # ---- Repair Stats with Costs ----
    repair_stats = {
        "total_repairs": approved_job_cards.count(),
        "total_downtime_hours": 0,
        "total_repair_cost": 0,
        "total_labor_cost": 0,
        "total_parts_cost": 0,
    }

    total_downtime_seconds = 0
    for jc in approved_job_cards:
        if jc.time_started and jc.time_completed:
            start_datetime = datetime.combine(jc.date_issued, jc.time_started)
            end_datetime = datetime.combine(jc.date_issued, jc.time_completed)
            downtime = end_datetime - start_datetime
            total_downtime_seconds += downtime.total_seconds()

        # Add cost calculations
        repair_stats["total_labor_cost"] += float(jc.labor_cost)
        repair_stats["total_parts_cost"] += float(jc.total_parts_cost)
        repair_stats["total_repair_cost"] += float(jc.get_total_cost())

    repair_stats["total_downtime_hours"] = round(total_downtime_seconds / 3600, 2)

    # ---- Recent Repair History (latest 5) with costs ----
    history_items = []
    recent_repairs = approved_job_cards.order_by("-date_issued", "-time_started")[:5]
    for job_card in recent_repairs:
        spare_parts = [
            {
                "name": sp.part.name.name if sp.part and sp.part.name else "Unknown Part",
                "quantity": sp.quantity,
                "unit_cost": float(sp.unit_cost),
                "total_cost": float(sp.get_total_cost()),
                "remarks": sp.remarks
            }
            for sp in job_card.spare_parts.all()
        ]

        downtime_hours = 0
        if job_card.time_started and job_card.time_completed:
            start_time = datetime.combine(job_card.date_issued, job_card.time_started)
            end_time = datetime.combine(job_card.date_issued, job_card.time_completed)
            downtime = end_time - start_time
            downtime_hours = round(downtime.total_seconds() / 3600, 2)

        history_items.append({
            "type": "repair",
            "date": job_card.date_issued,
            "date_display": job_card.date_issued,
            "equipment": job_card.equipment,
            "repair_count": 1,
            "downtime_hours": downtime_hours,
            # Add cost information
            "labor_cost": float(job_card.labor_cost),
            "total_parts_cost": float(job_card.total_parts_cost),
            "additional_costs": float(job_card.additional_costs),
            "additional_costs_description": job_card.additional_costs_description or "",
            "total_cost": float(job_card.get_total_cost()),
            "job_cards": [{
                "id": job_card.id,
                "date": job_card.date_issued,
                "description": job_card.job_description,
                "action_taken": job_card.action_taken,
                "performed_by": job_card.performed_by.get_full_name() if job_card.performed_by else "N/A",
                "time_started": job_card.time_started,
                "time_completed": job_card.time_completed,
                "status": job_card.status,
                "spare_parts": spare_parts,
                # Add cost breakdown to job card detail
                "labor_cost": float(job_card.labor_cost),
                "total_parts_cost": float(job_card.total_parts_cost),
                "additional_costs": float(job_card.additional_costs),
                "additional_costs_description": job_card.additional_costs_description or "",
                "total_cost": float(job_card.get_total_cost()),
            }],
        })

    # ---- Context ----
    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "categories": categories,
        "selected_category": category_id,
        "categorized_equipment": categorized_equipment,
        "manufacturer_performance": manufacturer_performance,  # rendered with |json_script
        "history": history_items,
        "repair_stats": repair_stats,
        "workshops": workshops if is_hod else None,
        "selected_workshop": selected_workshop,
        "is_hod": is_hod,
        "current_user": request.user.username,
        "current_date": timezone.now(),
        "years": range(2020, timezone.now().year + 1),
        "months": [
            (1, "January"), (2, "February"), (3, "March"), (4, "April"),
            (5, "May"), (6, "June"), (7, "July"), (8, "August"),
            (9, "September"), (10, "October"), (11, "November"), (12, "December"),
        ],
        "selected_year": year,
        "selected_month": month,
        "descriptions": descriptions,
        "equipment_page": page_obj,
    }

    return render(request, "Machine Reports/machinereports.html", context)
