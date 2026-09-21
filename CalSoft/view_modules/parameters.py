import logging
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.db import transaction
from django.core.paginator import Paginator

from CalSoft.models import Standard, Parameter, CalibrationAuditLog
from CalSoft.forms import (
    CalibrationScheduleForm,
    StandardForm,
    ParameterForm,
    StandardParameterFormSet,
)

logger = logging.getLogger(__name__)


def is_ajax(request):
    return request.headers.get(
        "X-Requested-With"
    ) == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", "")


# ------------------------------------------------------------------
# AJAX data endpoint — powers the two tables on the list page
# ------------------------------------------------------------------


@login_required
def api_standards_parameters_data(request):
    """
    GET /calibration/api/standards-parameters/
    Returns JSON: { standards: [...], parameters: [...] }
    """
    standards = list(
        Standard.objects.order_by("name").values(
            "id",
            "name",
            "model_number",
            "serial_number",
            "manufacturer",
            "calibration_date",
            "calibration_due_date",
        )
    )
    # Serialize dates to strings
    for s in standards:
        s["id"] = str(s["id"])
        s["calibration_date"] = (
            s["calibration_date"].strftime("%Y-%m-%d") if s["calibration_date"] else ""
        )
        s["calibration_due_date"] = (
            s["calibration_due_date"].strftime("%Y-%m-%d") if s["calibration_due_date"] else ""
        )

    parameters = list(Parameter.objects.order_by("name").values("id", "name", "symbol", "unit"))
    for p in parameters:
        p["id"] = str(p["id"])
        p["symbol"] = p["symbol"] or ""

    return JsonResponse({"standards": standards, "parameters": parameters})


# ------------------------------------------------------------------
# Standards
# ------------------------------------------------------------------


@login_required
def standard_create(request):
    if request.method == "POST":
        form = StandardForm(request.POST)
        formset = StandardParameterFormSet(request.POST, prefix="parameters")
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                standard = form.save(commit=False)
                standard.created_by = request.user
                standard.save()
                formset.instance = standard
                formset.save()
                messages.success(request, "Standard created successfully.")
                return redirect("calibration:standard_create")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = StandardForm()
        formset = StandardParameterFormSet(prefix="parameters")

    context = {
        "form": form,
        "formset": formset,
        "parameters": Parameter.objects.all(),
        "show_sidebar": True,
    }
    return render(request, "Calibrition/standard_form.html", context)


@login_required
def standard_edit(request, pk):
    standard = get_object_or_404(Standard, pk=pk)

    if request.method == "POST":
        form = StandardForm(request.POST, instance=standard)
        formset = StandardParameterFormSet(request.POST, instance=standard, prefix="parameters")

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                standard = form.save()
                formset.save()

                CalibrationAuditLog.objects.create(
                    user=request.user,
                    action="update_standard",
                    description=f"Updated standard: {standard.name}",
                )

                if is_ajax(request):
                    return JsonResponse(
                        {
                            "success": True,
                            "standard": {
                                "id": str(standard.pk),
                                "name": standard.name,
                                "model_number": standard.model_number,
                                "serial_number": standard.serial_number,
                                "manufacturer": standard.manufacturer,
                                "calibration_date": (
                                    standard.calibration_date.strftime("%Y-%m-%d")
                                    if standard.calibration_date
                                    else None
                                ),
                                "calibration_due_date": (
                                    standard.calibration_due_date.strftime("%Y-%m-%d")
                                    if standard.calibration_due_date
                                    else None
                                ),
                            },
                        }
                    )

                messages.success(request, "Standard updated successfully.")
                return redirect("calibration:StandardsParameters_lists")

        else:
            errors = {}
            if form.errors:
                errors["form"] = {
                    field: [str(e) for e in errs] for field, errs in form.errors.items()
                }
            if formset.errors:
                errors["formset"] = [
                    {
                        "index": idx,
                        "errors": {
                            field: [str(e) for e in errs] for field, errs in err_dict.items()
                        },
                    }
                    for idx, err_dict in enumerate(formset.errors)
                    if err_dict
                ]

            if is_ajax(request):
                return JsonResponse({"success": False, "errors": errors}, status=400)

            messages.error(request, "Please correct the errors.")
    else:
        form = StandardForm(instance=standard)
        formset = StandardParameterFormSet(instance=standard, prefix="parameters")

    context = {
        "form": form,
        "formset": formset,
        "parameters": Parameter.objects.filter(active_status=True),
        "standard": standard,
        "show_sidebar": True,
    }
    return render(request, "Calibrition/standard_form.html", context)


@login_required
def standard_delete(request, pk):
    if request.method == "POST":
        try:
            standard = get_object_or_404(Standard, pk=pk)
            if hasattr(standard, "pending_delete"):
                standard.pending_delete = True
                standard.updated_at = timezone.now()
                standard.save(update_fields=["pending_delete", "updated_at"])
                return JsonResponse(
                    {
                        "success": True,
                        "message": "Standard marked for deletion. It will be removed after syncing with HQ.",
                    }
                )
            else:
                standard.delete()
                return JsonResponse({"success": True})
        except Exception as e:
            logger.exception("%s failed: %s", "standard_delete", e)
            return JsonResponse({"success": False, "error": str(e)}, status=400)

    return JsonResponse({"success": False, "error": "Invalid request method."}, status=405)


# ------------------------------------------------------------------
# Parameters
# ------------------------------------------------------------------


@login_required
def parameter_create(request):
    if request.method == "POST":
        form = ParameterForm(request.POST)
        if form.is_valid():
            parameter = form.save(commit=False)
            parameter.created_by = request.user
            parameter.save()
            if is_ajax(request):
                return JsonResponse(
                    {
                        "success": True,
                        "parameter": {"id": str(parameter.pk), "name": parameter.name},
                    }
                )
            return redirect("calibration:standard_create")
        else:
            if is_ajax(request):
                return JsonResponse({"success": False, "errors": form.errors.as_json()}, status=400)
            messages.error(request, "Please correct the errors below.")

    # Parameters are created from the modal on the standard form; there is no
    # stand-alone page (the template this used to render never existed).
    return redirect("calibration:standard_create")


@login_required
def parameter_edit(request, pk):
    parameter = get_object_or_404(Parameter, pk=pk)

    if request.method == "POST":
        form = ParameterForm(request.POST, instance=parameter)
        if form.is_valid():
            parameter = form.save()
            if is_ajax(request):
                return JsonResponse(
                    {
                        "success": True,
                        "parameter": {
                            "id": str(parameter.pk),
                            "name": parameter.name,
                            "symbol": parameter.symbol or "",
                            "unit": parameter.unit,
                        },
                    }
                )
            messages.success(request, "Parameter updated successfully.")
            return redirect("calibration:StandardsParameters_lists")
        else:
            if is_ajax(request):
                return JsonResponse({"success": False, "errors": form.errors.as_json()}, status=400)
            messages.error(request, "Please correct the errors below.")
    else:
        # GET — return JSON for the edit modal
        if is_ajax(request):
            return JsonResponse(
                {
                    "id": str(parameter.pk),
                    "name": parameter.name,
                    "symbol": parameter.symbol or "",
                    "unit": parameter.unit,
                }
            )

    # Editing happens in the modal on the standards & parameters list.
    return redirect("calibration:StandardsParameters_lists")


@login_required
def parameter_delete(request, pk):
    if request.method == "POST":
        try:
            parameter = get_object_or_404(Parameter, pk=pk)
            if hasattr(parameter, "pending_delete"):
                parameter.pending_delete = True
                parameter.updated_at = timezone.now()
                parameter.save(update_fields=["pending_delete", "updated_at"])
                return JsonResponse(
                    {
                        "success": True,
                        "message": "Parameter marked for deletion. It will be removed after syncing with HQ.",
                    }
                )
            else:
                parameter.delete()
                return JsonResponse({"success": True})
        except Exception as e:
            logger.exception("%s failed: %s", "parameter_delete", e)
            return JsonResponse({"success": False, "error": str(e)}, status=400)

    return JsonResponse({"success": False, "error": "Invalid request method."}, status=405)


# ------------------------------------------------------------------
# Combined list view — tables load via AJAX, no server-side queryset
# ------------------------------------------------------------------


