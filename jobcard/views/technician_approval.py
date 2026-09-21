"""jobcard.views — technician job-card handler for job-card creation.

``handle_technician_job_card`` reads the form, validates it, resolves the
department/equipment/PPM schedule, and creates the job card with its spare
parts in one transaction. Each step is a helper below; every refusal
re-renders the form with the technician's input intact.
"""
import json
import logging
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.timezone import now

from Inventory.models import Department, Equipment
from parts_tools.models import Accessories

from ..models import SparePartUsed, jobcard
from .helpers import get_or_create_user_signature

logger = logging.getLogger(__name__)

# Actions each workshop category may record.
ALLOWED_ACTIONS = {
    "maintenance": (["PPM", "Repair", "Others"], "maintenance workshops"),
    "calibration_center": (["Calibration", "Others"], "calibration centers"),
}

REQUIRED_FIELDS = ("priority_level", "department", "equipment", "job_description",
                   "action_taken", "time_started")


class FormRejected(Exception):
    """Raised by a step to re-render the form with an error message."""


# ── Reading and rendering the form ───────────────────────────────────────────

def _read_form(request):
    post = request.POST
    return {
        "priority_level": post.get("priority_level"),
        "department": post.get("department"),
        "equipment": post.get("equipment"),
        "job_description": post.get("job_description"),
        "action_taken": post.get("action_taken"),
        "time_started": post.get("time_started"),
        "time_completed": post.get("time_completed"),
        "signature_data": post.get("signature_data"),
        "use_auto_signature": post.get("use_auto_signature") == "true",
        "spare_parts_data": post.get("spare_parts_data", "[]"),
        "labor_cost": post.get("labor_cost", "0.00"),
        "additional_costs": post.get("additional_costs", "0.00"),
        "additional_costs_description": post.get("additional_costs_description", ""),
        "ppm_schedule_id": post.get("ppm_schedule_id"),
    }


def _form_choices(workshop):
    """Departments and accessories a technician of ``workshop`` can pick from."""
    if workshop and workshop.category == "calibration_center":
        return (Department.objects.filter(active_status=True).order_by("name"),
                Accessories.objects.filter(active_status=True).order_by("name"))
    if not workshop:
        return Department.objects.none(), Accessories.objects.none()
    return (Department.objects.filter(workshop=workshop, active_status=True).order_by("name"),
            Accessories.objects.filter(workshop=workshop, active_status=True).order_by("name"))


def _render_form(request, workshop, form_data, **extra):
    departments, accessories = _form_choices(workshop)
    return render(request, "jobcard/jbb.html", {
        "show_sidebar": True,
        "form_data": form_data,
        "is_technician": True,
        "is_nurse": False,
        "departments": departments,
        "accessories": accessories,
        "job_cards": jobcard.objects.none(),
        **extra,
    })


# ── Validation ───────────────────────────────────────────────────────────────

def _validate(workshop, form):
    if not form["use_auto_signature"] and not form["signature_data"]:
        raise FormRejected("Please provide a signature or enable auto-signature.")
    if not all(form[field] for field in REQUIRED_FIELDS):
        raise FormRejected("All required fields must be provided.")
    if workshop and workshop.category in ALLOWED_ACTIONS:
        allowed, label = ALLOWED_ACTIONS[workshop.category]
        if form["action_taken"] not in allowed:
            raise FormRejected(
                f"Action '{form['action_taken']}' is not allowed for {label}. "
                f"Allowed actions: {', '.join(allowed)}"
            )


def _usable_ppm_schedule_id(request, workshop, form):
    """The PPM schedule id to link, or None (with a warning explaining why)."""
    schedule_id = form["ppm_schedule_id"]
    if form["action_taken"] != "PPM" or not schedule_id:
        return None
    if workshop.category != "maintenance":
        messages.warning(
            request,
            "PPM schedules can only be linked in maintenance workshops. Creating job card without PPM link.",
            extra_tags="jobcard",
        )
        return None

    from ppms.models import PPMSchedule

    try:
        PPMSchedule.objects.get(id=UUID(schedule_id), active_status=True)
    except (ValueError, AttributeError):
        messages.warning(request, "Invalid PPM schedule format. Creating job card without PPM link.",
                         extra_tags="jobcard")
        return None
    except PPMSchedule.DoesNotExist:
        messages.warning(request, "Selected PPM schedule not found or inactive. Creating job card without PPM link.",
                         extra_tags="jobcard")
        return None
    return schedule_id


def _department_and_equipment(workshop, form):
    if not workshop:
        raise FormRejected("Technician's workshop not found. Cannot create job card.")
    try:
        department_uuid = UUID(form["department"])
        equipment_uuid = UUID(form["equipment"])
    except (ValueError, AttributeError) as exc:
        logger.error("Invalid UUID format - department_id: %s, equipment_id: %s",
                     form["department"], form["equipment"])
        raise FormRejected(f"Invalid department or equipment ID format: {exc}")

    departments = Department.objects.filter(active_status=True)
    if workshop.category != "calibration_center":
        departments = departments.filter(workshop=workshop)
    try:
        department = departments.get(id=department_uuid)
    except Department.DoesNotExist:
        logger.error("Department not found or inactive: %s", form["department"])
        raise FormRejected("Invalid department selected or department is inactive.")
    try:
        # The device must belong to the selected department, otherwise a job
        # card could be raised against another workshop's equipment.
        equipment = Equipment.objects.get(id=equipment_uuid, department=department, active_status=True)
    except Equipment.DoesNotExist:
        logger.error("Equipment not found or inactive: %s", form["equipment"])
        raise FormRejected("Invalid equipment selected or equipment is inactive.")
    return department, equipment


def _costs(form):
    try:
        labor = Decimal(form["labor_cost"].strip()) if form["labor_cost"] and form["labor_cost"].strip() else Decimal("0.00")
        additional = (Decimal(form["additional_costs"].strip())
                      if form["additional_costs"] and form["additional_costs"].strip() else Decimal("0.00"))
        if labor < 0 or additional < 0:
            raise ValueError("Costs cannot be negative")
    except (InvalidOperation, ValueError) as exc:
        logger.error("Invalid cost format: labor_cost=%s, additional_costs=%s, error=%s",
                     form["labor_cost"], form["additional_costs"], exc)
        raise FormRejected("Invalid cost format. Please enter valid positive numbers.")
    return labor, additional


# ── Persisting ───────────────────────────────────────────────────────────────

def _signature(request, form):
    if not form["use_auto_signature"]:
        return form["signature_data"]
    signature = get_or_create_user_signature(request.user)
    if not signature:
        messages.warning(request, "Auto-signature could not be generated. Using manual signature.",
                         extra_tags="jobcard")
        return form["signature_data"]
    return signature


def _eligible_ppm_schedule(request, workshop, equipment, schedule_id):
    """The schedule to link: incomplete, for this device and workshop, not already approved."""
    if not schedule_id:
        return None
    from ppms.models import PPMSchedule

    try:
        schedule = PPMSchedule.objects.get(
            id=UUID(schedule_id), equipment_id=equipment.id, workshop=workshop,
            status__in=["pending", "pushed", "overdue"], active_status=True,
        )
    except PPMSchedule.DoesNotExist:
        logger.warning("PPM schedule %s not found, already completed, or doesn't match equipment/workshop",
                       schedule_id)
        return None
    except (ValueError, AttributeError) as exc:
        logger.error("Invalid PPM schedule ID format: %s, error: %s", schedule_id, exc)
        return None

    linked = getattr(schedule, "related_job_card", None)
    if linked and linked.status == "Approved":
        messages.warning(
            request,
            f"PPM schedule {schedule.scheduled_month.strftime('%B %Y')} is already linked to an approved "
            "job card. Creating job card without PPM link.",
            extra_tags="jobcard",
        )
        return None
    logger.info("Linking job card to PPM schedule %s (%s) for equipment %s",
                schedule.id, schedule.scheduled_month.strftime("%B %Y"), equipment.serial_number)
    return schedule


def _requested_parts(request, form):
    """Spare-part rows with a real part and a positive quantity."""
    try:
        rows = json.loads(form["spare_parts_data"])
        if not isinstance(rows, list):
            raise ValueError("Spare parts data must be a list")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Invalid spare parts data format: %s", exc)
        messages.error(request, f"Invalid spare parts data format: {exc}", extra_tags="jobcard")
        raise ValidationError(f"Invalid spare parts data: {exc}")
    return [row for row in rows
            if row.get("part_id") and row.get("part_id") != "none"
            and row.get("quantity") and int(row.get("quantity")) > 0]


def _accessible_part(workshop, part_uuid):
    parts = Accessories.objects.filter(active_status=True)
    if workshop.category != "calibration_center":
        parts = parts.filter(workshop=workshop)
    return parts.get(id=part_uuid)


def _check_stock(request, workshop, rows):
    issues = []
    for row in rows:
        try:
            part = _accessible_part(workshop, UUID(row["part_id"]))
        except (ValueError, AttributeError):
            issues.append(f"Invalid part ID format: {row['part_id']}")
            continue
        except Accessories.DoesNotExist:
            issues.append(f"Part with ID {row['part_id']} not found, inactive, or not accessible to your workshop")
            continue
        if part.stock_count < int(row["quantity"]):
            name = part.name.name if part.name else f"Part {part.id}"
            issues.append(f"{name}: Available {part.stock_count}, Requested {row['quantity']}")
    if issues:
        error = "Stock validation failed:\n" + "\n".join(issues)
        messages.error(request, error, extra_tags="jobcard")
        raise ValidationError(error)


def _record_parts(job_card, workshop, rows):
    for row in rows:
        try:
            part_uuid = UUID(row["part_id"])
            unit_cost = row.get("unit_cost", "0.00")
            cost = Decimal(str(unit_cost).strip()) if unit_cost else Decimal("0.00")
            if cost < 0:
                logger.warning("Negative unit cost detected for part %s, setting to 0.00", row["part_id"])
                cost = Decimal("0.00")
        except (ValueError, AttributeError, InvalidOperation) as exc:
            logger.error("Invalid UUID format or cost for part_id during creation: %s, error: %s",
                         row["part_id"], exc)
            continue
        try:
            part = _accessible_part(workshop, part_uuid)
        except Accessories.DoesNotExist:
            logger.error("Part %s not found or inactive during SparePartUsed creation", row["part_id"])
            continue
        SparePartUsed.objects.create(job_card=job_card, part=part, quantity=int(row["quantity"]),
                                     remarks=row.get("remarks") or "", unit_cost=cost)


def _success_message(job_card, workshop, ppm_schedule, action_taken):
    message = (
        f"Job card #{job_card.id} created successfully by {workshop.name} "
        f"and is waiting for approval. Total cost: KSh {job_card.get_total_cost():,.2f}."
    )
    if ppm_schedule:
        message += (
            f" Linked to PPM schedule for {ppm_schedule.scheduled_month.strftime('%B %Y')}. "
            "PPM will be marked as completed when approved."
        )
    elif action_taken == "PPM":
        message += " (Manual/unscheduled PPM work)"
    return message + " Stock will be deducted when approved."


# ── The view handler ─────────────────────────────────────────────────────────

def handle_technician_job_card(request, workshop):
    form = _read_form(request)
    try:
        _validate(workshop, form)
        schedule_id = _usable_ppm_schedule_id(request, workshop, form)
        department, equipment = _department_and_equipment(workshop, form)
    except FormRejected as rejection:
        messages.error(request, str(rejection), extra_tags="jobcard")
        return _render_form(request, workshop, form)

    # Kept for the error re-render: the signature flag counts as unavailable
    # here, as it always did.
    no_signature = {"user_signature_available": False}
    try:
        with transaction.atomic():
            signature = _signature(request, form)
            try:
                labor_cost, additional_costs = _costs(form)
            except FormRejected as rejection:
                messages.error(request, str(rejection), extra_tags="jobcard")
                return _render_form(request, workshop, form)

            ppm_schedule = _eligible_ppm_schedule(request, workshop, equipment, schedule_id)
            job_card = jobcard.objects.create(
                department=department,
                equipment=equipment,
                workshop=workshop,
                priority_level=form["priority_level"],
                job_description=form["job_description"],
                action_taken=form["action_taken"],
                time_started=form["time_started"],
                time_completed=form["time_completed"] or None,
                performed_by=request.user,
                tech_signature=signature,
                technician_signed_date=now(),
                status="Waiting Approval",
                labor_cost=labor_cost,
                additional_costs=additional_costs,
                additional_costs_description=(form["additional_costs_description"] or "").strip(),
                related_ppm_schedule=ppm_schedule,
            )

            rows = _requested_parts(request, form)
            _check_stock(request, workshop, rows)
            _record_parts(job_card, workshop, rows)
            job_card.update_costs()

            messages.success(request, _success_message(job_card, workshop, ppm_schedule, form["action_taken"]),
                             extra_tags="jobcard")
            request.session.pop("jobcard_form_data", None)
            return redirect("jobcard:waiting_jobcards")

    except ValidationError as exc:
        logger.error("Validation error creating job card: %s", exc)
        messages.error(request, f"Cannot create job card: {exc}", extra_tags="jobcard")
    except Exception as exc:
        logger.error("Unexpected error creating job card: %s", exc, exc_info=True)
        messages.error(request, f"Error creating job card: {exc}", extra_tags="jobcard")

    request.session["jobcard_form_data"] = form
    return _render_form(request, workshop, form, **no_signature)
