import logging
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.db import transaction

from CalSoft.models import (
    Equipment,
    Standard,
    CalibrationProcedure,
    Parameter,
    EquipmentCalibrationProcedure,
    CalibrationSchedule,
    CalibrationAuditLog,
)
from CalSoft.forms import (
    EquipmentProcedureMappingForm,
    StandardForm,
    ParameterForm,
    StandardParameterFormSet,
)

logger = logging.getLogger(__name__)


@login_required
def standard_create(request):
    if request.method == "POST":
        form = StandardForm(request.POST)
        formset = StandardParameterFormSet(request.POST, prefix="parameters")
        if form.is_valid() and formset.is_valid():
            standard = form.save(commit=False)
            standard.created_by = request.user
            standard.save()
            formset.instance = standard
            formset.save()
            messages.success(request, "Standard created successfully.")
            return redirect("calibration:standard_create")
    else:
        form = StandardForm()
        formset = StandardParameterFormSet(prefix="parameters")
    return render(
        request,
        "Calibrition/standard_form.html",
        {
            "form": form,
            "formset": formset,
            "parameters": Parameter.objects.filter(active_status=True).order_by("name"),
            "show_sidebar": True,
        },
    )


@login_required
def standard_edit(request, pk):
    standard = get_object_or_404(Standard, pk=pk)
    if request.method == "POST":
        form = StandardForm(request.POST, instance=standard)
        formset = StandardParameterFormSet(request.POST, instance=standard, prefix="parameters")
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Standard updated successfully.")
            return redirect("calibration:standard_list")
    else:
        form = StandardForm(instance=standard)
        formset = StandardParameterFormSet(instance=standard, prefix="parameters")
    return render(
        request,
        "Calibration/standard_form.html",
        {
            "form": form,
            "formset": formset,
            "standard": standard,
            "parameters": Parameter.objects.filter(active_status=True).order_by("name"),
            "show_sidebar": True,
        },
    )


@login_required
def standard_delete(request, pk):
    standard = get_object_or_404(Standard, pk=pk)
    if request.method == "POST":
        standard.delete()
        messages.success(request, "Standard deleted successfully.")
    return redirect("calibration:standard_list")


@login_required
def parameter_create(request):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if request.method == "POST":
        form = ParameterForm(request.POST)
        if form.is_valid():
            parameter = form.save(commit=False)
            parameter.created_by = request.user
            parameter.save()

            if is_ajax:
                return JsonResponse(
                    {
                        "success": True,
                        "parameter": {
                            "id": str(parameter.pk),
                            "name": parameter.name,
                            "symbol": parameter.symbol,
                            "unit": parameter.unit,
                        },
                    }
                )

            messages.success(request, "Parameter created successfully.")
            return redirect("calibration:standard_list")

        if is_ajax:
            return JsonResponse({"success": False, "errors": form.errors}, status=400)
    else:
        form = ParameterForm()
    return render(request, "Calibration/parameter_form.html", {"form": form, "show_sidebar": True})


@login_required
def parameter_edit(request, pk):
    from CalSoft.models import Parameter

    parameter = get_object_or_404(Parameter, pk=pk)
    if request.method == "POST":
        form = ParameterForm(request.POST, instance=parameter)
        if form.is_valid():
            form.save()
            messages.success(request, "Parameter updated successfully.")
            return redirect("calibration:standard_list")
    else:
        form = ParameterForm(instance=parameter)
    return render(request, "Calibration/parameter_form.html", {"form": form, "show_sidebar": True})


@login_required
def parameter_delete(request, pk):
    from CalSoft.models import Parameter

    parameter = get_object_or_404(Parameter, pk=pk)
    if request.method == "POST":
        parameter.delete()
        messages.success(request, "Parameter deleted successfully.")
    return redirect("calibration:standard_list")


@login_required
def StandardsParameters_lists(request):
    standards = Standard.objects.all().order_by("name")
    return render(
        request,
        "Calibrition/standards_parameters_list.html",
        {"standards": standards, "show_sidebar": True},
    )


@login_required
def equipment_procedure_mapping(request):
    mappings = EquipmentCalibrationProcedure.objects.select_related(
        "equipment", "calibration_procedure"
    ).all()

    if request.method == "POST":
        form = EquipmentProcedureMappingForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    mapping = form.save(commit=False)
                    mapping.created_by = request.user
                    mapping.save()
                    form.save_m2m()

                    CalibrationAuditLog.objects.create(
                        user=request.user,
                        action="create_equipment_procedure_mapping",
                        description=f"Created mapping for {mapping.equipment.description}",
                        schedule=None,
                    )

                    messages.success(request, "Equipment-procedure mapping created successfully.")
                    return redirect("calibration:equipment_procedure_mapping")
            except Exception as e:
                messages.error(request, f"Error creating mapping: {str(e)}")
    else:
        form = EquipmentProcedureMappingForm()

    context = {
        "mappings": mappings,
        "form": form,
        "show_sidebar": True,
    }

    return render(request, "Calibration/equipment_procedure_mapping.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def auto_assign_procedures(request):
    if request.method == "POST":
        try:
            with transaction.atomic():
                schedules = CalibrationSchedule.objects.filter(
                    status="pending", calibration_procedure__isnull=True
                )
                assigned_count = 0

                for schedule in schedules:
                    default_procedure = EquipmentCalibrationProcedure.objects.filter(
                        equipment=schedule.equipment, is_default=True
                    ).first()

                    if default_procedure:
                        schedule.calibration_procedure = default_procedure.calibration_procedure
                        schedule.estimated_duration = default_procedure.estimated_duration
                        schedule.save()
                        assigned_count += 1

                        CalibrationAuditLog.objects.create(
                            user=request.user,
                            action="auto_assign_procedure",
                            description=f"Auto-assigned procedure to schedule {schedule.id}",
                            schedule=schedule,
                        )

                messages.success(
                    request, f"Successfully assigned procedures to {assigned_count} schedules."
                )
                return redirect("calibration:cal-dashboard")
        except Exception as e:
            messages.error(request, f"Error during auto-assignment: {str(e)}")

    context = {
        "pending_schedules_count": CalibrationSchedule.objects.filter(
            status="pending", calibration_procedure__isnull=True
        ).count(),
        "show_sidebar": True,
    }

    return render(request, "Calibration/auto_assign_procedures.html", context)
