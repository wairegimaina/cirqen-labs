"""Excel / PDF exports for tools and accessories."""
import logging
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from openpyxl import Workbook

from parts_tools.pdf_generators import AccessoriesPDFReport, ToolsPDFGenerator
from ..models import Tools, Accessories
from workshop.models import Workshop

logger = logging.getLogger(__name__)


@login_required
def export_excel_tools(request):
    """Export tools to Excel."""
    profile = request.user.userprofile
    if profile.role == 'HOD':
        tools = Tools.objects.filter(active_status=True).select_related('name', 'manufacturer', 'workshop')
    else:
        tools = Tools.objects.filter(workshop=profile.workshop, active_status=True).select_related('name', 'manufacturer', 'workshop')

    wb = Workbook()
    ws = wb.active
    ws.title = "Tools"
    ws.append(["Name", "Model", "Serial Number", "Manufacturer", "Workshop"])

    for tool in tools:
        ws.append([
            tool.name.name if tool.name else '',
            tool.model or '',
            tool.serial_number or '',
            tool.manufacturer.name if tool.manufacturer else '',
            tool.workshop.name if tool.workshop else 'Global',
        ])

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=tools.xlsx'
    wb.save(response)
    return response


@login_required
def export_excel_accessories(request):
    """Export accessories to Excel."""
    profile = request.user.userprofile
    if profile.role == 'HOD':
        accessories = Accessories.objects.filter(active_status=True).select_related('name', 'manufacturer', 'equipment_description', 'workshop')
    else:
        accessories = Accessories.objects.filter(workshop=profile.workshop, active_status=True).select_related('name', 'manufacturer', 'equipment_description', 'workshop')

    wb = Workbook()
    ws = wb.active
    ws.title = "Accessories"
    ws.append(["Name", "Equipment Description", "Manufacturer", "Note", "Stock Count", "Unit Cost", "Workshop"])

    for accessory in accessories:
        ws.append([
            accessory.name.name if accessory.name else '',
            accessory.equipment_description.name if accessory.equipment_description else '',
            accessory.manufacturer.name if accessory.manufacturer else '',
            accessory.note or '',
            accessory.stock_count,
            float(accessory.unit_cost),
            accessory.workshop.name if accessory.workshop else 'Global',
        ])

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=accessories.xlsx'
    wb.save(response)
    return response


@login_required
def export_pdf_tools(request):
    """Export tools to PDF."""
    profile = request.user.userprofile
    workshop_name = None

    if profile.role == 'HOD':
        selected_workshop_id = request.GET.get('workshop_id') or request.session.get('selected_workshop_id')
        if selected_workshop_id:
            try:
                selected_workshop = Workshop.objects.get(id=selected_workshop_id)
                tools = Tools.objects.filter(workshop=selected_workshop, active_status=True)
                workshop_name = selected_workshop.name
            except Workshop.DoesNotExist:
                tools = Tools.objects.filter(active_status=True)
                workshop_name = "All Workshops"
        else:
            tools = Tools.objects.filter(active_status=True)
            workshop_name = "All Workshops"
    else:
        tools = Tools.objects.filter(workshop=profile.workshop, active_status=True)
        workshop_name = profile.workshop.name if profile.workshop else None

    pdf_generator = ToolsPDFGenerator(tools, workshop_name)
    buffer = pdf_generator.generate_pdf()

    timestamp = datetime.now().strftime('%Y%m%d')
    safe_name = workshop_name.replace(' ', '_') if workshop_name else 'report'
    filename = f"tools_report_{safe_name}_{timestamp}.pdf"

    response = HttpResponse(content_type='application/pdf', content=buffer.getvalue())
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    buffer.close()
    return response


@login_required
def export_pdf_accessories(request):
    """Export accessories to PDF."""
    profile = request.user.userprofile
    workshop_name = None

    if profile.role == 'HOD':
        selected_workshop_id = request.GET.get('workshop_id') or request.session.get('selected_workshop_id')
        if selected_workshop_id:
            try:
                selected_workshop = Workshop.objects.get(id=selected_workshop_id)
                accessories = Accessories.objects.filter(workshop=selected_workshop, active_status=True)
                workshop_name = selected_workshop.name
            except Workshop.DoesNotExist:
                accessories = Accessories.objects.filter(active_status=True)
                workshop_name = "All Workshops"
        else:
            accessories = Accessories.objects.filter(active_status=True)
            workshop_name = "All Workshops"
    else:
        accessories = Accessories.objects.filter(workshop=profile.workshop, active_status=True)
        workshop_name = profile.workshop.name if profile.workshop else None

    pdf_generator = AccessoriesPDFReport(accessories, workshop_name)
    buffer = pdf_generator.generate_pdf()

    timestamp = datetime.now().strftime('%Y%m%d')
    safe_name = workshop_name.replace(' ', '_') if workshop_name else 'report'
    filename = f"accessories_report_{safe_name}_{timestamp}.pdf"

    response = HttpResponse(content_type='application/pdf', content=buffer.getvalue())
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    buffer.close()
    return response
