"""Bulk workshop import from Excel.

HOD only, matching :func:`workshop.views.create_workshop`. Uploads follow the
shared preview/commit contract in :mod:`core.excel_import`.
"""
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.views.decorators.http import require_GET, require_POST

from openpyxl import Workbook

from core import excel_import as xl
from workshop.models import Workshop

COLUMNS = {"name": "Workshop Name", "category": "Category"}

HEADER_ALIASES = {
    "workshopname": "name",
    "workshop": "name",
    "name": "name",
    "category": "category",
    "type": "category",
    "workshoptype": "category",
}

CATEGORY_LABELS = dict(Workshop.CATEGORY_CHOICES)
CATEGORY_ALIASES = {
    **{xl.normalize(label): value for value, label in Workshop.CATEGORY_CHOICES},
    **{xl.normalize(value): value for value, _ in Workshop.CATEGORY_CHOICES},
    "calibrationcentre": "calibration_center",
    "calibration": "calibration_center",
}

NAME_MAX_LENGTH = Workshop._meta.get_field("name").max_length


def _denied(request):
    profile = getattr(request.user, "userprofile", None)
    if profile is None or profile.role != "HOD":
        return xl.json_error("Only HODs can bulk-upload workshops.", 403)
    return None


@login_required
@require_GET
def download_workshop_import_template(request):
    denied = _denied(request)
    if denied:
        return denied

    wb = Workbook()
    ws = wb.active
    ws.title = "Workshops"
    xl.write_header(ws, list(COLUMNS.values()), width=34)

    existing = (
        Workshop.objects.filter(pending_delete=False).order_by("name").values_list("name", flat=True)
    )
    ranges = xl.write_reference(wb, [
        ("Existing Workshops", list(existing)),
        ("Category", list(CATEGORY_LABELS.values())),
    ])
    xl.add_dropdown(ws, 2, ranges.get("Category"))

    xl.write_instructions(wb, [
        "How to use this template",
        "",
        "1. Enter one workshop per row on the 'Workshops' sheet, starting at row 2.",
        "2. Do not rename, reorder or delete the header row.",
        f"3. Workshop Name is required (max {NAME_MAX_LENGTH} characters) and must be unique.",
        "4. Category must be one of: " + ", ".join(CATEGORY_LABELS.values()) + ".",
        "5. Workshops that already exist (see the 'Reference' sheet) are skipped, not changed.",
        f"6. Maximum {xl.MAX_ROWS} rows per upload.",
        "",
        "You will always see a preview of what will be imported before anything is saved.",
    ])
    return xl.workbook_response(wb, "workshop_import_template.xlsx")


@login_required
@require_POST
def upload_workshops_excel(request):
    denied = _denied(request)
    if denied:
        return denied
    return xl.run_import(request, what="Workshop", process=_process_workshops)


def _process_workshops(workbook, report):
    ws = xl.find_sheet(workbook, "Workshops", fallback_first=True)
    header_row, mapping = xl.locate_header(ws, HEADER_ALIASES, required=tuple(COLUMNS))
    if not header_row:
        raise xl.ImportFileError(xl.header_error(COLUMNS.values()))

    seen = {}
    for excel_row, values in xl.data_rows(ws, header_row, mapping, report):
        name = values["name"]
        category_text = values["category"]
        errors = []

        if not name:
            errors.append("Workshop Name is required.")
        elif len(name) > NAME_MAX_LENGTH:
            errors.append(f"Workshop Name exceeds {NAME_MAX_LENGTH} characters.")

        category = CATEGORY_ALIASES.get(xl.normalize(category_text))
        if not category:
            errors.append(
                f"Category '{category_text}' is not valid. Use: {', '.join(CATEGORY_LABELS.values())}."
                if category_text else "Category is required."
            )

        if name and name.lower() in seen:
            errors.append(f"'{name}' is duplicated in this file (also on row {seen[name.lower()]}).")
        elif name:
            seen[name.lower()] = excel_row

        if errors:
            report.record("error", row=excel_row, item=name, messages=errors)
            continue

        existing = Workshop.objects.filter(name__iexact=name).first()
        if existing and existing.pending_delete:
            report.record("error", row=excel_row, item=name, messages=[
                f"Workshop '{existing.name}' is pending deletion. Wait for it to sync before adding it again."
            ])
            continue
        if existing:
            report.record("skip", row=excel_row, item=name, messages=["Already exists - not changed."])
            continue

        try:
            with transaction.atomic():
                workshop = Workshop.objects.create(name=name, category=category)
        except Exception as exc:
            report.record("error", row=excel_row, item=name, messages=xl.error_messages(exc))
            continue

        report.saved.append(workshop)
        report.record("create", row=excel_row, item=name, messages=[CATEGORY_LABELS[category]])
