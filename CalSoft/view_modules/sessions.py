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
    CalibrationSession, CalibrationReading, CalibrationProcedure,
    Equipment, CalibrationAuditLog, PendingCertificate, CalibrationSchedule,
    SessionParameterResolution, CalibrationParameter, SubParameter, SetValue
)
from CalSoft.forms import SessionSearchForm
from CalSoft.utils import QualityAssurance, CalibrationCalculator, DriftAnalyzer

logger = logging.getLogger(__name__)


@login_required
def session_detail(request, pk):
    session = get_object_or_404(
        CalibrationSession.objects.select_related(
            'procedure', 'performed_by'), pk=pk)
    readings = session.readings.select_related(
        'parameter', 'sub_parameter', 'set_value'
    ).order_by('parameter__order', 'sub_parameter__order', 'set_value__order')

    Department_info = {'name': "Unknown Department", 'workshop': "Unknown Workshop"}

    if session.Department:
        Department_info = {
            'name': session.Department.name,
            'workshop': session.Department.workshop.name if session.Department.workshop else "Unknown Workshop"
        }
    elif session.device_serial:
        try:
            equipment = Equipment.objects.select_related(
                'department', 'department__workshop').get(serial_number=session.device_serial)
            if equipment.department:
                Department_info = {
                    'name': equipment.department.name,
                    'workshop': equipment.department.workshop.name if equipment.department.workshop else "Unknown Workshop"
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
        sub_param_name = reading.sub_parameter.name if reading.sub_parameter else 'Default'
        key = f"{param_name}_{sub_param_name}"

        if key not in readings_by_parameter:
            readings_by_parameter[key] = []
            parameter_stats[key] = {
                'parameter': reading.parameter,
                'sub_parameter': reading.sub_parameter,
                'unit': reading.parameter.unit,
                'readings_count': 0,
                'passed_count': 0,
                'failed_count': 0,
                'max_error': None,
                'avg_uncertainty': None,
                'error_values': []
            }

        readings_by_parameter[key].append(reading)
        parameter_stats[key]['readings_count'] += 1

        if reading.passes_tolerance:
            parameter_stats[key]['passed_count'] += 1
        else:
            parameter_stats[key]['failed_count'] += 1

        if reading.error is not None:
            parameter_stats[key]['error_values'].append(float(reading.error))
            current_max = abs(float(reading.error))
            if (parameter_stats[key]['max_error'] is None or
                    current_max > parameter_stats[key]['max_error']):
                parameter_stats[key]['max_error'] = current_max

    for key in parameter_stats:
        uncertainties = [float(r.expanded_uncertainty) for r in readings_by_parameter[key]
                         if r.expanded_uncertainty is not None]
        if uncertainties:
            parameter_stats[key]['avg_uncertainty'] = sum(uncertainties) / len(uncertainties)

    linearity_analysis = _calculate_linearity_analysis(readings_by_parameter)

    drift_analysis = None
    if session.device_serial:
        drift_data = DriftAnalyzer.analyze_drift(session.device_serial, session.timestamp)
        if drift_data:
            drift_analysis = {
                'trend': drift_data.get('trend', 'No significant drift detected'),
                'recommendation': drift_data.get('recommendation', 'No action required'),
                'parameters': [],
                'stability_index': drift_data.get('stability_index', None),
                'confidence_level': drift_data.get('confidence_level', None),
                'historical_data_points': drift_data.get('historical_data_points', 0)
            }
            for param_data in drift_data.get('parameter_drifts', []):
                param_key = f"{param_data['parameter_name']}_{param_data.get('sub_parameter_name', 'Default')}"
                if param_key in parameter_stats:
                    drift_analysis['parameters'].append({
                        'name': param_data['parameter_name'],
                        'sub_parameter': param_data.get('sub_parameter_name'),
                        'drift_rate': param_data.get('drift_rate'),
                        'drift_direction': param_data.get('drift_direction'),
                        'confidence': param_data.get('confidence'),
                        'historical_readings': param_data.get('historical_readings', 0)
                    })

    standards_used = []
    if session.procedure:
        standards_used = session.procedure.get_standards_used()

    context = {
        'session': session,
        'Department': Department_info,
        'readings_by_parameter': readings_by_parameter,
        'parameter_stats': parameter_stats,
        'linearity_analysis': linearity_analysis,
        'drift_analysis': drift_analysis,
        'standards_used': standards_used,
        'device_description': session.device_description,
        'related_equipment': Equipment.objects.filter(serial_number=session.device_serial).first(),
        'show_sidebar': True,
    }

    return render(request, 'Calibrition/session_detail.html', context)


@login_required
def session_list(request):
    sessions = CalibrationSession.objects.select_related(
        'procedure', 'performed_by'
    ).order_by('-timestamp')

    search_form = SessionSearchForm(request.GET)
    if search_form.is_valid():
        search = search_form.cleaned_data.get('search', '')
        procedure = search_form.cleaned_data.get('procedure')
        performed_by = search_form.cleaned_data.get('performed_by')
        pass_status = search_form.cleaned_data.get('pass_status')
        date_from = search_form.cleaned_data.get('date_from')
        date_to = search_form.cleaned_data.get('date_to')

        if search:
            sessions = sessions.filter(
                Q(device_serial__icontains=search) |
                Q(device_model__icontains=search) |
                Q(certificate_number__icontains=search) |
                Q(device_description__icontains=search)
            )
        if procedure:
            sessions = sessions.filter(procedure=procedure)
        if performed_by:
            sessions = sessions.filter(performed_by=performed_by)
        if pass_status:
            sessions = sessions.filter(overall_pass=(pass_status == 'pass'))
        if date_from:
            sessions = sessions.filter(timestamp__gte=date_from)
        if date_to:
            sessions = sessions.filter(timestamp__lte=date_to)

    paginator = Paginator(sessions, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    total_sessions = sessions.count()
    passed_sessions = sessions.filter(overall_pass=True).count()
    pass_rate = (passed_sessions / total_sessions * 100) if total_sessions > 0 else 0

    context = {
        'page_obj': page_obj,
        'search_form': search_form,
        'total_sessions': total_sessions,
        'passed_sessions': passed_sessions,
        'pass_rate': round(pass_rate, 1),
        'show_sidebar': True,
    }

    return render(request, 'calibration/session_list.html', context)


@login_required
def certificate_validation(request, certificate_number):
    try:
        session = CalibrationSession.objects.get(certificate_number=certificate_number)

        validation_data = {
            'valid': True,
            'certificate_number': certificate_number,
            'device_info': {
                'model': getattr(session, 'device_model', None),
                'serial': getattr(session, 'device_serial', None),
                'manufacturer': getattr(session, 'device_manufacturer', None)
            },
            'calibration_date': session.timestamp.isoformat() if session.timestamp else None,
            'performed_by': session.performed_by.get_full_name() if session.performed_by else None,
            'status': 'PASS' if getattr(session, 'overall_pass', False) else 'FAIL',
            'validation_timestamp': timezone.now().isoformat()
        }

        return JsonResponse(validation_data)

    except CalibrationSession.DoesNotExist:
        return JsonResponse({
            'valid': False,
            'error': 'Certificate not found',
            'certificate_number': certificate_number,
            'validation_timestamp': timezone.now().isoformat()
        }, status=404)

    except Exception as e:
        logger.error(f"Error validating certificate {certificate_number}: {str(e)}")
        return JsonResponse({
            'valid': False,
            'error': str(e),
            'certificate_number': certificate_number,
            'validation_timestamp': timezone.now().isoformat()
        }, status=500)


def _calculate_linearity_analysis(readings_by_parameter):
    linearity_data = {}
    try:
        from CalSoft.utils import TrendAnalysis
        for key, readings in readings_by_parameter.items():
            set_values = [float(r.set_value.value) for r in readings]
            measured_values = [float(r.mean) for r in readings if r.mean]

            if len(set_values) < 2 or len(measured_values) < 2:
                continue

            try:
                slope, intercept = TrendAnalysis.calculate_linear_regression(
                    set_values, measured_values)
                linearity_error = max(abs(float(
                    r.mean) - (slope * float(r.set_value.value) + intercept)) for r in readings if r.mean)
                linearity_data[key] = {
                    'slope': slope,
                    'intercept': intercept,
                    'max_linearity_error': linearity_error
                }
            except Exception:
                linearity_data[key] = {'error': 'Unable to calculate linearity'}
    except Exception as e:
        logger.error(f"Error in _calculate_linearity_analysis: {str(e)}")

    return linearity_data