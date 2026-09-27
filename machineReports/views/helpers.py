"""machineReports views — shared workshop-context and performance/rating helpers."""
from uuid import UUID
from users.control import get_user_role
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
from CalSoft.models import CalibrationSession
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


def calculate_manufacturer_performance(equipment_qs):
    """
    Calculate performance metrics for each manufacturer

    Args:
        equipment_qs: QuerySet of Equipment objects

    Returns:
        dict: Performance metrics for each manufacturer

    Counts and costs are summed in the database. Building a model instance per
    repair (with its spare parts) took ~12 s for 12,000 machines and 36,000
    repairs; only the downtime needs per-row values, read as plain tuples.
    """
    from collections import defaultdict
    from datetime import datetime
    from decimal import Decimal

    from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum

    from jobcard.models import SparePartUsed, jobcard

    def name(value):
        return value or 'Unknown'

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

    active_equipment = equipment_qs.filter(active_status=True)

    for row in (active_equipment.order_by().values('manufacturer__name')
                .annotate(n=Count('id'), working=Count('id', filter=Q(status='Working')))):
        metrics = manufacturers[name(row['manufacturer__name'])]
        metrics['equipment_count'] += row['n']
        metrics['working_equipment'] += row['working']

    repairs = jobcard.objects.filter(equipment__in=active_equipment, action_taken='Repair', status='Approved')

    for maker, day, started, completed in repairs.values_list(
            'equipment__manufacturer__name', 'date_issued', 'time_started', 'time_completed').iterator(chunk_size=2000):
        metrics = manufacturers[name(maker)]
        metrics['total_repairs'] += 1
        if started and completed:
            metrics['total_downtime'] += (
                datetime.combine(day, completed) - datetime.combine(day, started)
            ).total_seconds() / 3600

    # Parts at the part's current unit cost, as before; a spare-part row whose
    # part was deleted contributes nothing.
    line_cost = ExpressionWrapper(F('quantity') * F('part__unit_cost'),
                                  output_field=DecimalField(max_digits=18, decimal_places=2))
    for row in (SparePartUsed.objects.filter(job_card__in=repairs, part__isnull=False).order_by()
                .values('job_card__equipment__manufacturer__name').annotate(cost=Sum(line_cost))):
        manufacturers[name(row['job_card__equipment__manufacturer__name'])]['total_repair_cost'] += float(
            row['cost'] or Decimal(0))

    # Calculate derived metrics
    for manufacturer, metrics in manufacturers.items():
        if metrics['equipment_count'] > 0:
            metrics['avg_uptime'] = (metrics['working_equipment'] / metrics['equipment_count']) * 100
            metrics['repair_frequency'] = metrics['total_repairs'] / metrics['equipment_count']
            if metrics['total_repairs'] > 0:
                metrics['avg_repair_cost'] = metrics['total_repair_cost'] / metrics['total_repairs']

    # Convert to regular dict to return
    return dict(manufacturers)


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

    if get_user_role(request.user) == 'HOD':
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
