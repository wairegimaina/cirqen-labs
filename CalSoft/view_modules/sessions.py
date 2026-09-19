import logging
from datetime import timedelta, date
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction, IntegrityError
from django.core.paginator import Paginator
from django.db.models import Q, Case, When, Value, IntegerField
from functools import wraps

from CalSoft.models import (
    CalibrationSession,
    CalibrationReading,
    CalibrationProcedure,
    Equipment,
    CalibrationAuditLog,
    PendingCertificate,
    CalibrationSchedule,
    SessionParameterResolution,
    CalibrationParameter,
    SubParameter,
    SetValue,
)
from CalSoft.forms import SessionSearchForm
from CalSoft.utils import (
    QualityAssurance,
    CalibrationCalculator,
    DriftAnalyzer,
    grade_drift,
    years_until_tolerance_breach,
)

logger = logging.getLogger(__name__)


@login_required
def session_detail(request, pk):
    session = get_object_or_404(
        CalibrationSession.objects.select_related("procedure", "performed_by"), pk=pk
    )
    readings = session.readings.select_related("parameter", "sub_parameter", "set_value").order_by(
        "parameter__order", "sub_parameter__order", "set_value__order"
    )

    Department_info = {"name": "Unknown Department", "workshop": "Unknown Workshop"}

    if session.Department:
        Department_info = {
            "name": session.Department.name,
            "workshop": (
                session.Department.workshop.name
                if session.Department.workshop
                else "Unknown Workshop"
            ),
        }
    elif session.device_serial:
        try:
            equipment = Equipment.objects.select_related("department", "department__workshop").get(
                serial_number=session.device_serial
            )
            if equipment.department:
                Department_info = {
                    "name": equipment.department.name,
                    "workshop": (
                        equipment.department.workshop.name
                        if equipment.department.workshop
                        else "Unknown Workshop"
                    ),
                }
                if not session.Department:
                    session.Department = equipment.department
                    session.save()
        except Equipment.DoesNotExist:
            pass

    readings_by_parameter = {}
    parameter_stats = {}
    for reading in readings:
        param_name = reading.parameter.name
        sub_param_name = reading.sub_parameter.name if reading.sub_parameter else "Default"
        key = f"{param_name}_{sub_param_name}"

        if key not in readings_by_parameter:
            readings_by_parameter[key] = []
            parameter_stats[key] = {
                "parameter": reading.parameter,
                "sub_parameter": reading.sub_parameter,
                "unit": reading.parameter.unit,
                "readings_count": 0,
                "passed_count": 0,
                "failed_count": 0,
                "max_error": None,
                "avg_uncertainty": None,
                "error_values": [],
            }

        readings_by_parameter[key].append(reading)
        parameter_stats[key]["readings_count"] += 1

        if reading.passes_tolerance:
            parameter_stats[key]["passed_count"] += 1
        else:
            parameter_stats[key]["failed_count"] += 1

        if reading.error is not None:
            parameter_stats[key]["error_values"].append(float(reading.error))
            current_max = abs(float(reading.error))
            if (
                parameter_stats[key]["max_error"] is None
                or current_max > parameter_stats[key]["max_error"]
            ):
                parameter_stats[key]["max_error"] = current_max

    for key in parameter_stats:
        uncertainties = [
            float(r.expanded_uncertainty)
            for r in readings_by_parameter[key]
            if r.expanded_uncertainty is not None
        ]
        if uncertainties:
            parameter_stats[key]["avg_uncertainty"] = sum(uncertainties) / len(uncertainties)

    linearity_analysis = _calculate_linearity_analysis(readings_by_parameter)

    drift_analysis = _build_drift_analysis(session)

    standards_used = []
    if session.procedure:
        standards_used = session.procedure.get_standards_used()

    context = {
        "session": session,
        "Department": Department_info,
        "readings_by_parameter": readings_by_parameter,
        "parameter_stats": parameter_stats,
        "linearity_analysis": linearity_analysis,
        "drift_analysis": drift_analysis,
        "standards_used": standards_used,
        "device_description": session.device_description,
        "related_equipment": Equipment.objects.filter(serial_number=session.device_serial).first(),
        "show_sidebar": True,
    }

    return render(request, "Calibrition/session_detail.html", context)


@login_required
def certificate_validation(request, certificate_number):
    try:
        session = CalibrationSession.objects.get(certificate_number=certificate_number)

        validation_data = {
            "valid": True,
            "certificate_number": certificate_number,
            "device_info": {
                "model": getattr(session, "device_model", None),
                "serial": getattr(session, "device_serial", None),
                "manufacturer": getattr(session, "device_manufacturer", None),
            },
            "calibration_date": session.timestamp.isoformat() if session.timestamp else None,
            "performed_by": session.performed_by.get_full_name() if session.performed_by else None,
            "status": "PASS" if getattr(session, "overall_pass", False) else "FAIL",
            "validation_timestamp": timezone.now().isoformat(),
        }

        return JsonResponse(validation_data)

    except CalibrationSession.DoesNotExist:
        return JsonResponse(
            {
                "valid": False,
                "error": "Certificate not found",
                "certificate_number": certificate_number,
                "validation_timestamp": timezone.now().isoformat(),
            },
            status=404,
        )

    except Exception as e:
        logger.error(f"Error validating certificate {certificate_number}: {str(e)}")
        return JsonResponse(
            {
                "valid": False,
                "error": str(e),
                "certificate_number": certificate_number,
                "validation_timestamp": timezone.now().isoformat(),
            },
            status=500,
        )


def _tolerance_for_parameter(session, parameter_name):
    """The tolerance on record for a named parameter in this session's procedure.

    Drift can only be graded against the parameter's own tolerance, and the
    historical table stores names rather than foreign keys.
    """
    if not session.procedure_id:
        return None
    parameter = (
        CalibrationParameter.objects
        .filter(procedure_id=session.procedure_id, name=parameter_name)
        .only("tolerance")
        .first()
    )
    return parameter.tolerance if parameter else None


def _build_drift_analysis(session):
    """Summarise historical drift for the device this session calibrated.

    ``DriftAnalyzer.analyze_drift`` returns ``{parameter: {set_value: metrics}}``.
    The previous consumer read five keys that shape never contains —
    ``trend``, ``recommendation``, ``stability_index``, ``confidence_level`` and
    ``parameter_drifts`` — so every lookup fell through to its default and the
    page displayed the literal string "No significant drift detected" for every
    device, whatever the data said. This reads the real shape.

    Grades are relative to each parameter's tolerance (see
    ``CalSoft.utils.grade_drift``), because an absolute drift rate cannot be
    compared across parameters measured in different units.
    """
    if not session.device_serial:
        return None

    drift_data = DriftAnalyzer.analyze_drift(session.device_serial, session.timestamp)
    if not drift_data:
        return None

    rows = []
    worst_fraction = None
    total_points = 0

    for parameter_name, by_set_value in sorted(drift_data.items()):
        tolerance = _tolerance_for_parameter(session, parameter_name)

        for set_value, metrics in sorted(by_set_value.items()):
            if not metrics:
                continue

            per_year = metrics.get("drift_rate_per_year") or 0
            label, recommendation, fraction = grade_drift(per_year, tolerance)
            years_left = years_until_tolerance_breach(
                metrics.get("max_error") or 0, per_year, tolerance
            )

            total_points += metrics.get("number_of_calibrations", 0)
            if fraction is not None and (worst_fraction is None or fraction > worst_fraction):
                worst_fraction = fraction

            rows.append({
                "name": parameter_name,
                "set_value": set_value,
                "sessions": metrics.get("number_of_calibrations", 0),
                "drift_per_year": per_year,
                "drift_per_day": metrics.get("drift_rate_per_day"),
                "total_drift": metrics.get("total_drift"),
                "direction": (
                    "Stable" if abs(per_year) < 1e-9
                    else "Increasing" if per_year > 0
                    else "Decreasing"
                ),
                "grade": label,
                "recommendation": recommendation,
                "fraction_of_tolerance": fraction,
                "years_until_breach": years_left,
                "tolerance": tolerance,
                "uncertainty_trend": metrics.get("uncertainty_trend"),
                "average_uncertainty": metrics.get("average_uncertainty"),
                "first_calibration": metrics.get("first_calibration"),
                "last_calibration": metrics.get("last_calibration"),
                "equation": metrics.get("drift_equation"),
            })

    if not rows:
        return None

    # The overall view is driven by the worst-graded point, not by an average:
    # one parameter drifting out of tolerance is the thing worth surfacing.
    worst = max(
        rows,
        key=lambda r: (r["fraction_of_tolerance"] is not None,
                       r["fraction_of_tolerance"] or 0),
    )

    return {
        "parameters": rows,
        "points_analysed": len(rows),
        "historical_data_points": total_points,
        "worst_grade": worst["grade"],
        "worst_fraction_of_tolerance": worst_fraction,
        "trend": (
            f"{worst['name']} at {worst['set_value']} shows the largest drift: "
            f"{worst['drift_per_year']:+.6f} per year ({worst['grade'].lower()})"
        ),
        "recommendation": worst["recommendation"],
    }


def _calculate_linearity_analysis(readings_by_parameter):
    """Least-squares linearity per parameter, from the shared calculator.

    This previously called ``TrendAnalysis.calculate_linear_regression``, which
    does not exist on that class. A bare ``except Exception`` swallowed the
    ``AttributeError``, so every parameter silently reported "Unable to
    calculate linearity" and the failure never surfaced.

    It now uses ``CalibrationCalculator.calculate_linearity`` — the one
    implementation in the codebase — and pairs set values with means
    positionally, skipping any reading without a mean so the two series cannot
    fall out of step.
    """
    linearity_data = {}
    calculator = CalibrationCalculator()

    for key, readings in readings_by_parameter.items():
        pairs = [
            (float(r.set_value.value), float(r.mean))
            for r in readings
            if r.set_value is not None and r.mean is not None
        ]
        if len(pairs) < 2:
            continue

        set_values = [p[0] for p in pairs]
        measured_values = [p[1] for p in pairs]

        try:
            result = calculator.calculate_linearity(set_values, measured_values)
        except (ValueError, ZeroDivisionError, InvalidOperation) as exc:
            # A genuine data problem (fewer than two distinct points, or an
            # unusable value). Logged at warning so it is visible rather than
            # silently degraded, and reported to the page as a real reason.
            logger.warning(
                "Linearity unavailable for %s: %s", key, exc
            )
            linearity_data[key] = {"error": str(exc)}
            continue

        linearity_data[key] = {
            "slope": float(result["slope"]),
            "intercept": float(result["intercept"]),
            "max_linearity_error": abs(float(result["max_linearity_error_units"])),
            "max_linearity_error_percent": float(result["max_linearity_error_percent"]),
            "points": len(pairs),
        }

    return linearity_data
