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


from .models import (
    EquipmentStatusReport,
    MachineRepairHistory,
    WorkshopEquipmentReport,
    EquipmentCategory,
)
from Inventory.models import Equipment, Workshop
from jobcard.models import jobcard, SparePartUsed
from CalSoft.models import (CalibrationSession, CalibrationSchedule)

logger = logging.getLogger(__name__)

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import EquipmentCategory
from Inventory.models import EquipmentDescription
from django.template.loader import render_to_string

from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle,
    Spacer, PageBreak, BaseDocTemplate, Frame, PageTemplate
)
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

from .equipment_pdf_generator import create_equipment_pdf_response

from Inventory.models import Equipment, Workshop

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
    manufacturer_performance = calculate_manufacturer_performance(equipment_qs)
    # Convert to JSON string properly
    manufacturer_performance_json = json.dumps(manufacturer_performance)

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
        "manufacturer_performance": manufacturer_performance_json,  # Now properly JSON encoded
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


def calculate_manufacturer_performance(equipment_qs):
    """
    Calculate performance metrics for each manufacturer

    Args:
        equipment_qs: QuerySet of Equipment objects

    Returns:
        dict: Performance metrics for each manufacturer
    """
    from collections import defaultdict
    from datetime import datetime
    from jobcard.models import jobcard

    manufacturers = defaultdict(lambda: {
        'equipment_count': 0,
        'total_repairs': 0,
        'total_downtime': 0,
        'total_repair_cost': 0,
        'working_equipment': 0,
        'avg_uptime': 0,
        'repair_frequency': 0,
        'avg_repair_cost': 0
    })

    # Get equipment data grouped by manufacturer - filter active_status
    for equipment in equipment_qs.filter(active_status=True).select_related('category', 'workshop', 'manufacturer'):
        # Get manufacturer name instead of manufacturer instance
        manufacturer_name = equipment.manufacturer.name if equipment.manufacturer else 'Unknown'
        manufacturers[manufacturer_name]['equipment_count'] += 1

        if equipment.status == 'Working':
            manufacturers[manufacturer_name]['working_equipment'] += 1

        # Get repair data for this equipment
        repairs = jobcard.objects.filter(
            equipment=equipment,
            action_taken='Repair',
            status='Approved'
        ).prefetch_related('spare_parts__part')

        for repair in repairs:
            manufacturers[manufacturer_name]['total_repairs'] += 1

            # Calculate downtime
            if repair.time_started and repair.time_completed:
                start_time = datetime.combine(repair.date_issued, repair.time_started)
                end_time = datetime.combine(repair.date_issued, repair.time_completed)
                downtime = (end_time - start_time).total_seconds() / 3600
                manufacturers[manufacturer_name]['total_downtime'] += downtime

            # Calculate repair costs
            repair_cost = sum(
                sp.quantity * float(sp.part.unit_cost if sp.part else 0)
                for sp in repair.spare_parts.all()
            )
            manufacturers[manufacturer_name]['total_repair_cost'] += repair_cost

    # Calculate derived metrics
    for manufacturer, metrics in manufacturers.items():
        if metrics['equipment_count'] > 0:
            metrics['avg_uptime'] = (metrics['working_equipment'] / metrics['equipment_count']) * 100
            metrics['repair_frequency'] = metrics['total_repairs'] / metrics['equipment_count']
            if metrics['total_repairs'] > 0:
                metrics['avg_repair_cost'] = metrics['total_repair_cost'] / metrics['total_repairs']

    # Convert to regular dict to return
    return dict(manufacturers)


@require_http_methods(["GET"])
def equipment_repair_details(request, equipment_id):
    """
    Fetch repair history and calibration certificates for a specific equipment.

    Args:
        request: HTTP request
        equipment_id (UUID): Equipment ID

    Returns:
        JsonResponse with equipment details, repairs, and calibration certificates

    URL Pattern:
        /machineReports/equipment/<uuid:equipment_id>/repair-details/
    """
    try:
        # Get the equipment
        equipment = get_object_or_404(Equipment, id=equipment_id)

        # Get calibration sessions for this equipment
        # Match by BOTH schedule equipment relationship AND serial number
        # This ensures we capture all sessions regardless of how they were created
        calibration_sessions = CalibrationSession.objects.filter(
            Q(schedule__equipment=equipment) | Q(device_serial=equipment.serial_number),
            status__in=['approved', 'approved_pending_certificate']
        ).select_related(
            'procedure',
            'performed_by',
            'device_manufacturer',
            'device_description',
            'schedule'
        ).distinct().order_by('-timestamp')

        # Format calibration certificates data for frontend
        calibration_certificates = []
        for session in calibration_sessions:
            calibration_certificates.append({
                'id': str(session.id),
                'certificate_number': session.certificate_number or 'Pending',
                'date': session.timestamp.strftime('%Y-%m-%d'),
                'next_due': session.next_calibration_due.strftime('%Y-%m-%d') if session.next_calibration_due else 'Not Set',
                'procedure_name': session.procedure.name if session.procedure else 'N/A',
                'performed_by': session.performed_by.get_full_name() or session.performed_by.username,
                'overall_pass': session.overall_pass,
                'status': session.get_status_display(),
                'notes': session.notes or '',
                'certificate_url': f'/calibration/sessions/{session.id}/certificate/comprehensive/',
            })

        # ========================================
        # REPAIR HISTORY SECTION
        # ========================================
        repairs = []
        repair_summary = {
            'total_repairs': 0,
            'total_cost': 0.0,
            'total_downtime': 0.0,
            'average_repair_cost': 0.0,
            'total_labor_cost': 0.0,
            'total_parts_cost': 0.0
        }

        repairs_qs = jobcard.objects.filter(
            equipment=equipment,
            action_taken='Repair',
            status='Approved'
        ).select_related('performed_by').prefetch_related('spare_parts__part').order_by('-date_issued')

        for repair in repairs_qs:
            # Calculate downtime
            downtime_hours = 0.0
            if repair.time_started and repair.time_completed:
                start_time = datetime.combine(repair.date_issued, repair.time_started)
                end_time = datetime.combine(repair.date_issued, repair.time_completed)
                downtime = end_time - start_time
                downtime_hours = round(downtime.total_seconds() / 3600, 2)

            # Build spare parts list
            spare_parts = []
            for sp in repair.spare_parts.all():
                spare_parts.append({
                    'name': sp.part.name.name if sp.part and sp.part.name else 'Unknown Part',
                    'quantity': sp.quantity,
                    'unit_cost': float(sp.unit_cost),
                    'total_cost': float(sp.get_total_cost()),
                })

            repairs.append({
                'id': str(repair.id),
                'date': repair.date_issued.strftime('%Y-%m-%d'),
                'description': repair.job_description,
                'performed_by': repair.performed_by.get_full_name() if repair.performed_by else 'N/A',
                'time_started': repair.time_started.strftime('%H:%M') if repair.time_started else None,
                'time_completed': repair.time_completed.strftime('%H:%M') if repair.time_completed else None,
                'downtime_hours': downtime_hours,
                'labor_cost': float(repair.labor_cost),
                'total_parts_cost': float(repair.total_parts_cost),
                'additional_costs': float(repair.additional_costs),
                'total_cost': float(repair.get_total_cost()),
                'status': repair.status,
                'spare_parts': spare_parts,
            })

        # Calculate repair summary
        if repairs_qs.exists():
            total_cost = sum(r['total_cost'] for r in repairs)
            total_downtime = sum(r['downtime_hours'] for r in repairs)
            repair_summary = {
                'total_repairs': len(repairs),
                'total_cost': total_cost,
                'total_downtime': total_downtime,
                'average_repair_cost': round(total_cost / len(repairs), 2) if repairs else 0.0,
                'total_labor_cost': sum(r['labor_cost'] for r in repairs),
                'total_parts_cost': sum(r['total_parts_cost'] for r in repairs),
            }

        # Prepare response data
        response_data = {
            'equipment': {
                'id': str(equipment.id),
                'name': equipment.name if hasattr(equipment, 'name') else 'Unknown Equipment',
                'serial_number': equipment.serial_number if hasattr(equipment, 'serial_number') else 'N/A',
                'status': equipment.status if hasattr(equipment, 'status') else 'Unknown',
                'category': equipment.category.name if hasattr(equipment, 'category') and equipment.category else 'N/A',
                'manufacturer': equipment.manufacturer.name if hasattr(equipment, 'manufacturer') and equipment.manufacturer else 'N/A',
                'model': equipment.model if hasattr(equipment, 'model') else 'N/A',
                'description': str(equipment.description) if hasattr(equipment, 'description') else ''
            },
            'repairs': repairs,
            'summary': repair_summary,
            'calibration_certificates': calibration_certificates,
            'total_calibrations': len(calibration_certificates)
        }

        return JsonResponse(response_data)

    except Equipment.DoesNotExist:
        return JsonResponse({
            'error': 'Equipment not found',
            'message': f'No equipment found with ID: {equipment_id}'
        }, status=404)

    except Exception as e:
        # Log the full error for debugging
        import traceback
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in equipment_repair_details for equipment {equipment_id}: {str(e)}")
        logger.error(traceback.format_exc())

        return JsonResponse({
            'error': 'Internal server error',
            'message': str(e),
            'debug': traceback.format_exc() if settings.DEBUG else 'Enable DEBUG for details'
        }, status=500)
@login_required
def export_equipment_history(request, equipment_id):
    """Export equipment history to Excel with complete cost breakdown"""
    profile, workshops, selected_workshop, is_hod = get_user_workshop_context(request)

    if profile is None:
        messages.error(request, "You do not have a user profile assigned. Please contact the administrator.")
        return redirect('custom_login')

    try:
        equipment = Equipment.objects.select_related('workshop', 'category', 'description', 'manufacturer').filter(active_status=True).get(id=equipment_id)
    except Equipment.DoesNotExist:
        messages.error(request, "Equipment not found.")
        return redirect('equipment_dashboard')

    if not is_hod and equipment.workshop != selected_workshop:
        messages.error(request, "You do not have permission to export this equipment's history.")
        return redirect('equipment_dashboard')

    # Create workbook
    wb = Workbook()

    # Equipment Details Sheet
    ws_details = wb.active
    ws_details.title = "Equipment Details"
    ws_details.append(["Equipment Details"])
    ws_details.append(["Name", equipment.description.name if equipment.description else "Unknown"])
    ws_details.append(["Serial Number", equipment.serial_number or "N/A"])
    ws_details.append(["Category", equipment.category.name if equipment.category else "Uncategorized"])
    ws_details.append(["Workshop", equipment.workshop.name if equipment.workshop else "N/A"])
    ws_details.append(["Status", equipment.status or "Unknown"])
    ws_details.append(["Manufacturer", equipment.manufacturer.name if equipment.manufacturer else "Unknown"])
    ws_details.append(["Model", equipment.model or "N/A"])
    ws_details.append(["Description", str(equipment.description) if equipment.description else "No description"])

    # Repair History Sheet with complete costs
    ws_repairs = wb.create_sheet("Repair History")
    ws_repairs.append([
        "Date", "Job Card ID", "Description", "Action Taken", "Performed By",
        "Time Started", "Time Completed", "Downtime (Hours)",
        "Labor Cost (KSh)", "Parts Cost (KSh)", "Additional Costs (KSh)",
        "Additional Costs Description", "Total Cost (KSh)", "Spare Parts Details"
    ])

    repairs = jobcard.objects.filter(
        equipment=equipment,
        status='Approved',
        action_taken='Repair'
    ).prefetch_related('spare_parts__part').order_by('-date_issued')

    total_repairs = 0
    total_downtime = 0
    total_labor = 0
    total_parts = 0
    total_additional = 0
    total_cost = 0

    for repair in repairs:
        # Build spare parts details string
        spare_parts_details = []
        for sp in repair.spare_parts.all():
            part_name = sp.part.name.name if sp.part and sp.part.name else 'Unknown'
            part_cost = float(sp.get_total_cost())
            spare_parts_details.append(
                f"{part_name} (x{sp.quantity} @ KSh {float(sp.unit_cost):.2f} = KSh {part_cost:.2f})"
            )
        spare_parts_str = "; ".join(spare_parts_details) if spare_parts_details else "No parts used"

        # Calculate downtime
        downtime_hours = 0
        if repair.time_started and repair.time_completed:
            start_time = datetime.combine(repair.date_issued, repair.time_started)
            end_time = datetime.combine(repair.date_issued, repair.time_completed)
            downtime = end_time - start_time
            downtime_hours = round(downtime.total_seconds() / 3600, 2)

        # Get costs
        labor_cost = float(repair.labor_cost)
        parts_cost = float(repair.total_parts_cost)
        additional_costs = float(repair.additional_costs)
        repair_total = float(repair.get_total_cost())

        # Accumulate totals
        total_repairs += 1
        total_downtime += downtime_hours
        total_labor += labor_cost
        total_parts += parts_cost
        total_additional += additional_costs
        total_cost += repair_total

        ws_repairs.append([
            repair.date_issued.strftime('%Y-%m-%d'),
            str(repair.id),
            repair.job_description,
            repair.action_taken,
            repair.performed_by.get_full_name() if repair.performed_by else 'N/A',
            repair.time_started.strftime('%H:%M') if repair.time_started else 'N/A',
            repair.time_completed.strftime('%H:%M') if repair.time_completed else 'N/A',
            downtime_hours,
            labor_cost,
            parts_cost,
            additional_costs,
            repair.additional_costs_description or 'N/A',
            repair_total,
            spare_parts_str
        ])

    # Add summary rows
    ws_repairs.append([])  # Empty row
    ws_repairs.append(["SUMMARY"])
    ws_repairs.append(["Total Repairs:", total_repairs])
    ws_repairs.append(["Total Downtime (hours):", round(total_downtime, 2)])
    ws_repairs.append(["Total Labor Cost (KSh):", round(total_labor, 2)])
    ws_repairs.append(["Total Parts Cost (KSh):", round(total_parts, 2)])
    ws_repairs.append(["Total Additional Costs (KSh):", round(total_additional, 2)])
    ws_repairs.append(["Total Cost (KSh):", round(total_cost, 2)])
    if total_repairs > 0:
        ws_repairs.append(["Average Cost per Repair (KSh):", round(total_cost / total_repairs, 2)])

    # Cost Analysis Sheet
    ws_cost_analysis = wb.create_sheet("Cost Analysis")
    ws_cost_analysis.append(["Cost Analysis Summary"])
    ws_cost_analysis.append([])
    ws_cost_analysis.append(["Metric", "Value"])
    ws_cost_analysis.append(["Total Repairs", total_repairs])
    ws_cost_analysis.append(["Total Cost (KSh)", round(total_cost, 2)])
    ws_cost_analysis.append(["Labor Cost (KSh)", round(total_labor, 2)])
    ws_cost_analysis.append(["Parts Cost (KSh)", round(total_parts, 2)])
    ws_cost_analysis.append(["Additional Costs (KSh)", round(total_additional, 2)])
    ws_cost_analysis.append([])
    ws_cost_analysis.append(["Percentages"])
    if total_cost > 0:
        ws_cost_analysis.append(["Labor %", f"{(total_labor / total_cost * 100):.1f}%"])
        ws_cost_analysis.append(["Parts %", f"{(total_parts / total_cost * 100):.1f}%"])
        ws_cost_analysis.append(["Additional %", f"{(total_additional / total_cost * 100):.1f}%"])
    ws_cost_analysis.append([])
    ws_cost_analysis.append(["Averages"])
    if total_repairs > 0:
        ws_cost_analysis.append(["Avg Cost per Repair (KSh)", round(total_cost / total_repairs, 2)])
        ws_cost_analysis.append(["Avg Labor per Repair (KSh)", round(total_labor / total_repairs, 2)])
        ws_cost_analysis.append(["Avg Parts per Repair (KSh)", round(total_parts / total_repairs, 2)])
        ws_cost_analysis.append(["Avg Downtime per Repair (hrs)", round(total_downtime / total_repairs, 2)])

    # Calibration History Sheet (unchanged)
    ws_calibrations = wb.create_sheet("Calibration History")
    ws_calibrations.append([
        "Date", "Certificate Number", "Procedure", "Performed By",
        "Result", "Next Due Date", "Temperature", "Humidity", "Pressure"
    ])

    calibrations = CalibrationSession.objects.filter(
        device_serial=equipment.serial_number
    ).select_related('procedure', 'performed_by')

    for cal in calibrations:
        ws_calibrations.append([
            cal.timestamp.strftime('%Y-%m-%d %H:%M'),
            cal.certificate_number,
            cal.procedure.name,
            cal.performed_by.get_full_name(),
            "PASS" if cal.overall_pass else "FAIL",
            cal.next_calibration_due.strftime('%Y-%m-%d') if cal.next_calibration_due else "N/A",
            f"{cal.actual_temperature}Â°C" if cal.actual_temperature else "N/A",
            f"{cal.actual_humidity}%RH" if cal.actual_humidity else "N/A",
            f"{cal.actual_pressure}kPa" if cal.actual_pressure else "N/A"
        ])

    # Prepare response
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename=equipment_history_{equipment.serial_number}_with_costs.xlsx'

    # Save the workbook to the response
    wb.save(response)

    return response

@login_required
def export_equipment_category_detailed_pdf(request):
    """
    Export equipment report to PDF with professional formatting

    Features:
    - Summary statistics with status breakdown
    - Category-based grouping
    - Workshop and category filtering
    - Search functionality
    - Professional header/footer with watermark
    """
    # Get user context
    profile, workshops, selected_workshop, is_hod = get_user_workshop_context(request)

    if profile is None:
        return HttpResponse("Access denied - No user profile assigned", status=403)

    # ==========================================
    # BUILD BASE EQUIPMENT QUERYSET
    # ==========================================
    equipment_qs = Equipment.objects.select_related(
        "workshop", "category", "description", "manufacturer"
    ).filter(active_status=True)

    # ==========================================
    # APPLY WORKSHOP FILTER
    # ==========================================
    if is_hod:
        # HOD can filter by any workshop
        workshop_id = request.GET.get("workshop")
        if workshop_id:
            try:
                equipment_qs = equipment_qs.filter(workshop_id=workshop_id)
                selected_workshop = Workshop.objects.get(id=workshop_id)
            except Workshop.DoesNotExist:
                pass
    else:
        # Regular users see only their workshop
        if selected_workshop:
            equipment_qs = equipment_qs.filter(workshop=selected_workshop)

    # ==========================================
    # APPLY CATEGORY FILTER
    # ==========================================
    category_id = request.GET.get("category")
    selected_category = None
    download_all_categories = request.GET.get("download_all_categories", "false") == "true"

    if category_id and not download_all_categories:
        try:
            selected_category = EquipmentCategory.objects.get(id=category_id)
            equipment_qs = equipment_qs.filter(category=selected_category)
        except EquipmentCategory.DoesNotExist:
            pass

    # ==========================================
    # APPLY SEARCH FILTER
    # ==========================================
    search_query = request.GET.get("search", "")
    if search_query:
        equipment_qs = equipment_qs.filter(
            Q(description__name__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(manufacturer__name__icontains=search_query)
        )

    # ==========================================
    # DETERMINE REPORT TYPE
    # ==========================================
    # 'category' = show all categories grouped
    # 'detailed' = show single category or filtered results
    if download_all_categories or not selected_category:
        report_type = 'category'
    else:
        report_type = 'detailed'

    # ==========================================
    # GENERATE AND RETURN PDF
    # ==========================================
    return create_equipment_pdf_response(
        equipment_queryset=equipment_qs,
        selected_workshop=selected_workshop,
        selected_category=selected_category,
        search_query=search_query if search_query else None,
        report_type=report_type,
        generated_by=request.user
    )


# ==========================================
# HELPER FUNCTION (Keep your existing one)
# ==========================================


class ManufacturerPerformanceDocTemplate(BaseDocTemplate):
    """Custom document template with headers and footers"""

    def __init__(self, filename, **kwargs):
        BaseDocTemplate.__init__(self, filename, **kwargs)

        # Define page templates
        frame = Frame(
            x1=2*cm, y1=2*cm,
            width=A4[0]-4*cm, height=A4[1]-5*cm,
            leftPadding=0, bottomPadding=0,
            rightPadding=0, topPadding=0
        )

        template = PageTemplate(
            id='normal',
            frames=[frame],
            onPage=self.add_header_footer
        )

        self.addPageTemplates([template])

    def add_header_footer(self, canvas, doc):
        """Add header and footer to each page"""
        canvas.saveState()

        # Header
        self.draw_header(canvas, doc)

        # Footer
        self.draw_footer(canvas, doc)

        canvas.restoreState()

    def draw_header(self, canvas, doc):
        """Draw header with logo and title"""
        # Header background (light gray bar)
        canvas.setFillColor(colors.lightgrey)
        canvas.rect(0, A4[1]-3*cm, A4[0], 3*cm, fill=1, stroke=0)

        # Logo (if exists)
        logo_path = os.path.join(settings.STATIC_ROOT or settings.STATICFILES_DIRS[0], 'images', 'logo.png')
        if os.path.exists(logo_path):
            try:
                canvas.drawImage(logo_path, 1*cm, A4[1]-2.5*cm, width=2*cm, height=1.5*cm, mask='auto')
            except:
                # If logo fails to load, draw a placeholder
                canvas.setFillColor(colors.white)
                canvas.rect(1*cm, A4[1]-2.5*cm, 2*cm, 1.5*cm, fill=1, stroke=1)
                canvas.setFillColor(colors.black)
                canvas.setFont("Helvetica", 8)
                canvas.drawCentredString(2*cm, A4[1]-1.8*cm, "LOGO")
        else:
            # Draw placeholder logo box
            canvas.setFillColor(colors.white)
            canvas.rect(1*cm, A4[1]-2.5*cm, 2*cm, 1.5*cm, fill=1, stroke=1)
            canvas.setFillColor(colors.black)
            canvas.setFont("Helvetica", 8)
            canvas.drawCentredString(2*cm, A4[1]-1.8*cm, "LOGO")

        # Header title
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawCentredString(A4[0]/2, A4[1]-1.2*cm, "MANUFACTURER PERFORMANCE REPORT")

        # Department info
        canvas.setFont("Helvetica", 10)
        canvas.drawCentredString(A4[0]/2, A4[1]-1.6*cm, "Biomedical Engineering Department")

        # Contact info
        canvas.setFont("Helvetica", 7)
        contact_text = "Email: calibration@btwelve.hospital | Phone: +254-XXX-XXXX | ISO/IEC 17025:2017"
        canvas.drawCentredString(A4[0]/2, A4[1]-2.3*cm, contact_text)

    def draw_footer(self, canvas, doc):
        """Draw footer with page number and additional info"""
        # Footer line
        canvas.setStrokeColor(colors.lightgrey)
        canvas.line(1*cm, 1.5*cm, A4[0]-1*cm, 1.5*cm)

        # Page number (lighter and italic)
        canvas.setFillColor(colors.grey)
        canvas.setFont("Helvetica-Oblique", 8)
        page_text = f"Page {doc.page}"
        canvas.drawRightString(A4[0]-1*cm, 1*cm, page_text)

        # Footer left text (lighter and italic)
        canvas.setFillColor(colors.grey)
        canvas.setFont("Helvetica-Oblique", 8)
        canvas.drawString(1*cm, 1*cm, "Manufacturer Performance Analysis Report")

        # Footer center text (lighter and italic - watermark effect)
        canvas.setFillColor(colors.lightgrey)
        canvas.setFont("Helvetica-Oblique", 8)
        canvas.drawCentredString(A4[0]/2, 1*cm, "Confidential - Internal Use Only")



from .manufacturer_performance_pdf_generator import create_manufacturer_pdf_response
from Inventory.models import Equipment, Workshop


@login_required
def export_manufacturer_performance_pdf(request):
    """
    Export manufacturer performance report to PDF

    Features:
    - Executive summary with key metrics
    - Performance overview table sorted by uptime
    - Detailed analysis for top manufacturers
    - Performance ratings (Excellent/Good/Fair/Poor)
    - Color-coded metrics
    - Recommendations based on data
    - Professional header/footer with watermark
    """
    # Get user context
    profile, workshops, selected_workshop, is_hod = get_user_workshop_context(request)

    if profile is None:
        return HttpResponse("Access denied - No user profile assigned", status=403)

    # ==========================================
    # BUILD BASE EQUIPMENT QUERYSET
    # ==========================================
    equipment_qs = Equipment.objects.select_related(
        "workshop", "category", "description", "manufacturer"
    ).filter(active_status=True)

    # ==========================================
    # APPLY WORKSHOP FILTER
    # ==========================================
    if is_hod:
        # HOD can filter by any workshop
        workshop_id = request.GET.get("workshop")
        if workshop_id:
            try:
                equipment_qs = equipment_qs.filter(workshop_id=workshop_id)
                selected_workshop = Workshop.objects.get(id=workshop_id)
            except Workshop.DoesNotExist:
                pass
    else:
        # Regular users see only their workshop
        if selected_workshop:
            equipment_qs = equipment_qs.filter(workshop=selected_workshop)

    # ==========================================
    # CALCULATE MANUFACTURER PERFORMANCE
    # ==========================================
    manufacturer_performance = calculate_manufacturer_performance(equipment_qs)

    # ==========================================
    # GENERATE AND RETURN PDF
    # ==========================================
    return create_manufacturer_pdf_response(
        manufacturer_performance_data=manufacturer_performance,
        selected_workshop=selected_workshop,
        generated_by=request.user
    )

def get_user_workshop_context(request):
    """
    Helper function to get workshop context based on user role

    Returns:
        tuple: (profile, workshops, selected_workshop, is_hod)
    """
    try:
        profile = request.user.userprofile
    except AttributeError:
        return None, None, None, False

    if profile.role == 'HOD':
        workshops = Workshop.objects.all()
        selected_workshop_id = request.GET.get('workshop') or request.session.get('selected_workshop_id')
        if selected_workshop_id:
            try:
                selected_workshop = Workshop.objects.get(id=selected_workshop_id)
                request.session['selected_workshop_id'] = selected_workshop_id
            except Workshop.DoesNotExist:
                selected_workshop = None
        else:
            selected_workshop = None
        return profile, workshops, selected_workshop, True
    else:
        workshop = profile.workshop
        if not workshop:
            return profile, None, None, False
        return profile, None, workshop, False





def get_rating(value, metric_type):
    """Generate performance rating based on metric type and value"""
    if metric_type == 'uptime':
        if value >= 95:
            return "Excellent"
        elif value >= 85:
            return "Good"
        elif value >= 75:
            return "Fair"
        else:
            return "Poor"

    elif metric_type == 'frequency':
        if value <= 1:
            return "Excellent"
        elif value <= 2:
            return "Good"
        elif value <= 4:
            return "Fair"
        else:
            return "Poor"

    else:
        return "N/A"


def generate_recommendations(manufacturer_performance):
    """Generate recommendations based on performance data"""
    recommendations = []

    # Find best and worst performers
    if manufacturer_performance:
        best_uptime = max(manufacturer_performance.items(), key=lambda x: x[1]['avg_uptime'])
        worst_uptime = min(manufacturer_performance.items(), key=lambda x: x[1]['avg_uptime'])

        recommendations.append(
            f"Consider prioritizing equipment from {best_uptime[0]} (uptime: {best_uptime[1]['avg_uptime']:.1f}%) for future purchases."
        )

        if worst_uptime[1]['avg_uptime'] < 80:
            recommendations.append(
                f"Review maintenance protocols for {worst_uptime[0]} equipment (uptime: {worst_uptime[1]['avg_uptime']:.1f}%)."
            )

        # High repair frequency
        high_repair_freq = [name for name, data in manufacturer_performance.items() if data['repair_frequency'] > 3]
        if high_repair_freq:
            recommendations.append(
                f"Investigate frequent repairs for equipment from: {', '.join(high_repair_freq[:3])}."
            )

    recommendations.append("Regular performance reviews should be conducted quarterly to track improvements.")
    recommendations.append("Establish preferred vendor programs with top-performing manufacturers.")

    return recommendations


class EquipmentListDocTemplate(BaseDocTemplate):
    """Custom document template for equipment list with headers and footers"""

    def __init__(self, filename, **kwargs):
        BaseDocTemplate.__init__(self, filename, **kwargs)

        # Define page templates - using landscape for better table display
        frame = Frame(
            x1=2*cm, y1=2*cm,
            width=A4[1]-4*cm, height=A4[0]-5*cm,
            leftPadding=0, bottomPadding=0,
            rightPadding=0, topPadding=0
        )

        template = PageTemplate(
            id='normal',
            frames=[frame],
            onPage=self.add_header_footer
        )

        self.addPageTemplates([template])

    def add_header_footer(self, canvas, doc):
        """Add header and footer to each page"""
        canvas.saveState()

        # Header
        self.draw_header(canvas, doc)

        # Footer
        self.draw_footer(canvas, doc)

        canvas.restoreState()

    def draw_header(self, canvas, doc):
        """Draw header with logo and title"""
        # Adjust for landscape orientation
        width, height = A4[1], A4[0]

        # Header background
        canvas.setFillColor(colors.lightgrey)
        canvas.rect(0, height-3*cm, width, 3*cm, fill=1, stroke=0)

        # Logo placeholder
        canvas.setFillColor(colors.white)
        canvas.rect(1*cm, height-2.5*cm, 2*cm, 1.5*cm, fill=1, stroke=1)
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica", 8)
        canvas.drawCentredString(2*cm, height-1.8*cm, "LOGO")

        # Header title
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawCentredString(width/2, height-1.2*cm, "EQUIPMENT REPORT")

        # Department info
        canvas.setFont("Helvetica", 10)
        canvas.drawCentredString(width/2, height-1.6*cm, "Biomedical Engineering Department")

        # Contact info
        canvas.setFont("Helvetica", 7)
        contact_text = "Email: calibration@btwelve.hospital | Phone: +254-XXX-XXXX | ISO/IEC 17025:2017"
        canvas.drawCentredString(width/2, height-2.3*cm, contact_text)

    def draw_footer(self, canvas, doc):
        """Draw footer with page number and additional info"""
        width, height = A4[1], A4[0]

        # Footer line
        canvas.setStrokeColor(colors.lightgrey)
        canvas.line(1*cm, 1.5*cm, width-1*cm, 1.5*cm)

        # Page number
        canvas.setFillColor(colors.grey)
        canvas.setFont("Helvetica-Oblique", 8)
        page_text = f"Page {doc.page}"
        canvas.drawRightString(width-1*cm, 1*cm, page_text)

        # Footer left text
        canvas.setFillColor(colors.grey)
        canvas.setFont("Helvetica-Oblique", 8)
        canvas.drawString(1*cm, 1*cm, "Equipment Report")

        # Footer center text
        canvas.setFillColor(colors.lightgrey)
        canvas.setFont("Helvetica-Oblique", 8)
        canvas.drawCentredString(width/2, 1*cm, "Confidential - Internal Use Only")


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
