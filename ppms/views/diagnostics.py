"""ppms.views — celery test, task status, and debug/log endpoints."""
from calendar import monthrange
from django.db.models import Count
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from ..models import PPMSchedule
from Inventory.models import Equipment, Department, EquipmentDescription
from ..tasks import initialize_ppm_schedule_with_logic, normalize_ppm_schedules, smart_reorganize_ppm_schedules
from openpyxl import Workbook
from workshop.models import Workshop
import logging
from django.utils import timezone
from ..ppm_pdf_generator import create_ppm_pdf_response
from celery.result import AsyncResult
logger = logging.getLogger(__name__)

# sibling modules in this package
from .helpers import get_user_access_context


@login_required
def test_celery_connection(request):
    """Test endpoint to verify Celery is working properly"""
    results = {
        'timestamp': datetime.now().isoformat(),
        'celery_status': 'unknown',
        'broker_connection': 'unknown',
        'test_task': 'unknown',
        'ppm_task_import': 'unknown',
        'errors': [],
        'workshop_info': {}
    }

    try:
        # Test 1: Celery app configuration
        from Equiper.celery import app as celery_app
        results['celery_status'] = 'configured'
        results['broker_url'] = str(celery_app.conf.broker_url)
        results['result_backend'] = str(celery_app.conf.result_backend)

        # Test 2: Broker connection
        try:
            conn = celery_app.connection()
            conn.ensure_connection(max_retries=3, timeout=5)
            results['broker_connection'] = '✓ Connected'
            conn.release()
        except Exception as e:
            results['broker_connection'] = f'✗ Failed: {str(e)}'
            results['errors'].append(f'Broker connection: {str(e)}')

        # Test 3: Simple test task
        try:
            from Equiper.celery import test_celery
            task = test_celery.delay()
            results['test_task'] = f'✓ Queued (ID: {task.id})'
            results['test_task_id'] = str(task.id)
        except Exception as e:
            results['test_task'] = f'✗ Failed: {str(e)}'
            results['errors'].append(f'Test task: {str(e)}')

        # Test 4: PPM task import
        try:
            from ppms.tasks import initialize_ppm_schedule_with_logic
            results['ppm_task_import'] = '✓ Imported successfully'
            results['ppm_task_name'] = initialize_ppm_schedule_with_logic.name
        except Exception as e:
            results['ppm_task_import'] = f'✗ Failed: {str(e)}'
            results['errors'].append(f'PPM task import: {str(e)}')

        # Test 5: Workshop info
        access_context = get_user_access_context(request)
        if access_context and access_context['workshop_id']:
            try:
                workshop = Workshop.objects.get(id=access_context['workshop_id'])
                active_equipment = Equipment.objects.filter(
                    workshop_id=workshop.id,
                    active_status=True
                ).count()
                scheduled_equipment = PPMSchedule.objects.filter(
                    workshop_id=workshop.id,
                    equipment__active_status=True
                ).count()

                results['workshop_info'] = {
                    'id': str(workshop.id),
                    'name': workshop.name,
                    'active_equipment': active_equipment,
                    'scheduled_equipment': scheduled_equipment,
                    'unscheduled_equipment': active_equipment - scheduled_equipment
                }
            except Exception as e:
                results['workshop_info'] = {'error': str(e)}

        # Overall status
        results['overall_status'] = '✓ All tests passed' if not results['errors'] else '✗ Some tests failed'

    except Exception as e:
        results['errors'].append(f'General error: {str(e)}')
        results['overall_status'] = '✗ Test suite failed'

    return JsonResponse(results, json_dumps_params={'indent': 2})


@login_required
def check_task_status(request, task_id):
    """Check the real-time status of a Celery task"""
    try:
        task = AsyncResult(task_id)

        response_data = {
            'task_id': task_id,
            'state': task.state,
            'ready': task.ready(),
            'successful': task.successful() if task.ready() else None,
            'failed': task.failed() if task.ready() else None,
            'timestamp': datetime.now().isoformat()
        }

        if task.state == 'PENDING':
            response_data['status'] = 'waiting'
            response_data['message'] = 'Task is waiting in queue'

        elif task.state == 'STARTED':
            response_data['status'] = 'running'
            response_data['message'] = 'Task has started processing'

        elif task.state == 'PROGRESS':
            response_data['status'] = 'in_progress'
            response_data['progress'] = task.info
            response_data['message'] = task.info.get('status', 'Processing...')

        elif task.state == 'SUCCESS':
            response_data['status'] = 'completed'
            response_data['result'] = task.result
            response_data['message'] = 'Task completed successfully'

        elif task.state == 'FAILURE':
            response_data['status'] = 'failed'
            response_data['error'] = str(task.info)
            response_data['message'] = f'Task failed: {str(task.info)}'
            if hasattr(task, 'traceback'):
                response_data['traceback'] = task.traceback

        else:
            response_data['status'] = 'unknown'
            response_data['message'] = f'Unknown state: {task.state}'

        return JsonResponse(response_data, json_dumps_params={'indent': 2})

    except Exception as e:
        return JsonResponse({
            'error': str(e),
            'task_id': task_id,
            'timestamp': datetime.now().isoformat()
        }, status=500)


@login_required
def debug_user_access(request):
    access_context = get_user_access_context(request)

    debug_info = {
        'user': request.user.username,
        'access_context': access_context,
        'session_workshop_id': request.session.get('workshop_id'),
        'session_department_id': request.session.get('department_id'),
        'has_userprofile': hasattr(request.user, 'userprofile'),
    }

    if hasattr(request.user, 'userprofile'):
        profile = request.user.userprofile
        debug_info.update({
            'profile_role': getattr(profile, 'role', 'No role'),
            'profile_level': getattr(profile, 'level', 'No level'),
            'profile_has_workshop': profile.workshop is not None,
            'profile_has_department': profile.department is not None,
        })

        if profile.workshop:
            debug_info['profile_workshop_id'] = profile.workshop.id
            debug_info['profile_workshop_name'] = profile.workshop.name

        if profile.department:
            debug_info['profile_department_id'] = profile.department.id
            debug_info['profile_department_name'] = profile.department.name
            debug_info['profile_department_workshop_id'] = profile.department.workshop_id if profile.department.workshop else None

        try:
            profile.clean()
            debug_info['profile_validation'] = 'Valid'
        except ValidationError as e:
            debug_info['profile_validation'] = f'Invalid: {e}'

    return JsonResponse(debug_info, indent=2)


@login_required
def view_logs(request):
    """View recent log entries"""
    if not request.user.is_staff:
        messages.error(request, "Only staff can view logs")
        return redirect('ppm_dashboard')

    log_type = request.GET.get('type', 'ppm')
    lines = int(request.GET.get('lines', 100))

    import os
    from django.conf import settings

    log_files = {
        'ppm': os.path.join(settings.BASE_DIR, 'logs', 'ppm.log'),
        'celery': os.path.join(settings.BASE_DIR, 'logs', 'celery.log'),
        'debug': os.path.join(settings.BASE_DIR, 'logs', 'debug.log'),
    }

    log_file = log_files.get(log_type)

    if not log_file or not os.path.exists(log_file):
        return HttpResponse(f"Log file not found: {log_file}", content_type='text/plain')

    try:
        with open(log_file, 'r') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:]
            content = ''.join(recent_lines)

        return HttpResponse(content, content_type='text/plain')
    except Exception as e:
        return HttpResponse(f"Error reading log: {str(e)}", content_type='text/plain')
