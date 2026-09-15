"""Bulk department import from Excel.

Departments are added to the workshop whose department page the upload was
started from, with the permissions of
:func:`Inventory.views.departments.create_department`: HODs may add to any
workshop, Technologists only to their own. Uploads follow the shared
preview/commit contract in :mod:`core.excel_import`.
"""
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.views.decorators.http import require_GET, require_POST

from openpyxl import Workbook

from core import excel_import as xl
from Inventory.models import Department
from workshop.models import Workshop

COLUMN_LABEL = "Department Name"

HEADER_ALIASES = {
    "departmentname": "name",
    "department": "name",
    "dept": "name",
    "name": "name",
}

NAME_MAX_LENGTH = Department._meta.get_field("name").max_length


def _import_scope(request, workshop_id):
    """Resolve the target workshop. Returns ``(workshop, error_response)``."""
    profile = getattr(request.user, "userprofile", None)
    if profile is None:
        return None, xl.json_error("User profile not found.", 403)
    if profile.role == "HOD":
        workshop = Workshop.objects.filter(id=workshop_id, pending_delete=False).first()
        if workshop is None:
            return None, xl.json_error("Workshop not found.", 404)
        return workshop, None
    if profile.role == "Tech":
        if not profile.workshop_id:
            return None, xl.json_error("Your profile is not associated with a workshop.", 403)
        if profile.workshop_id != workshop_id:
            return None, xl.json_error("You can only add departments to your own workshop.", 403)
        return profile.workshop, None
    return None, xl.json_error("Only HODs and Technologists can bulk-upload departments.", 403)


@login_required
@require_GET
def download_department_import_template(request, workshop_id):
    workshop, denied = _import_scope(request, workshop_id)
    if denied:
        return denied

    wb = Workbook()
    ws = wb.active
    ws.title = "Departments"
    xl.write_header(ws, [COLUMN_LABEL], width=40)

    existing = Department.objects.filter(workshop=workshop).order_by("name").values_list("name", flat=True)
    xl.write_reference(wb, [(f"Departments in {workshop.name}", list(existing))])

    xl.write_instructions(wb, [
        "How to use this template",
        "",
        f"1. Enter one department per row on the 'Departments' sheet. They are added to {workshop.name}.",
        "2. Do not rename or delete the header row.",
        f"3. Department Name is required (max {NAME_MAX_LENGTH} characters).",
        "4. Department names must be unique across ALL workshops: a name already used in another",
        "   workshop is reported as an error.",
        "5. Departments that already exist in this workshop (see the 'Reference' sheet) are skipped.",
        f"6. Maximum {xl.MAX_ROWS} rows per upload.",
        "",
        "You will always see a preview of what will be imported before anything is saved.",
    ])
    return xl.workbook_response(wb, "department_import_template.xlsx")


@login_required
@require_POST
def upload_departments_excel(request, workshop_id):
    workshop, denied = _import_scope(request, workshop_id)
    if denied:
        return denied
    return xl.run_import(
        request,
        what=f"Department ({workshop.name})",
        process=lambda workbook, report: _process_departments(workbook, report, workshop),
    )


def _process_departments(workbook, report, workshop):
    ws = xl.find_sheet(workbook, "Departments", fallback_first=True)
    header_row, mapping = xl.locate_header(ws, HEADER_ALIASES, required=("name",))
    if not header_row:
        raise xl.ImportFileError(xl.header_error([COLUMN_LABEL]))

    seen = {}
    for excel_row, values in xl.data_rows(ws, header_row, mapping, report):
        name = values["name"]
        errors = []

        if len(name) > NAME_MAX_LENGTH:
            errors.append(f"Department Name exceeds {NAME_MAX_LENGTH} characters.")
        if name.lower() in seen:
            errors.append(f"'{name}' is duplicated in this file (also on row {seen[name.lower()]}).")
        else:
            seen[name.lower()] = excel_row

        if errors:
            report.record("error", row=excel_row, item=name, messages=errors)
            continue

        # Department.clean() rejects a name used anywhere, so check all workshops.
        existing = Department.objects.filter(name__iexact=name).select_related("workshop").first()
        if existing and existing.workshop_id == workshop.id:
            report.record("skip", row=excel_row, item=name, messages=[
                f"Already exists in {workshop.name} - not changed."
            ])
            continue
        if existing:
            report.record("error", row=excel_row, item=name, messages=[
                f"'{existing.name}' already exists in another workshop ({existing.workshop.name}). "
                "Department names must be unique across workshops."
            ])
            continue

        try:
            with transaction.atomic():
                department = Department(name=name, workshop=workshop)
                department.save()
        except Exception as exc:
            report.record("error", row=excel_row, item=name, messages=xl.error_messages(exc))
            continue

        report.saved.append(department)
        report.record("create", row=excel_row, item=name)
