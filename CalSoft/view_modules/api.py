import logging
from decimal import Decimal, InvalidOperation
from uuid import UUID
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods
from django.contrib.auth.decorators import login_required
from django.db.models import Q

from CalSoft.models import (
    CalibrationProcedure,
    CalibrationParameter,
    CalibrationSession,
    CalibrationSchedule,
    Equipment,
    CalibrationReading,
    SubParameter,
    Parameter,
    Standard,
    StandardParameter,
    SetValue,
)

logger = logging.getLogger(__name__)


def api_schedule(request, schedule_id):
    try:
        schedule = get_object_or_404(CalibrationSchedule, pk=schedule_id)
        return JsonResponse(
            {
                "success": True,
                "equipment_id": schedule.equipment.id,
                "procedure_id": (
                    schedule.calibration_procedure.id if schedule.calibration_procedure else None
                ),
                "equipment_description": schedule.equipment.description,
                "equipment_serial": schedule.equipment.serial_number,
            }
        )
    except Exception as e:
        logger.error(f"Error in api_schedule: {str(e)}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)


def api_equipment_procedure(request, equipment_id):
    try:
        from CalSoft.models import EquipmentCalibrationProcedure

        mapping = EquipmentCalibrationProcedure.objects.filter(
            equipment_id=equipment_id, is_default=True
        ).first()

        return JsonResponse(
            {
                "success": True,
                "procedure_id": mapping.calibration_procedure.id if mapping else None,
                "procedure_name": mapping.calibration_procedure.name if mapping else None,
            }
        )
    except Exception as e:
        logger.error(f"Error in api_equipment_procedure: {str(e)}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)


def api_procedure(request, procedure_id):
    try:
        procedure = CalibrationProcedure.objects.get(pk=procedure_id, active_status=True)
        parameters = procedure.parameters.all().order_by("order")

        procedure_data = {
            "id": str(procedure.id),
            "name": procedure.name,
            "description": procedure.description,
            "temperature": (
                str(procedure.temperature) if hasattr(procedure, "temperature") else None
            ),
            "humidity": str(procedure.humidity) if hasattr(procedure, "humidity") else None,
            "pressure": str(procedure.pressure) if hasattr(procedure, "pressure") else None,
            "parameters": [],
        }

        for param in parameters:
            sub_parameters = param.sub_parameters.all().order_by("order")
            param_data = {
                "id": str(param.id),
                "name": param.name,
                "unit": param.unit,
                "num_readings": param.num_readings,
                "standard_reference": param.standard_reference,
                "reference_uncertainty": str(param.reference_uncertainty),
                "coverage_factor": str(param.coverage_factor),
                "tolerance": str(param.tolerance) if param.tolerance else None,
                "sub_parameters": [],
                "set_values": [],
            }

            for sub_param in sub_parameters:
                sub_param_data = {
                    "id": str(sub_param.id),
                    "name": sub_param.name,
                    "tolerance": str(sub_param.tolerance) if sub_param.tolerance else None,
                }
                param_data["sub_parameters"].append(sub_param_data)

            if not sub_parameters.exists():
                set_values = SetValue.objects.filter(parameter=param, sub_parameter=None).order_by(
                    "order"
                )
                for sv in set_values:
                    param_data["set_values"].append({"id": str(sv.id), "value": float(sv.value)})

            procedure_data["parameters"].append(param_data)

        return JsonResponse({"success": True, "procedure": procedure_data})

    except CalibrationProcedure.DoesNotExist:
        return JsonResponse({"success": False, "error": "Procedure not found"}, status=404)
    except Exception as e:
        logger.error(f"Error in api_procedure: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required
def api_parameters(request):
    name = request.GET.get("name", "")
    parameters = Parameter.objects.filter(active_status=True)
    if name:
        parameters = parameters.filter(name=name)
    data = {"parameters": [{"name": param.name, "unit": param.unit} for param in parameters]}
    return JsonResponse(data)


@login_required
def api_standards(request):
    parameter_name = request.GET.get("parameter_name", "")
    standard_id = request.GET.get("standard_id", "")
    standards = Standard.objects.filter(
        standardparameter__parameter__name=parameter_name
    ).distinct()
    if standard_id:
        standards = standards.filter(id=standard_id)
    data = {
        "standards": [
            {"id": standard.id, "serial_number": standard.serial_number, "name": standard.name}
            for standard in standards
        ]
    }
    return JsonResponse(data)


def api_standard_parameters(request):
    try:
        standard_id = request.GET.get("standard_id", "")
        parameter_name = request.GET.get("parameter_name", "")

        if not standard_id or not parameter_name:
            return JsonResponse(
                {"success": False, "error": "Both standard_id and parameter_name are required"},
                status=400,
            )

        standard_parameter = (
            StandardParameter.objects.filter(
                standard_id=standard_id, parameter__name=parameter_name
            )
            .select_related("parameter", "standard")
            .first()
        )

        if standard_parameter:
            data = {
                "success": True,
                "uncertainty": str(standard_parameter.uncertainty),
                "parameter_name": standard_parameter.parameter.name,
                "standard_name": standard_parameter.standard.name,
                "standard_serial": standard_parameter.standard.serial_number,
            }
        else:
            data = {
                "success": True,
                "uncertainty": "0.001",
                "parameter_name": parameter_name,
                "standard_name": None,
                "standard_serial": None,
            }

        return JsonResponse(data)

    except Exception as e:
        logger.error(f"Error in api_standard_parameters: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


def api_set_values(request):
    try:
        parameter_id = request.GET.get("parameter")

        if not parameter_id:
            return JsonResponse(
                {"success": False, "error": "Valid Parameter ID is required"}, status=400
            )

        try:
            UUID(str(parameter_id))
        except (ValueError, AttributeError, TypeError):
            return JsonResponse(
                {"success": False, "error": "Invalid UUID format for Parameter ID"}, status=400
            )

        set_values = (
            SetValue.objects.filter(parameter_id=parameter_id)
            .select_related("sub_parameter")
            .order_by("order", "value")
        )

        set_values_data = [
            {
                "id": str(sv.id),
                "value": str(sv.value),
                "sub_parameter": str(sv.sub_parameter_id) if sv.sub_parameter_id else None,
                "sub_parameter_name": sv.sub_parameter.name if sv.sub_parameter else None,
            }
            for sv in set_values
        ]

        return JsonResponse(set_values_data, safe=False)

    except Exception as e:
        logger.error(f"Error in api_set_values: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required
def api_procedure_detail(request, pk):
    try:
        procedure = get_object_or_404(CalibrationProcedure, pk=pk)
        parameters = [
            {
                "id": param.id,
                "name": param.name,
                "unit": param.unit,
                "num_readings": param.num_readings,
                "standard_reference": param.standard_reference,
                "reference_uncertainty": str(param.reference_uncertainty),
                "coverage_factor": str(param.coverage_factor),
                "tolerance": str(param.tolerance) if param.tolerance else None,
                "order": param.order,
                "sub_parameters": [
                    {
                        "id": sub.id,
                        "name": sub.name,
                        "tolerance": str(sub.tolerance) if sub.tolerance else None,
                        "order": sub.order,
                    }
                    for sub in param.sub_parameters.all()
                ],
            }
            for param in procedure.parameters.all()
        ]

        return JsonResponse(
            {
                "success": True,
                "procedure": {
                    "id": procedure.id,
                    "name": procedure.name,
                    "description": procedure.description,
                    "temperature": str(procedure.temperature) if procedure.temperature else None,
                    "humidity": str(procedure.humidity) if procedure.humidity else None,
                    "pressure": str(procedure.pressure) if procedure.pressure else None,
                    "parameters": parameters,
                },
            }
        )
    except Exception as e:
        logger.error(f"Error in api_procedure_detail: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required
def api_standard_parameters_detail(request, standard_id):
    try:
        standard = get_object_or_404(Standard, pk=standard_id)
        parameters = StandardParameter.objects.filter(standard=standard).select_related("parameter")

        data = {
            "success": True,
            "parameters": [
                {
                    "id": str(sp.id),
                    "parameter_id": str(sp.parameter.id),
                    "parameter_name": sp.parameter.name,
                    "uncertainty": str(sp.uncertainty),
                }
                for sp in parameters
            ],
        }

        return JsonResponse(data)
    except Exception as e:
        logger.error(f"Error fetching standard parameters: {str(e)}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required
def api_validate_readings(request):
    from django.core.serializers.json import json
    from CalSoft.utils import QualityAssurance

    try:
        data = json.loads(request.body)
        readings = [Decimal(str(v)) for v in data.get("readings", []) if v is not None]
        parameter_id = data.get("parameter_id")
        sub_parameter_id = data.get("sub_parameter_id")

        parameter = get_object_or_404(CalibrationParameter, pk=parameter_id)
        sub_parameter = (
            get_object_or_404(SubParameter, pk=sub_parameter_id) if sub_parameter_id else None
        )

        issues = QualityAssurance.validate_readings(
            readings, sub_parameter if sub_parameter else parameter
        )

        return JsonResponse({"success": True, "issues": issues})
    except Exception as e:
        logger.exception("api_validate_readings failed: %s", e)
        return JsonResponse({"success": False, "error": str(e)}, status=400)


@login_required
def api_calculate_uncertainty(request):
    """Live uncertainty preview while a technician enters readings.

    Uses the same ``compute_uncertainty_budget`` the model uses, so the preview
    and the stored record cannot disagree. This endpoint previously computed
    Type A from the full-precision standard deviation while the model computed
    it from the six-decimal quantised value, and divided the reference
    uncertainty by k inline rather than through the calculator — so the numbers
    shown while typing could differ from the numbers saved on submit.
    """
    from django.core.serializers.json import json
    from CalSoft.utils import compute_uncertainty_budget

    try:
        data = json.loads(request.body)
        readings = [Decimal(str(v)) for v in data.get("readings", []) if v is not None]
        resolution = Decimal(str(data.get("resolution", 0.001)))
        reference_uncertainty = Decimal(str(data.get("reference_uncertainty", 0.001)))
        coverage_factor = Decimal(str(data.get("coverage_factor", 2.0)))

        budget = compute_uncertainty_budget(
            readings=readings,
            resolution=resolution,
            reference_uncertainty=reference_uncertainty,
            coverage_factor=coverage_factor,
            reference_is_expanded=True,
        )

        if not budget:
            return JsonResponse(
                {"success": False, "error": "At least two readings are required"},
                status=400,
            )

        return JsonResponse(
            {
                "success": True,
                "statistics": {
                    "mean": str(budget["mean"]),
                    "std_dev": str(budget["std_dev"]),
                    "count": budget["count"],
                },
                "uncertainty": {
                    "type_a": str(budget["type_a"]),
                    "type_b": str(budget["type_b"]),
                    "reference": str(budget["reference"]),
                    "combined": str(budget["combined"]),
                    "expanded": str(budget["expanded"]),
                    "coverage_factor": str(budget["coverage_factor"]),
                },
            }
        )
    except (ValueError, TypeError, InvalidOperation) as e:
        logger.warning("api_calculate_uncertainty rejected a payload: %s", e)
        return JsonResponse({"success": False, "error": str(e)}, status=400)


@login_required
def api_session_details(request, session_id):
    session = get_object_or_404(CalibrationSession, id=session_id)

    session_data = {
        "id": session.id,
        "status": session.status,
        "procedure": session.procedure.name if session.procedure else "Unknown",
        "performed_by": session.performed_by.get_full_name() or session.performed_by.username,
        "timestamp": session.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "device": {
            "model": session.device_model,
            "serial": session.device_serial,
            "manufacturer": (
                str(session.device_manufacturer) if session.device_manufacturer else None
            ),
            "description": str(session.device_description) if session.device_description else None,
        },
        "environment": {
            "temperature": str(session.actual_temperature),
            "humidity": str(session.actual_humidity),
            "pressure": str(session.actual_pressure),
        },
        "overall_pass": session.overall_pass,
        "certificate_number": session.certificate_number,
        "notes": session.notes,
        "readings": [],
    }

    readings = CalibrationReading.objects.filter(session=session).select_related(
        "parameter", "sub_parameter", "set_value"
    )

    for r in readings:
        session_data["readings"].append(
            {
                "parameter": r.parameter.name,
                "sub_parameter": r.sub_parameter.name if r.sub_parameter else None,
                "unit": r.parameter.unit,
                "set_value": float(r.set_value.value),
                "readings": r.get_readings_list() if hasattr(r, "get_readings_list") else [],
                "mean": float(r.mean) if r.mean is not None else None,
                "std_dev": (
                    float(r.standard_deviation) if r.standard_deviation is not None else None
                ),
                "error": float(r.error) if r.error is not None else None,
                "type_a_unc": (
                    float(r.type_a_uncertainty) if r.type_a_uncertainty is not None else None
                ),
                "type_b_unc": (
                    float(r.type_b_uncertainty) if r.type_b_uncertainty is not None else None
                ),
                "ref_unc_comp": (
                    float(r.reference_uncertainty_component)
                    if r.reference_uncertainty_component is not None
                    else None
                ),
                "combined_unc": (
                    float(r.combined_uncertainty) if r.combined_uncertainty is not None else None
                ),
                "expanded_unc": (
                    float(r.expanded_uncertainty) if r.expanded_uncertainty is not None else None
                ),
                "passes_tolerance": r.passes_tolerance,
            }
        )

    return JsonResponse(session_data, safe=False)
