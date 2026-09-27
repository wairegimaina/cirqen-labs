import logging
from datetime import timedelta
from django.utils import timezone
from django.shortcuts import render
from django.contrib import messages

from CalSoft.models import (
    Equipment, CalibrationProcedure,
    CalibrationSession, CalibrationReading, HistoricalCalibration,
    CalibrationAuditLog, Standard
)
from calSchedules.models import CalibrationSchedule
from calSchedules.grouping import next_due_date

logger = logging.getLogger(__name__)


def _store_historical_data(session):
    try:
        for reading in session.readings.all():
            if reading.mean is not None:
                try:
                    HistoricalCalibration.objects.create(
                        device_serial=session.device_serial,
                        parameter_name=reading.parameter.name,
                        sub_parameter_name=reading.sub_parameter.name if reading.sub_parameter else '',
                        set_value=reading.set_value.value,
                        measured_value=reading.mean,
                        error=reading.error,
                        uncertainty=reading.expanded_uncertainty,
                        calibration_date=session.timestamp
                    )
                except Exception as e:
                    logger.error(f"Error storing historical data: {str(e)}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in _store_historical_data: {str(e)}", exc_info=True)


def _get_grouped_schedule_if_exists(current_schedule, equipment):
    try:
        same_description_equipment = Equipment.objects.filter(
            description=equipment.description, active_status=True
        ).exclude(id=equipment.id)

        for eq in same_description_equipment:
            grouped_schedule = CalibrationSchedule.objects.filter(
                equipment=eq, status__in=['pending', 'pushed'],
                active_status=True, scheduled_month__gte=current_schedule.scheduled_month
            ).order_by('scheduled_month').first()

            if grouped_schedule:
                return grouped_schedule
    except Exception as e:
        logger.error(f"Error checking grouped schedule: {str(e)}")
    return None


def _find_grouped_schedule_for_equipment(equipment):
    if not equipment or not equipment.description:
        return None
    try:
        same_description_equipment = Equipment.objects.filter(
            description=equipment.description, active_status=True
        ).exclude(id=equipment.id)
        for eq in same_description_equipment:
            grouped_schedule = CalibrationSchedule.objects.filter(
                equipment=eq, status__in=['pending', 'pushed'], active_status=True
            ).order_by('scheduled_month').first()
            if grouped_schedule:
                return grouped_schedule
    except Exception as e:
        logger.error(f"Error finding grouped schedule: {str(e)}")
    return None


def _create_next_schedule(equipment, procedure):
    try:
        today = timezone.localdate()
        interval_months = getattr(procedure, "interval_months", None) or getattr(procedure, "calibration_period", 12) or 12
        # Calendar months, ending on the last day of the due month.
        # `today + timedelta(days=30 * months)` treated every month as 30 days,
        # so a 12-month interval came out 5 days short and the due date slid
        # into the previous month after about six cycles.
        next_month = next_due_date(today, interval_months)
        new_schedule, _ = CalibrationSchedule.objects.get_or_create(
            equipment=equipment,
            scheduled_month=next_month,
            defaults={
                "calibration_procedure": procedure,
                "status": "pending",
            },
        )
        try:
            return CalibrationSchedule.objects.get(pk=new_schedule.pk)
        except CalibrationSchedule.DoesNotExist:
            return CalibrationSchedule.objects.filter(
                equipment=equipment,
                status__in=["pending", "pushed", "pending_approval"],
            ).order_by("scheduled_month").first()
    except Exception as e:
        logger.error(f"Error creating next schedule: {str(e)}")
        return None


def _render_calibration_form_with_context(request, form, equipment, schedule,
                                        return_to, return_page, return_department,
                                        return_month, return_year, return_search, reschedule_preference):
    from CalSoft.models import CalibrationProcedure
    equipment_list = Equipment.objects.all().order_by('description')
    procedures = CalibrationProcedure.objects.filter(active_status=True).order_by('name')
    return render(request, 'Calibrition/calibration.html', {
        'pending_schedules': CalibrationSchedule.objects.filter(status__in=['pending', 'pushed']),
        'equipment_list': equipment_list,
        'recommended_procedures': [],
        'other_procedures': [{'id': p.id, 'name': p.name} for p in procedures],
        'form': form, 'selected_equipment': equipment, 'selected_schedule': schedule,
        'return_to': return_to, 'return_page': return_page, 'return_department': return_department,
        'return_month': return_month, 'return_year': return_year, 'return_search': return_search,
        'reschedule_preference': reschedule_preference, 'show_sidebar': True,
    })

STANDARD_DUE_WARNING_DAYS = 30


def _reference_standard_status(procedure, today=None):
    """Split the procedure's reference standards into (expired, due_soon).

    ISO/IEC 17025 needs every reference to be within its own calibration when
    it is used. Parameters name their standard by serial number
    (``CalibrationParameter.standard_reference``); a serial with no matching
    ``Standard`` row is skipped here, as ``get_standards_used`` already logs it.
    """
    today = today or timezone.localdate()
    serials = {
        p.standard_reference for p in procedure.parameters.all() if p.standard_reference
    }
    expired, due_soon = [], []
    for standard in Standard.objects.filter(serial_number__in=serials).order_by("name"):
        due = standard.calibration_due_date
        if not due:
            continue
        if due < today:
            expired.append(standard)
        elif due <= today + timedelta(days=STANDARD_DUE_WARNING_DAYS):
            due_soon.append(standard)
    return expired, due_soon
