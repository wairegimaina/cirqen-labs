"""Supplier register. Warranties pick their supplier from here, so contact
details are kept in one place (Inventory.views.warranties)."""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from openpyxl import Workbook

from core import excel_import as xl
from Inventory.models import Supplier
from users.control import role_required

logger = logging.getLogger(__name__)

_FIELDS = ('name', 'contact_person', 'phone', 'email', 'address', 'warranty_terms')


def _fill(supplier, post):
    for field in _FIELDS:
        setattr(supplier, field, post.get(field, '').strip())
    months = post.get('default_warranty_months', '').strip()
    if months and not months.isdigit():
        raise ValidationError({'default_warranty_months': 'Warranty months must be a whole number.'})
    supplier.default_warranty_months = int(months) if months else None


def _error_text(exc):
    if hasattr(exc, 'message_dict'):
        return '; '.join(m for msgs in exc.message_dict.values() for m in msgs)
    return '; '.join(exc.messages)


@login_required
@role_required('Tech', 'HOD', redirect_to='inventory', message="Only technologists and HODs manage suppliers.")
def supplier_list(request):
    suppliers = (
        Supplier.objects.filter(pending_delete=False)
        .annotate(warranty_count=Count('warranties', filter=Q(warranties__active_status=True,
                                                              warranties__pending_delete=False)))
        .order_by('-active_status', 'name')
    )
    return render(request, 'Inventory/suppliers.html', {
        'show_sidebar': True,
        'suppliers': suppliers,
        'supplier_template_url': reverse('supplier_import_template'),
        'supplier_upload_url': reverse('supplier_import_upload'),
    })


@login_required
@role_required('Tech', 'HOD', redirect_to='inventory', message="Only technologists and HODs manage suppliers.")
@require_POST
def supplier_save(request, supplier_id=None):
    supplier = get_object_or_404(Supplier, id=supplier_id) if supplier_id else Supplier()
    try:
        _fill(supplier, request.POST)
        supplier.save()
    except ValidationError as exc:
        messages.error(request, _error_text(exc))
        return redirect('supplier_list')
    logger.info("Supplier %s saved by %s", supplier.name, request.user.username)
    messages.success(request, f"Supplier '{supplier.name}' saved.", extra_tags="success")
    return redirect('supplier_list')


@login_required
@role_required('Tech', 'HOD', redirect_to='inventory', message="Only technologists and HODs manage suppliers.")
@require_POST
def supplier_toggle_active(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    supplier.active_status = not supplier.active_status
    supplier.save()
    state = 'reactivated' if supplier.active_status else 'deactivated'
    messages.success(request, f"Supplier '{supplier.name}' {state}.", extra_tags="success")
    return redirect('supplier_list')


# ── Excel import (same preview/commit contract as the inventory upload) ─────

COLUMNS = [
    ('name', 'Supplier Name'),
    ('contact_person', 'Contact Person'),
    ('phone', 'Phone'),
    ('email', 'Email'),
    ('address', 'Address'),
    ('default_warranty_months', 'Default Warranty (Months)'),
    ('warranty_terms', 'Warranty Terms'),
]
HEADER_ALIASES = {
    'suppliername': 'name', 'supplier': 'name', 'name': 'name', 'vendor': 'name', 'company': 'name',
    'contactperson': 'contact_person', 'contact': 'contact_person', 'contactname': 'contact_person',
    'phone': 'phone', 'phonenumber': 'phone', 'telephone': 'phone', 'tel': 'phone', 'mobile': 'phone',
    'email': 'email', 'emailaddress': 'email',
    'address': 'address', 'location': 'address',
    'defaultwarrantymonths': 'default_warranty_months', 'warrantymonths': 'default_warranty_months',
    'defaultwarranty': 'default_warranty_months',
    'warrantyterms': 'warranty_terms', 'terms': 'warranty_terms',
}
MAX_LENGTHS = {f.name: f.max_length for f in Supplier._meta.fields if getattr(f, 'max_length', None)}


@login_required
@role_required('Tech', 'HOD', redirect_to='inventory', message="Only technologists and HODs manage suppliers.")
@require_GET
def supplier_import_template(request):
    wb = Workbook()
    ws = wb.active
    ws.title = "Suppliers"
    xl.write_header(ws, [label for _, label in COLUMNS])
    xl.write_reference(wb, [("Existing suppliers", list(
        Supplier.objects.filter(pending_delete=False).order_by('name').values_list('name', flat=True)))])
    xl.write_instructions(wb, [
        "How to use this template",
        "",
        "1. One supplier per row on the 'Suppliers' sheet. Do not rename or delete the header row.",
        "2. Supplier Name is required and must be unique. Suppliers that already exist are skipped.",
        "3. Default Warranty (Months) and Warranty Terms pre-fill new warranties from this supplier.",
        f"4. Maximum {xl.MAX_ROWS} rows per upload.",
        "",
        "You will always see a preview of what will be imported before anything is saved.",
    ])
    return xl.workbook_response(wb, "supplier_import_template.xlsx")


@login_required
@role_required('Tech', 'HOD', redirect_to='inventory', message="Only technologists and HODs manage suppliers.")
@require_POST
def supplier_import_upload(request):
    return xl.run_import(request, what="Supplier", process=_process_suppliers)


def _process_suppliers(workbook, report):
    ws = xl.find_sheet(workbook, "Suppliers", fallback_first=True)
    header_row, mapping = xl.locate_header(ws, HEADER_ALIASES, required=('name',))
    if not header_row:
        raise xl.ImportFileError(xl.header_error(['Supplier Name']))

    seen = {}
    for excel_row, values in xl.data_rows(ws, header_row, mapping, report):
        name = values.get('name', '')
        errors = []
        if not name:
            errors.append("Supplier Name is required.")
        elif name.lower() in seen:
            errors.append(f"'{name}' is duplicated in this file (also on row {seen[name.lower()]}).")
        else:
            seen[name.lower()] = excel_row
        for field, label in COLUMNS:
            limit = MAX_LENGTHS.get(field)
            if limit and len(values.get(field, '')) > limit:
                errors.append(f"{label} exceeds {limit} characters.")
        months = values.get('default_warranty_months', '')
        if months and not months.isdigit():
            errors.append(f"Default Warranty (Months): '{months}' is not a whole number.")
        if errors:
            report.record('error', row=excel_row, item=name, messages=errors)
            continue

        if Supplier.objects.filter(name__iexact=name, pending_delete=False).exists():
            report.record('skip', row=excel_row, item=name, messages=["Already exists - not changed."])
            continue
        try:
            with transaction.atomic():
                supplier = Supplier(**{f: values.get(f, '') for f, _ in COLUMNS if f != 'default_warranty_months'})
                supplier.default_warranty_months = int(months) if months else None
                supplier.save()
        except Exception as exc:
            report.record('error', row=excel_row, item=name, messages=xl.error_messages(exc))
            continue
        report.saved.append(supplier)
        report.record('create', row=excel_row, item=name)
