"""Bulk user import from Excel (HOD only).

Same rules as creating one user on the Manage Users page: a generated
username and temporary password, first-login setup required (new password and
signature), and the welcome email with the temporary password. Emails are
sent only when the import is committed, never for a preview. Uploads follow
the shared preview/commit contract in core.excel_import.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.views.decorators.http import require_GET, require_POST
from openpyxl import Workbook

from core import excel_import as xl
from Inventory.models import Department
from workshop.models import Workshop

from .models import UserProfile
from .utils import UserManagementUtils

User = get_user_model()

COLUMNS = {
    "first_name": "First Name", "last_name": "Last Name", "email": "Email", "role": "Role",
    "workshop": "Workshop", "level": "Level", "department": "Department", "phone": "Phone",
}
REQUIRED = ("first_name", "last_name", "email", "role")
HEADER_ALIASES = {xl.normalize(label): key for key, label in COLUMNS.items()} | {
    "firstname": "first_name", "surname": "last_name", "lastname": "last_name", "emailaddress": "email",
    "phonenumber": "phone", "mobile": "phone",
}
ROLES = {"hod": "HOD", "headofdepartment": "HOD", "tech": "Tech", "technologist": "Tech",
         "technician": "Tech", "engineer": "Tech", "nic": "NIC", "incharge": "NIC", "nurseincharge": "NIC"}
LEVELS = {xl.normalize(value): value for value, _ in UserProfile.LEVEL_CHOICES}


def _denied(request):
    profile = getattr(request.user, "userprofile", None)
    if profile is None or profile.role != "HOD":
        return xl.json_error("Only HODs can bulk-upload users.", 403)
    return None


@login_required
@require_GET
def download_user_import_template(request):
    denied = _denied(request)
    if denied:
        return denied
    wb = Workbook()
    ws = wb.active
    ws.title = "Users"
    xl.write_header(ws, list(COLUMNS.values()), width=24)
    ranges = xl.write_reference(wb, [
        ("Role", ["Tech", "NIC", "HOD"]),
        ("Level", [value for value, _ in UserProfile.LEVEL_CHOICES]),
        ("Workshop", list(Workshop.objects.filter(pending_delete=False).order_by("name")
                          .values_list("name", flat=True))),
        ("Department", list(Department.objects.filter(pending_delete=False).order_by("name")
                            .values_list("name", flat=True))),
    ])
    for column, name in ((4, "Role"), (5, "Workshop"), (6, "Level"), (7, "Department")):
        xl.add_dropdown(ws, column, ranges.get(name))
    xl.write_instructions(wb, [
        "How to use this template",
        "",
        "1. One person per row on the 'Users' sheet, from row 2. Keep the header row as it is.",
        "2. First Name, Last Name, Email and Role are required. Role is Tech, NIC or HOD.",
        "3. Tech (technologist): Workshop and Level (Engineer or Engineer Incharge) are required.",
        "4. NIC (in-charge): Department is required.",
        "5. Each person gets a username and a temporary password by email, and sets their own",
        "   password and signature at first login.",
        "6. People whose email is already registered are skipped, not changed.",
        f"7. Maximum {xl.MAX_ROWS} rows per upload. You see a preview before anything is saved.",
    ])
    return xl.workbook_response(wb, "user_import_template.xlsx")


@login_required
@require_POST
def upload_users_excel(request):
    denied = _denied(request)
    if denied:
        return denied
    return xl.run_import(request, what="User",
                         process=lambda workbook, report: _process_users(workbook, report, request.user))


def _process_users(workbook, report, created_by):
    ws = xl.find_sheet(workbook, "Users", fallback_first=True)
    header_row, mapping = xl.locate_header(ws, HEADER_ALIASES, required=REQUIRED)
    if not header_row:
        raise xl.ImportFileError(xl.header_error([COLUMNS[k] for k in REQUIRED]))
    workshops = {xl.normalize(w.name): w for w in Workshop.objects.filter(pending_delete=False)}
    departments = {xl.normalize(d.name): d for d in Department.objects.filter(pending_delete=False)}
    seen = {}
    for excel_row, values in xl.data_rows(ws, header_row, mapping, report):
        first, last = values.get("first_name", ""), values.get("last_name", "")
        email = (values.get("email") or "").strip().lower()
        item = f"{first} {last}".strip() or email
        errors = []
        if not first:
            errors.append("First Name is required.")
        if not last:
            errors.append("Last Name is required.")
        try:
            validate_email(email)
        except ValidationError:
            errors.append(f"'{email}' is not a valid email address." if email else "Email is required.")
        role = ROLES.get(xl.normalize(values.get("role")))
        if not role:
            errors.append(f"Role '{values.get('role') or ''}' is not valid. Use Tech, NIC or HOD.")
        workshop = department = level = None
        if role == "Tech":
            workshop = workshops.get(xl.normalize(values.get("workshop")))
            level = LEVELS.get(xl.normalize(values.get("level")))
            if not workshop:
                errors.append(f"Workshop '{values.get('workshop') or ''}' was not found." if values.get("workshop")
                              else "Workshop is required for Tech.")
            if not level:
                errors.append("Level must be Engineer or Engineer Incharge for Tech.")
        if role == "NIC":
            department = departments.get(xl.normalize(values.get("department")))
            if not department:
                errors.append(f"Department '{values.get('department') or ''}' was not found."
                              if values.get("department") else "Department is required for NIC.")
        if email and email in seen:
            errors.append(f"{email} is duplicated in this file (also on row {seen[email]}).")
        elif email:
            seen[email] = excel_row
        if errors:
            report.record("error", row=excel_row, item=item, messages=errors)
            continue
        if User.objects.filter(email__iexact=email).exists():
            report.record("skip", row=excel_row, item=item, messages=["Email already registered - not changed."])
            continue
        try:
            with transaction.atomic():
                password = UserManagementUtils.generate_temp_password()
                user = User.objects.create_user(username=UserManagementUtils.generate_username(first),
                                                first_name=first, last_name=last, email=email, password=password)
                profile, _ = UserProfile.objects.update_or_create(user=user, defaults={
                    "role": role, "workshop": workshop, "department": department, "level": level,
                    "phone_number": (values.get("phone") or "")[:15] or None, "created_by": created_by,
                    "must_change_password": True, "has_uploaded_signature": False})
                profile.full_clean(exclude=["user", "created_by"])
        except Exception as exc:
            report.record("error", row=excel_row, item=item, messages=xl.error_messages(exc))
            continue
        # Only if the whole import commits; a preview rolls back and sends nothing.
        transaction.on_commit(lambda u=user, p=password: UserManagementUtils.send_welcome_email(u, p, created_by))
        report.saved.extend([user, profile])
        label = role + (f", {workshop.name} ({level})" if workshop else f", {department.name}" if department else "")
        report.record("create", row=excel_row, item=item, messages=[f"{user.username} · {label}"])
