"""Warranties module: one warranty per device and supplier, kept off the inventory table.

Inventory Equipment -> Warranty -> Supplier. The list and detail pages read the
device and the supplier through the foreign keys, so a supplier's new phone
number shows here as soon as it is saved in Suppliers. Status is worked out
from the dates (Warranty.status); "No Warranty" is a device with no warranty
recorded.

Everyone who can see a device's inventory can see its warranty (core.scoping);
technologists and HODs add and change warranties.
"""
import datetime
import logging
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.timezone import localdate
from django.views.decorators.http import require_GET, require_POST

from openpyxl import Workbook

from core import excel_import as xl
from core.scoping import for_user, get_for_user_or_404
from Inventory.models import Equipment, Supplier, Warranty
from users.control import get_user_role, role_required

logger = logging.getLogger(__name__)

_EDITORS = ('Tech', 'HOD')
_DENIED = "Only technologists and HODs can change warranties."
PAGE_SIZE = 50


# ── Form handling (shared with the Add Equipment modal) ─────────────────────


def warranty_form_present(post):
    """True when the Add Equipment form's optional warranty section was filled in."""
    return any((post.get(f) or '').strip() for f in (
        'warranty_supplier', 'warranty_start_date', 'warranty_period_months', 'warranty_expiry_date'))


def fill_warranty(warranty, post):
    """Copy the ``warranty_*`` fields from a form onto ``warranty``. Raises ValidationError."""
    errors = {}
    supplier_id = (post.get('warranty_supplier') or '').strip()
    if not supplier_id:
        errors['supplier'] = "Select the supplier."
    else:
        try:
            warranty.supplier = Supplier.objects.get(id=supplier_id, pending_delete=False)
        except (Supplier.DoesNotExist, ValueError, ValidationError):
            errors['supplier'] = "Select a supplier from the list."

    for field in ('start_date', 'expiry_date'):
        raw = (post.get(f'warranty_{field}') or '').strip()
        try:
            value = parse_date(raw) if raw else None
        except ValueError:
            value = None
        if raw and value is None:
            errors[field] = f"Invalid {field.replace('_', ' ')}."
        setattr(warranty, field, value)

    months = (post.get('warranty_period_months') or '').strip()
    if months and not months.isdigit():
        errors['period_months'] = "Warranty period must be a whole number of months."
    warranty.period_months = int(months) if months.isdigit() and int(months) else None
    # A changed period recomputes the expiry unless one was typed in.
    if warranty.period_months and not (post.get('warranty_expiry_date') or '').strip():
        warranty.expiry_date = None

    warranty.coverage = [c for c in post.getlist('warranty_coverage') if c in Warranty.COVERAGE_LABELS]
    warranty.coverage_other = (post.get('warranty_coverage_other') or '').strip()[:255]
    warranty.reference = (post.get('warranty_reference') or '').strip()[:100]
    warranty.terms = (post.get('warranty_terms') or '').strip()
    if errors:
        raise ValidationError(errors)


def error_text(exc):
    if hasattr(exc, 'message_dict'):
        return ' '.join(m for msgs in exc.message_dict.values() for m in msgs)
    return ' '.join(exc.messages)


def form_context():
    """What the warranty form fields need (templates/Inventory/_warranty_fields.html)."""
    return {
        'warranty_suppliers': Supplier.objects.filter(active_status=True, pending_delete=False).order_by('name'),
        'coverage_choices': Warranty.COVERAGE_CHOICES,
    }


def _safe_next(request):
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return reverse('warranty_list')


def _can_edit(user):
    return get_user_role(user) in _EDITORS


# ── Pages ────────────────────────────────────────────────────────────────────


def _visible_equipment(user):
    return for_user(Equipment.objects.filter(active_status=True, pending_delete=False), user)


def _live_warranties():
    return Warranty.objects.filter(active_status=True, pending_delete=False)


@login_required
@require_GET
def warranty_list(request):
    today = localdate()
    soon = today + datetime.timedelta(days=Warranty.expiring_soon_days())
    equipment = _visible_equipment(request.user)
    warranties = _live_warranties().filter(equipment__in=equipment).select_related(
        'equipment__description', 'equipment__department', 'equipment__manufacturer', 'supplier')
    uncovered = equipment.exclude(Exists(_live_warranties().filter(equipment=OuterRef('pk'))))

    counts = {
        'active': warranties.filter(expiry_date__gt=soon).count(),
        'expiring': warranties.filter(expiry_date__gte=today, expiry_date__lte=soon).count(),
        'expired': warranties.filter(expiry_date__lt=today).count(),
        'none': uncovered.count(),
    }

    status = request.GET.get('status', '')
    q = request.GET.get('q', '').strip()
    supplier_filter = request.GET.get('supplier', '')

    if status == 'none':
        rows = uncovered.select_related('description', 'department', 'manufacturer').order_by('description__name')
        if q:
            rows = rows.filter(Q(description__name__icontains=q) | Q(serial_number__icontains=q)
                               | Q(asset_tag__icontains=q) | Q(model__icontains=q))
    else:
        rows = warranties
        if status == 'active':
            rows = rows.filter(expiry_date__gt=soon)
        elif status == 'expiring':
            rows = rows.filter(expiry_date__gte=today, expiry_date__lte=soon)
        elif status == 'expired':
            rows = rows.filter(expiry_date__lt=today)
        if supplier_filter:
            rows = rows.filter(supplier_id=supplier_filter) if _is_uuid(supplier_filter) else rows.none()
        if q:
            rows = rows.filter(Q(equipment__description__name__icontains=q) | Q(equipment__serial_number__icontains=q)
                               | Q(equipment__asset_tag__icontains=q) | Q(supplier__name__icontains=q)
                               | Q(reference__icontains=q))
        rows = rows.order_by('expiry_date')

    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get('page'))
    can_edit = _can_edit(request.user)
    context = {
        'show_sidebar': True,
        'page': page,
        'showing_uncovered': status == 'none',
        'counts': counts,
        'status': status,
        'q': q,
        'supplier_filter': supplier_filter,
        'suppliers': Supplier.objects.filter(pending_delete=False).order_by('name'),
        'expiring_soon_days': Warranty.expiring_soon_days(),
        'can_edit': can_edit,
    }
    if can_edit:
        context.update(form_context())
        context['equipment_choices'] = equipment.select_related('description').order_by(
            'description__name', 'serial_number')
        context['warranty_template_url'] = reverse('warranty_import_template')
        context['warranty_upload_url'] = reverse('warranty_import_upload')
    return render(request, 'Inventory/warranties.html', context)


def _is_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _detail(request, equipment, warranty):
    history = list(_live_warranties().filter(equipment=equipment).select_related('supplier')
                   .order_by('-expiry_date'))
    context = {
        'show_sidebar': True,
        'equipment': equipment,
        'warranty': warranty,
        'history': history,
        'can_edit': _can_edit(request.user),
        'expiring_soon_days': Warranty.expiring_soon_days(),
    }
    if context['can_edit']:
        context.update(form_context())
    return render(request, 'Inventory/warranty_detail.html', context)


@login_required
@require_GET
def warranty_detail(request, warranty_id):
    warranty = get_for_user_or_404(
        _live_warranties().select_related('equipment__description', 'equipment__department__workshop',
                                          'equipment__manufacturer', 'supplier', 'created_by'),
        request.user, id=warranty_id)
    return _detail(request, warranty.equipment, warranty)


@login_required
@require_GET
def equipment_warranty(request, equipment_id):
    """A device's warranty page: its current warranty, or a prompt to add one."""
    equipment = get_for_user_or_404(
        Equipment.objects.select_related('description', 'department__workshop', 'manufacturer').prefetch_related(
            Prefetch('warranties', queryset=_live_warranties().select_related('supplier', 'created_by'))),
        request.user, id=equipment_id)
    return _detail(request, equipment, equipment.current_warranty())


@login_required
@role_required(*_EDITORS, redirect_to='warranty_list', message=_DENIED)
@require_POST
def warranty_save(request, warranty_id=None):
    if warranty_id:
        warranty = get_for_user_or_404(_live_warranties(), request.user, id=warranty_id)
    else:
        equipment_id = request.POST.get('equipment')
        try:
            equipment = _visible_equipment(request.user).get(id=equipment_id)
        except (Equipment.DoesNotExist, ValueError, ValidationError):
            messages.error(request, "Select the equipment this warranty is for.")
            return redirect(_safe_next(request))
        warranty = Warranty(equipment=equipment, created_by=request.user)
    try:
        fill_warranty(warranty, request.POST)
        warranty.save()
    except ValidationError as exc:
        messages.error(request, error_text(exc))
        return redirect(_safe_next(request))
    logger.info("Warranty %s for %s saved by %s", warranty.id, warranty.equipment.serial_number,
                request.user.username)
    messages.success(request, f"Warranty for {warranty.equipment.description.name} "
                              f"({warranty.equipment.serial_number}) saved.", extra_tags="success")
    return redirect('warranty_detail', warranty_id=warranty.id)


@login_required
@role_required(*_EDITORS, redirect_to='warranty_list', message=_DENIED)
@require_POST
def warranty_remove(request, warranty_id):
    """Take a warranty entered in error off the list (kept for sync, like other deletes)."""
    warranty = get_for_user_or_404(_live_warranties(), request.user, id=warranty_id)
    warranty.active_status = False
    warranty.save()
    messages.success(request, "Warranty removed.", extra_tags="success")
    return redirect('equipment_warranty', equipment_id=warranty.equipment_id)


# ── Excel import (same preview/commit contract as the inventory upload) ─────

COLUMNS = [
    ('serial_number', 'Serial Number'),
    ('supplier', 'Supplier'),
    ('start_date', 'Start Date'),
    ('period_months', 'Period (Months)'),
    ('expiry_date', 'Expiry Date'),
    ('coverage', 'Covers'),
    ('coverage_other', 'Other Coverage'),
    ('reference', 'Warranty Reference'),
    ('terms', 'Terms / Notes'),
]
HEADER_ALIASES = {
    'serialnumber': 'serial_number', 'serialno': 'serial_number', 'serial': 'serial_number', 'sn': 'serial_number',
    'hospitalassetno': 'asset_tag', 'assetno': 'asset_tag', 'assettag': 'asset_tag', 'assetnumber': 'asset_tag',
    'supplier': 'supplier', 'vendor': 'supplier', 'suppliername': 'supplier',
    'startdate': 'start_date', 'warrantystart': 'start_date', 'warrantystartdate': 'start_date',
    'purchasedate': 'start_date', 'datefrom': 'start_date',
    'periodmonths': 'period_months', 'period': 'period_months', 'months': 'period_months',
    'warrantymonths': 'period_months', 'warrantyperiodmonths': 'period_months', 'warrantyperiod': 'period_months',
    'expirydate': 'expiry_date', 'expiry': 'expiry_date', 'warrantyexpiry': 'expiry_date',
    'warrantyexpirydate': 'expiry_date', 'enddate': 'expiry_date', 'warrantyenddate': 'expiry_date',
    'covers': 'coverage', 'coverage': 'coverage', 'warrantycovers': 'coverage',
    'othercoverage': 'coverage_other',
    'warrantyreference': 'reference', 'reference': 'reference', 'contractno': 'reference',
    'certificateno': 'reference',
    'termsnotes': 'terms', 'terms': 'terms', 'notes': 'terms',
}
_COVERAGE_BY_TEXT = {
    **{xl.normalize(label): code for code, label in Warranty.COVERAGE_CHOICES},
    **{xl.normalize(code): code for code, _ in Warranty.COVERAGE_CHOICES},
    'parts': 'parts', 'spareparts': 'parts', 'labor': 'labour',
    'manufacturingdefect': 'manufacturing_defects', 'defects': 'manufacturing_defects',
    'electrical': 'electrical', 'software': 'software', 'firmware': 'software', 'ppm': 'preventive_maintenance',
    'onsite': 'onsite_service', 'replacement': 'replacement_unit', 'loanunit': 'replacement_unit',
}


def parse_coverage(text):
    """'Parts, Labour; other thing' -> (['parts', 'labour'], 'other thing')."""
    codes, other = [], []
    for part in str(text or '').replace(';', ',').replace('+', ',').split(','):
        part = part.strip()
        if not part:
            continue
        code = _COVERAGE_BY_TEXT.get(xl.normalize(part))
        if code:
            codes.append(code)
        else:
            other.append(part)
    return codes, ', '.join(other)


@login_required
@role_required(*_EDITORS, redirect_to='warranty_list', message=_DENIED)
@require_GET
def warranty_import_template(request):
    wb = Workbook()
    ws = wb.active
    ws.title = "Warranties"
    xl.write_header(ws, [label for _, label in COLUMNS])
    suppliers = list(Supplier.objects.filter(active_status=True, pending_delete=False)
                     .order_by('name').values_list('name', flat=True))
    serials = list(_visible_equipment(request.user).order_by('serial_number').values_list('serial_number', flat=True))
    ranges = xl.write_reference(wb, [
        ("Suppliers", suppliers),
        ("Coverage options", [label for _, label in Warranty.COVERAGE_CHOICES]),
        ("Your equipment serial numbers", serials),
    ])
    xl.add_dropdown(ws, 2, ranges.get("Suppliers"))
    xl.write_instructions(wb, [
        "How to use this template",
        "",
        "1. One warranty per row on the 'Warranties' sheet. Do not rename or delete the header row.",
        "2. Serial Number must match equipment already in your inventory (a Hospital Asset No. column works too).",
        "3. Supplier must be one of your suppliers (see 'Reference'); tick 'Create missing suppliers' to add new ones.",
        "4. Start Date is required. Give Period (Months), Expiry Date, or both.",
        "5. Covers: a comma-separated list, e.g. 'Parts replacement, Labour, Manufacturing defects'.",
        "   Anything not in the 'Coverage options' list is kept as other coverage.",
        "6. A warranty that already exists for the device with the same start and expiry date is skipped.",
        f"7. Maximum {xl.MAX_ROWS} rows per upload.",
        "",
        "You will always see a preview of what will be imported before anything is saved.",
    ])
    return xl.workbook_response(wb, "warranty_import_template.xlsx")


@login_required
@role_required(*_EDITORS, redirect_to='warranty_list', message=_DENIED)
@require_POST
def warranty_import_upload(request):
    create_missing = request.POST.get('create_missing', 'false').lower() == 'true'
    return xl.run_import(
        request, what="Warranty",
        process=lambda workbook, report: _process_warranties(workbook, report, request.user, create_missing),
    )


def _process_warranties(workbook, report, user, create_missing):
    ws = xl.find_sheet(workbook, "Warranties", fallback_first=True)
    header_row, mapping = xl.locate_header(ws, HEADER_ALIASES, required=('supplier', 'start_date'))
    if not header_row or not {'serial_number', 'asset_tag'} & set(mapping.values()):
        raise xl.ImportFileError(xl.header_error(['Serial Number', 'Supplier', 'Start Date']))

    equipment_qs = _visible_equipment(user)
    for excel_row, values in xl.data_rows(ws, header_row, mapping, report,
                                          raw_fields=('start_date', 'expiry_date')):
        serial = values.get('serial_number', '').upper()
        asset = values.get('asset_tag', '')
        item = serial or asset
        errors = []

        equipment = None
        if serial:
            equipment = equipment_qs.filter(serial_number__iexact=serial).select_related('description').first()
        elif asset:
            matches = list(equipment_qs.filter(asset_tag__iexact=asset).select_related('description')[:2])
            equipment = matches[0] if len(matches) == 1 else None
            if len(matches) > 1:
                errors.append(f"More than one device has Hospital Asset No. '{asset}'; use the serial number.")
        if not item:
            errors.append("Serial Number is required.")
        elif equipment is None and not errors:
            errors.append(f"No equipment with {'serial number' if serial else 'asset number'} '{item}' "
                          "in your inventory.")
        if equipment:
            item = f"{equipment.description.name} ({equipment.serial_number})"

        dates = {}
        for field, label in (('start_date', 'Start Date'), ('expiry_date', 'Expiry Date')):
            try:
                dates[field] = xl.parse_date(values.get(field))
            except ValueError as exc:
                errors.append(f"{label}: {exc}")
        months = values.get('period_months', '')
        if months and not months.isdigit():
            errors.append(f"Period (Months): '{months}' is not a whole number.")

        supplier_name = values.get('supplier', '')
        supplier = Supplier.objects.filter(name__iexact=supplier_name, pending_delete=False).first() \
            if supplier_name else None
        if not supplier_name:
            errors.append("Supplier is required.")
        elif supplier is None and not create_missing:
            errors.append(f"Supplier '{supplier_name}' does not exist. Add it in Suppliers first, or tick "
                          "'Create missing suppliers'.")

        if errors:
            report.record('error', row=excel_row, item=item, messages=errors)
            continue

        coverage, other = parse_coverage(values.get('coverage', ''))
        if values.get('coverage_other'):
            other = ', '.join(filter(None, [other, values['coverage_other']]))
        period = int(months) if months and int(months) else None
        expiry = dates['expiry_date'] or (Warranty.add_months(dates['start_date'], period)
                                          if dates['start_date'] and period else None)
        # Same device, same dates: already recorded.
        if expiry and _live_warranties().filter(equipment=equipment, start_date=dates['start_date'],
                                                expiry_date=expiry).exists():
            report.record('skip', row=excel_row, item=item,
                          messages=["This warranty is already recorded - not changed."])
            continue
        new_supplier = None
        try:
            with transaction.atomic():
                if supplier is None:
                    new_supplier = supplier = Supplier(name=supplier_name)
                    supplier.save()
                warranty = Warranty(
                    equipment=equipment, supplier=supplier, created_by=user,
                    start_date=dates['start_date'], expiry_date=dates['expiry_date'], period_months=period,
                    coverage=coverage, coverage_other=other[:255],
                    reference=values.get('reference', '')[:100], terms=values.get('terms', ''),
                )
                warranty.save()
        except Exception as exc:
            report.record('error', row=excel_row, item=item, messages=xl.error_messages(exc))
            continue
        if new_supplier:
            report.saved.append(new_supplier)
            report.add_created("Suppliers", new_supplier.name)
        report.saved.append(warranty)
        report.record('create', row=excel_row, item=item, messages=[
            f"{warranty.period_display}, expires {warranty.expiry_date:%d %b %Y}, {supplier.name}"])
