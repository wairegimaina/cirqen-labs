import logging
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_GET
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseForbidden
from django.contrib import messages
from django.db import transaction
from django.utils import timezone

from CalSoft.models import CalibrationWorkflow, CalibrationAuditLog, CalibrationSchedule

logger = logging.getLogger(__name__)


@login_required
def calibration_workflow_view(request, schedule_id):
    schedule = get_object_or_404(CalibrationSchedule, pk=schedule_id)
    workflow_steps = CalibrationWorkflow.objects.filter(
        schedule=schedule).order_by('step_order')

    context = {
        'schedule': schedule,
        'workflow_steps': workflow_steps,
        'show_sidebar': True,
    }

    return render(request, 'Calibration/workflow_view.html', context)


@login_required
@require_http_methods(["POST"])
def update_workflow_step(request, step_id):
    workflow_step = get_object_or_404(CalibrationWorkflow, pk=step_id)

    try:
        with transaction.atomic():
            status = request.POST.get('status')
            valid_statuses = [
                choice[0] for choice in CalibrationWorkflow._meta.get_field('status').choices]
            if status in valid_statuses:
                workflow_step.status = status
                if status == 'in_progress' and not workflow_step.started_at:
                    workflow_step.started_at = timezone.now()
                elif status in ['completed', 'skipped', 'failed'] and not workflow_step.completed_at:
                    workflow_step.completed_at = timezone.now()
                workflow_step.notes = request.POST.get('notes', '')
                workflow_step.assigned_to = request.user
                workflow_step.save()

                CalibrationAuditLog.objects.create(
                    user=request.user,
                    action='update_workflow_step',
                    description=f'Updated workflow step {workflow_step.step_name} to status {status}',
                    schedule=workflow_step.schedule,
                )

                messages.success(request, f'Workflow step {workflow_step.step_name} updated successfully.')
                return redirect('calibration:calibration_workflow_view', schedule_id=workflow_step.schedule.id)
            else:
                messages.error(request, 'Invalid status provided.')
    except Exception as e:
        messages.error(request, f'Error updating workflow step: {str(e)}')

    return redirect('calibration:calibration_workflow_view', schedule_id=workflow_step.schedule.id)