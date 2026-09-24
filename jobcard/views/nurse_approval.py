"""jobcard.views — nurse approval handler for job-card creation.

``handle_nurse_approval`` lets a department's in-charge approve or decline a
waiting job card. Approval deducts stock and completes a linked PPM schedule;
decline records the reason. Every refusal re-renders the form with the
in-charge's input intact.
"""
import logging
from uuid import UUID

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.timezone import now

from Inventory.models import Department
from parts_tools.models import Accessories
from users.models import UserSignature

from ..models import jobcard
from .helpers import get_or_create_user_signature

logger = logging.getLogger(__name__)


class FormRejected(Exception):
    """Raised by a step to re-render the form with an error message."""

    def __init__(self, message, job_card=None):
        super().__init__(message)
        self.job_card = job_card


def _has_saved_signature(user):
    """Whether the user has a stored signature to offer as the auto-signature."""
    signature = UserSignature.objects.filter(user=user, active_status=True).first()
    return bool(signature and signature.has_signature())


def _read_form(request):
    post = request.POST
    return {
        "jobcard_id": post.get("jobcard_id"),
        "nurse_name": post.get("nurse_name"),
        "nurse_signature_data": post.get("nurse_signature_data"),
        "decline_reason": post.get("decline_reason", "").strip(),
        "use_auto_signature": post.get("use_auto_signature") == "true",
    }


def _waiting_card(department, jobcard_id):
    """The department's waiting card with this id, or None (malformed ids included)."""
    if not jobcard_id:
        return None
    try:
        return jobcard.objects.filter(id=jobcard_id, status="Waiting Approval", department=department).first()
    except (ValueError, AttributeError, ValidationError):
        return None


def _render_form(request, department, form, selected_job_card):
    return render(request, "jobcard/jbb.html", {
        "show_sidebar": True,
        "form_data": form,
        "is_nurse": True,
        "is_technician": False,
        "job_cards": jobcard.objects.filter(status="Waiting Approval", department=department),
        "departments": Department.objects.filter(id=department.id),
        "accessories": Accessories.objects.none(),
        "selected_job_card": selected_job_card,
        "user_signature_available": _has_saved_signature(request.user),
    })


def _validate(form):
    if not form["use_auto_signature"] and not form["nurse_signature_data"]:
        raise FormRejected("Please provide a signature or enable auto-signature.")
    if not (form["jobcard_id"] and form["nurse_name"]):
        raise FormRejected("Work order and nurse name are required.")
    try:
        return UUID(form["jobcard_id"])
    except (ValueError, AttributeError):
        raise FormRejected("Invalid work order ID format.")


def _sign(request, job_card, form):
    job_card.verified_by_nurse = request.user
    job_card.nurse_name = form["nurse_name"]
    job_card.nurse_signed_date = now()
    signature = form["nurse_signature_data"]
    if form["use_auto_signature"]:
        auto_signature = get_or_create_user_signature(request.user)
        if auto_signature:
            signature = auto_signature
        else:
            messages.warning(request, "Auto-signature could not be generated. Using manual signature.",
                             extra_tags="jobcard")
    job_card.verified_signature = signature


def _check_linked_ppm(job_card):
    schedule = job_card.related_ppm_schedule
    if not schedule:
        return
    if schedule.status == "completed":
        raise FormRejected(
            f"Cannot approve: Linked PPM schedule for {schedule.scheduled_month.strftime('%B %Y')} "
            "is already marked as completed.",
            job_card,
        )
    if schedule.equipment != job_card.equipment:
        raise FormRejected(
            f"PPM schedule equipment mismatch. Schedule is for {schedule.equipment.description}, "
            f"work order is for {job_card.equipment.description}.",
            job_card,
        )


def _approve(request, job_card, form):
    _check_linked_ppm(job_card)
    try:
        job_card.deduct_stock()
    except ValidationError as exc:
        logger.error("Validation error approving job card #%s: %s", job_card.id, exc)
        raise FormRejected(f"Cannot approve work order: {exc}", job_card)
    job_card.status = "Approved"
    job_card.decline_reason = None
    job_card.save()

    ppm_updated = bool(job_card.related_ppm_schedule) and job_card.update_ppm_status_if_applicable()

    message = (
        f"Work order #{job_card.id} approved successfully. "
        f"Total cost: KSh {job_card.get_total_cost():,.2f}. Stock has been updated."
    )
    schedule = job_card.related_ppm_schedule
    if ppm_updated:
        message += f" PPM schedule for {schedule.scheduled_month.strftime('%B %Y')} has been marked as completed."
        logger.info("Job card #%s approval completed PPM schedule %s", job_card.id, schedule.id)
    elif job_card.action_taken == "PPM" and not schedule:
        message += " (Manual PPM work - no schedule was linked)"
    elif job_card.action_taken == "PPM":
        message += " (PPM schedule was already completed)"
    messages.success(request, message, extra_tags="jobcard")

    logger.info(
        "Job card #%s approved by %s (%s): equipment=%s action=%s cost=%s department=%s workshop=%s ppm_linked=%s",
        job_card.id, request.user.get_full_name(), form["nurse_name"], job_card.equipment.description,
        job_card.action_taken, job_card.get_total_cost(), job_card.department.name, job_card.workshop.name,
        bool(schedule),
    )
    return redirect("jobcard:approved_jobcards")


def _decline(request, job_card, form):
    if not form["decline_reason"]:
        raise FormRejected("Reason for decline is required.", job_card)
    if job_card.related_ppm_schedule and job_card.action_taken == "PPM":
        logger.warning("Declining PPM-linked job card #%s; PPM schedule %s remains pending.",
                       job_card.id, job_card.related_ppm_schedule.id)
    job_card.status = "Declined"
    job_card.decline_reason = form["decline_reason"]
    job_card.save()
    messages.success(request, f"Work order #{job_card.id} declined successfully. No stock changes made.",
                     extra_tags="jobcard")
    logger.info("Job card #%s declined by %s (%s): %s",
                job_card.id, request.user.get_full_name(), form["nurse_name"], form["decline_reason"][:100])
    return redirect("jobcard:waiting_jobcards")


def handle_nurse_approval(request, nurse_department):
    form = _read_form(request)
    selected_job_card = _waiting_card(nurse_department, form["jobcard_id"])

    try:
        jobcard_uuid = _validate(form)
        with transaction.atomic():
            job_card = jobcard.objects.select_for_update().get(id=jobcard_uuid, status="Waiting Approval")
            if job_card.department != nurse_department:
                raise FormRejected("You can only approve/decline work orders for your department.", job_card)
            _sign(request, job_card, form)
            if "approve" in request.POST:
                return _approve(request, job_card, form)
            if "decline" in request.POST:
                return _decline(request, job_card, form)
            raise FormRejected("Invalid action requested.", job_card)

    except FormRejected as rejection:
        messages.error(request, str(rejection), extra_tags="jobcard")
        return _render_form(request, nurse_department, form, rejection.job_card or selected_job_card)
    except jobcard.DoesNotExist:
        messages.error(request,
                       "Work order not found, already processed, or you don't have permission to access it.",
                       extra_tags="jobcard")
        logger.warning("Job card not found or inaccessible: %s", form["jobcard_id"])
    except Exception as exc:
        logger.error("Error in nurse approval/decline for jobcard_id %s: %s", form["jobcard_id"], exc,
                     exc_info=True)
        messages.error(request, f"Error processing work order: {exc}", extra_tags="jobcard")
    return _render_form(request, nurse_department, form, selected_job_card)
