"""machineReports views — equipment repair detail view."""
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
