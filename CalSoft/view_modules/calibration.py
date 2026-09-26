import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_GET
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction
from django.urls import reverse

from CalSoft.models import (
    CalibrationReading, SessionParameterResolution, CalibrationParameter,
    SubParameter, SetValue, CalibrationAuditLog, EquipmentCalibrationProcedure
)
from calSchedules.models import CalibrationSchedule
from calSchedules.grouping import next_due_date
from CalSoft.models import Equipment, CalibrationSession, CalibrationProcedure
from CalSoft.forms import CalibrationSessionForm
from CalSoft.utils import _validate_environmental_conditions

from .calibration_helpers import (
    _store_historical_data, _get_grouped_schedule_if_exists,
    _find_grouped_schedule_for_equipment, _create_next_schedule,
    _render_calibration_form_with_context, _reference_standard_status
)

logger = logging.getLogger(__name__)


@login_required
def complete_calibration_from_session(request, session_id):
    try:
        session = get_object_or_404(CalibrationSession, pk=session_id)
        schedule = None
        if session.device_serial:
            try:
                equipment = Equipment.objects.get(serial_number=session.device_serial)
                schedule = CalibrationSchedule.objects.filter(
                    equipment=equipment, status__in=['pending', 'in_progress', 'pushed']
                ).first()
            except Equipment.DoesNotExist:
                pass

        if schedule:
            schedule.status = 'completed'
            schedule.save()
            CalibrationAuditLog.objects.create(
                user=request.user, action='complete_schedule',
                description=f'Completed schedule {schedule.id} from calibration session {session_id}',
                schedule=schedule, equipment=schedule.equipment, session=session
            )
            messages.success(request, f'Schedule completed for {schedule.equipment.description}')
        else:
            messages.warning(request, 'No matching schedule found to complete.')
        return redirect('calibration:cal-dashboard')
    except Exception as e:
        logger.error(f"Error completing calibration from session {session_id}: {str(e)}")
        messages.error(request, f"Error completing calibration: {str(e)}")
        return redirect('calibration:cal-dashboard')


@login_required
@require_http_methods(["GET", "POST"])
def perform_calibration_global(request):
    if request.method == 'POST':
        return _handle_calibration_post(request)
    return _handle_calibration_get(request)


def _handle_calibration_post(request):
    equipment_id = request.POST.get('equipment')
    procedure_id = request.POST.get('procedure')
    schedule_id = request.POST.get('schedule')
    reschedule_preference = request.POST.get('reschedule_preference', 'maintain_original')
    return_to = request.POST.get('return_to', 'dashboard')
    return_page = request.POST.get('return_page', '1')
    return_department = request.POST.get('return_department', '')
    return_month = request.POST.get('return_month', '')
    return_year = request.POST.get('return_year', '')
    return_search = request.POST.get('return_search', '')

    if not equipment_id:
        messages.error(request, "Equipment selection is required.")
        return redirect('schedule:pending_calibrations')

    try:
        equipment = Equipment.objects.get(pk=equipment_id)
    except Equipment.DoesNotExist:
        messages.error(request, "Selected equipment not found.")
        return redirect('schedule:pending_calibrations')

    procedure = None
    schedule = None

    if procedure_id:
        try:
            procedure = CalibrationProcedure.objects.get(pk=procedure_id, active_status=True)
        except CalibrationProcedure.DoesNotExist:
            messages.error(request, "Selected calibration procedure not found or inactive.")
            return redirect('schedule:pending_calibrations')

    if schedule_id:
        try:
            schedule = CalibrationSchedule.objects.get(pk=schedule_id)
            if not procedure:
                procedure = schedule.calibration_procedure
            equipment = schedule.equipment
        except CalibrationSchedule.DoesNotExist:
            messages.error(request, "Selected schedule not found.")
            return redirect('schedule:pending_calibrations')

    if not procedure:
        mapping = EquipmentCalibrationProcedure.objects.filter(
            equipment=equipment, is_default=True
        ).first()
        if mapping:
            procedure = mapping.calibration_procedure
        else:
            messages.error(request, "No calibration procedure found for this equipment.")
            return redirect('schedule:pending_calibrations')

    # Refuse before anything is written: a certificate traced to an expired
    # reference is not valid (ISO/IEC 17025 6.4 / 6.5).
    expired, due_soon = _reference_standard_status(procedure)
    if expired:
        names = ", ".join(
            f"{s.name} (S/N {s.serial_number}, due {s.calibration_due_date:%d %b %Y})" for s in expired
        )
        messages.error(
            request,
            f"This calibration was not saved: the reference standard is past its calibration "
            f"due date: {names}. Recalibrate the standard, update its record, then repeat the session.",
        )
        return redirect('schedule:pending_calibrations')
    for s in due_soon:
        messages.warning(
            request,
            f"Reference standard {s.name} (S/N {s.serial_number}) is due for calibration on "
            f"{s.calibration_due_date:%d %b %Y}.",
        )

    schedule_was_created = False
    if not schedule:
        grouped_schedule = _find_grouped_schedule_for_equipment(equipment)
        scheduled_month = grouped_schedule.scheduled_month if grouped_schedule else timezone.localdate()
        schedule, created = CalibrationSchedule.objects.get_or_create(
            equipment=equipment,
            scheduled_month=scheduled_month,
            defaults={
                "calibration_procedure": procedure,
                "status": "pending",
            },
        )
        schedule_was_created = created or not schedule.pk

    session_form = CalibrationSessionForm(request.POST)
    if not session_form.is_valid():
        return _render_calibration_form_with_context(request, session_form, equipment, schedule,
            return_to, return_page, return_department, return_month, return_year, return_search,
            reschedule_preference)

    temperature = session_form.cleaned_data.get('actual_temperature')
    humidity = session_form.cleaned_data.get('actual_humidity')
    pressure = session_form.cleaned_data.get('actual_pressure')

    env_warnings = _validate_environmental_conditions(temperature, humidity, pressure, procedure)
    for warning in env_warnings:
        messages.warning(request, warning)

    if schedule and not schedule_was_created:
        grouped_schedule = _get_grouped_schedule_if_exists(schedule, equipment)
        if grouped_schedule and grouped_schedule.id != schedule.id:
            if reschedule_preference == 'follow_group':
                schedule = grouped_schedule
            elif reschedule_preference == 'create_new':
                new_schedule = _create_next_schedule(equipment, procedure)
                if new_schedule:
                    schedule = new_schedule

    schedule = _ensure_saved_schedule(schedule, equipment, procedure)

    session = CalibrationSession.objects.create(
        procedure=procedure, schedule=schedule, performed_by=request.user,
        timestamp=timezone.now(), device_model=equipment.model,
        device_serial=equipment.serial_number, device_manufacturer=equipment.manufacturer,
        device_description=equipment.description, actual_temperature=temperature,
        actual_humidity=humidity, actual_pressure=pressure,
        notes=session_form.cleaned_data.get('notes', ''), status='pending_review'
    )

    return _process_readings(request, session, procedure, equipment, schedule,
        return_department, return_month, return_year)


def _ensure_saved_schedule(schedule, equipment, procedure):
    if not schedule:
        return None

    scheduled_month = getattr(schedule, "scheduled_month", None) or timezone.localdate()
    schedule_pk = getattr(schedule, "pk", None)

    if schedule_pk:
        try:
            return CalibrationSchedule.objects.get(pk=schedule_pk)
        except CalibrationSchedule.DoesNotExist:
            logger.warning(
                f"CalibrationSchedule {schedule_pk} no longer exists; creating replacement "
                f"for equipment {equipment.id}"
            )

    schedule, _ = CalibrationSchedule.objects.get_or_create(
        equipment=equipment,
        scheduled_month=scheduled_month,
        defaults={
            "calibration_procedure": procedure,
            "status": "pending",
        },
    )

    try:
        return CalibrationSchedule.objects.get(pk=schedule.pk)
    except CalibrationSchedule.DoesNotExist:
        fallback_schedule = CalibrationSchedule.objects.filter(
            equipment=equipment,
            status__in=["pending", "pushed", "pending_approval"],
        ).exclude(scheduled_month=scheduled_month).order_by("scheduled_month").first()
        if fallback_schedule:
            logger.warning(
                f"Created CalibrationSchedule for equipment {equipment.id} was removed; "
                f"using existing schedule {fallback_schedule.pk}"
            )
            return fallback_schedule
        return schedule


def _process_readings(request, session, procedure, equipment, schedule, return_department, return_month, return_year):
    parameters = CalibrationParameter.objects.filter(procedure=procedure).order_by('order')
    readings_data = {}

    for key, value in request.POST.items():
        if key.startswith("reading_") and value.strip():
            try:
                parts = key.split("_")
                if len(parts) >= 5:
                    _, param_id, sub_param_id, set_value_id, _ = parts[:5]
                    reading_key = f"{param_id}_{sub_param_id}_{set_value_id}"
                    reading_value = Decimal(value.strip())
                    readings_data.setdefault(reading_key, []).append(reading_value)
            except (ValueError, InvalidOperation, IndexError):
                messages.error(request, f"Invalid reading format for key '{key}'")
                return redirect('schedule:pending_calibrations')

    overall_pass = True
    for parameter in parameters:
        resolution_key = f"resolution_{parameter.id}"
        resolution_value = request.POST.get(resolution_key, '')
        try:
            resolution = Decimal(resolution_value)
        except (InvalidOperation, ValueError):
            resolution = Decimal('0.001')

        SessionParameterResolution.objects.create(
            session=session, parameter=parameter, resolution=resolution)

        sub_parameters = SubParameter.objects.filter(parameter=parameter).order_by('order')
        sub_parameters = sub_parameters if sub_parameters.exists() else [None]

        for sub_param in sub_parameters:
            set_values = SetValue.objects.filter(parameter=parameter).order_by('order')
            if sub_param:
                set_values = set_values.filter(sub_parameter=sub_param)
            else:
                set_values = set_values.filter(sub_parameter=None)

            if not set_values.exists():
                set_values = [type('obj', (object,), {'id': 'default', 'value': Decimal('0')})()]

            for sv in set_values:
                reading_key = f"{parameter.id}_{sub_param.id if sub_param else 'null'}_{sv.id}"
                readings = readings_data.get(reading_key, [])

                if not readings:
                    overall_pass = False
                    continue

                reading = CalibrationReading.objects.create(
                    session=session, parameter=parameter, sub_parameter=sub_param, set_value=sv,
                )

                for i, val in enumerate(readings[:parameter.num_readings], 1):
                    try:
                        reading.set_reading(i, val)
                    except Exception as e:
                        logger.error(f"Error setting reading: {str(e)}")

                reading.calculate_statistics()
                if not reading.passes_tolerance:
                    overall_pass = False

    session.overall_pass = overall_pass

    # Record when this calibration next falls due.
    #
    # `next_calibration_due` is read by the machine reports and the equipment
    # export, but nothing ever wrote it, so both have always shown "Not Set".
    # The due date is the last day of the month the interval lands in, giving
    # the workshop that whole month to schedule the visit.
    interval_months = (
        getattr(schedule, "calibration_period", None)
        or getattr(procedure, "calibration_period", None)
        or 12
    )
    session.next_calibration_due = next_due_date(
        timezone.localdate(session.timestamp) if session.timestamp else timezone.localdate(),
        interval_months,
    )
    session.save()

    if schedule:
        schedule.status = 'pending_approval'
        schedule.save()

    schedule = _ensure_saved_schedule(schedule, equipment, procedure)
    _store_historical_data(session)

    CalibrationAuditLog.objects.create(
        user=request.user, action='complete_calibration',
        description=f"Calibration completed for {equipment.description}",
        schedule=schedule, equipment=equipment, session=session
    )

    messages.success(request, f"Calibration session completed. Result: {'PASSED' if overall_pass else 'FAILED'}")

    params = []
    if return_department:
        params.append(f"return_department={return_department}")
    if return_month:
        params.append(f"return_month={return_month}")
    if return_year:
        params.append(f"return_year={return_year}")
    if schedule and schedule.pk:
        params.append(f"selected={schedule.id}")
    params.append("returned_from=calibration")
    params.append(f"result={'pass' if overall_pass else 'fail'}")

    return_url = reverse("schedule:pending_calibrations")
    if params:
        return_url += "?" + "&".join(params)
    return redirect(return_url)


def _handle_calibration_get(request):
    equipment_id = request.GET.get('equipment')
    schedule_id = request.GET.get('schedule')
    quick_select = request.GET.get('quick_select') == 'true'
    check_grouping = request.GET.get('check_grouping', 'false') == 'true'

    return_to = request.GET.get("return_to", "dashboard")
    return_page = request.GET.get("page", "1")
    return_department = request.GET.get("return_department") or request.GET.get("department", "")
    return_month = request.GET.get("return_month") or request.GET.get("month", "")
    return_year = request.GET.get("return_year") or request.GET.get("year", "")
    return_search = request.GET.get("return_search") or request.GET.get("search", "")

    selected_equipment = None
    selected_schedule = None
    grouped_schedule_info = None

    if equipment_id:
        try:
            selected_equipment = Equipment.objects.select_related(
                'description', 'department'
            ).get(id=equipment_id)
        except (Equipment.DoesNotExist, ValueError, TypeError):
            messages.error(request, f"Equipment with ID {equipment_id} not found")

    if schedule_id:
        try:
            selected_schedule = CalibrationSchedule.objects.select_related(
                'equipment__description', 'equipment__department', 'calibration_procedure'
            ).get(id=schedule_id)
            if not selected_equipment and selected_schedule.equipment:
                selected_equipment = selected_schedule.equipment
        except (CalibrationSchedule.DoesNotExist, ValueError, TypeError):
            messages.error(request, f"Schedule with ID {schedule_id} not found")

    if check_grouping and selected_equipment:
        grouped_schedule = _find_grouped_schedule_for_equipment(selected_equipment)
        if grouped_schedule:
            grouped_schedule_info = {
                'exists': True,
                'schedule_id': grouped_schedule.id,
                'scheduled_month': grouped_schedule.scheduled_month,
            }

    pending_schedules = CalibrationSchedule.objects.filter(
        status__in=['pending', 'pushed']
    ).select_related('equipment', 'calibration_procedure').order_by('scheduled_month')

    equipment_list = Equipment.objects.all().order_by('description')
    all_procedures = CalibrationProcedure.objects.filter(active_status=True).order_by('name')

    recommended_procedure_ids = set()
    if selected_equipment and selected_equipment.description:
        sessions_with_same_description = CalibrationSession.objects.filter(
            device_description=selected_equipment.description, status='approved'
        ).select_related('procedure').values_list('procedure_id', flat=True).distinct()
        recommended_procedure_ids = set(sessions_with_same_description)

    recommended_procedures = [{'id': p.id, 'name': p.name, 'is_recommended': True}
        for p in all_procedures if p.id in recommended_procedure_ids]
    other_procedures = [{'id': p.id, 'name': p.name, 'is_recommended': False}
        for p in all_procedures if p.id not in recommended_procedure_ids]

    return render(request, 'Calibrition/calibration.html', {
        'pending_schedules': pending_schedules, 'equipment_list': equipment_list,
        'recommended_procedures': recommended_procedures, 'other_procedures': other_procedures,
        'form': CalibrationSessionForm(), 'selected_equipment': selected_equipment,
        'selected_schedule': selected_schedule, 'quick_select': quick_select,
        'grouped_schedule_info': grouped_schedule_info, 'return_to': return_to,
        'return_page': return_page, 'return_department': return_department,
        'return_month': return_month, 'return_year': return_year, 'return_search': return_search,
        'show_sidebar': True,
    })
