"""ppms.views — task status and debug/log endpoints."""
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
