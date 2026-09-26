"""ppms.views — single-schedule actions (push/complete/edit/delete/schedule)."""
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
def push_schedule(request, schedule_id):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on schedule {schedule_id}")
            messages.error(request, "You don't have permission to modify schedules.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id': schedule_id}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedule = get_object_or_404(PPMSchedule, **filter_kwargs)
            new_scheduled_month = schedule.scheduled_month + relativedelta(months=1)
            if PPMSchedule.objects.filter(equipment=schedule.equipment, scheduled_month=new_scheduled_month).exists():
                messages.error(request, f"Cannot push schedule for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} to {new_scheduled_month.strftime('%B %Y')} as it already exists.")
                return redirect('ppm_dashboard')
            schedule.scheduled_month = new_scheduled_month
            schedule.status = 'pushed'
            schedule.save()
            messages.success(request, f"Schedule for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} pushed to {new_scheduled_month.strftime('%B %Y')}.")
        except Exception as e:
            logger.error(f"Error pushing schedule {schedule_id} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to push schedule: {str(e)}")
    else:
        logger.warning(f"Invalid request method for push_schedule by user {request.user.username} on schedule {schedule_id}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def mark_completed(request, schedule_id):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on schedule {schedule_id}")
            messages.error(request, "You don't have permission to modify schedules.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id': schedule_id}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedule = get_object_or_404(PPMSchedule, **filter_kwargs)
            schedule.status = 'completed'
            schedule.save()
            messages.success(request, f"Schedule for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} marked as completed.")
        except Exception as e:
            logger.error(f"Error marking schedule {schedule_id} as completed for user {request.user.username}: {e}")
            messages.error(request, f"Failed to mark schedule as completed: {str(e)}")
    else:
        logger.warning(f"Invalid request method for mark_completed by user {request.user.username} on schedule {schedule_id}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def edit_schedule(request, schedule_id):
    access_context = get_user_access_context(request)
    if not access_context or not access_context.get('can_edit', False):
        logger.warning(f"Access denied for user {request.user.username} on schedule {schedule_id}")
        messages.error(request, "You don't have permission to edit schedules.")
        return redirect('ppm_dashboard')

    filter_kwargs = {'id': schedule_id}
    if access_context['access_type'] == 'department':
        filter_kwargs['equipment__department_id'] = access_context['department_id']
    else:
        filter_kwargs['workshop_id'] = access_context['workshop_id']

    schedule = get_object_or_404(PPMSchedule, **filter_kwargs)

    if request.method == 'POST':
        scheduled_month = request.POST.get('scheduled_month')
        status = request.POST.get('status')
        maintenance_period = int(request.POST.get('maintenance_period', schedule.maintenance_period))

        try:
            scheduled_month = datetime.strptime(scheduled_month, '%Y-%m').replace(day=1)
            if PPMSchedule.objects.filter(equipment=schedule.equipment, scheduled_month=scheduled_month).exclude(id=schedule.id).exists():
                messages.error(request, f"Cannot update schedule for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} as a schedule already exists for {scheduled_month.strftime('%B %Y')}.")
                return redirect('ppm_dashboard')
            schedule.scheduled_month = scheduled_month
            schedule.status = status
            schedule.maintenance_period = maintenance_period
            schedule.save()
            messages.success(request, f"Schedule for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} updated successfully.")
        except ValueError:
            logger.error(f"Invalid date format for schedule {schedule_id}: {scheduled_month}")
            messages.error(request, "Invalid date format.")
        except Exception as e:
            logger.error(f"Error updating schedule {schedule_id} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to update schedule: {str(e)}")
        return redirect('ppm_dashboard')
    return render(request, 'PPM/ppm.html', {
        'schedule': schedule,
        'access_context': access_context,
        'show_sidebar': True,  # Added sidebar context
    })


@login_required
def delete_schedule(request, schedule_id):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on schedule {schedule_id}")
            messages.error(request, "You don't have permission to delete schedules.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id': schedule_id}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedule = get_object_or_404(PPMSchedule, **filter_kwargs)
            equipment_name = schedule.equipment.description.name if schedule.equipment.description else 'N/A'

            if hasattr(schedule, 'pending_delete'):
                if getattr(schedule, 'status', None) in ['in_progress', 'completed']:
                    messages.error(request, f"Cannot delete schedule with status: {getattr(schedule, 'status', 'N/A')}")
                else:
                    schedule.pending_delete = True
                    schedule.updated_at = timezone.now()
                    schedule.save(update_fields=['pending_delete', 'updated_at'])
                    messages.success(request, f"Schedule for {equipment_name} marked for deletion.")
            elif hasattr(schedule, 'status'):
                if schedule.status in ['in_progress', 'completed']:
                    messages.error(request, f"Cannot delete schedule with status: {schedule.status}")
                elif schedule.status == 'pending_delete':
                    schedule.updated_at = timezone.now()
                    schedule.save(update_fields=['updated_at'])
                    schedule.delete()
                    messages.success(request, f"Schedule for {equipment_name} deleted successfully.")
                else:
                    schedule.status = 'pending_delete'
                    schedule.updated_at = timezone.now()
                    schedule.save(update_fields=['status', 'updated_at'])
                    messages.success(request, f"Schedule for {equipment_name} marked for deletion.")
            else:
                schedule.updated_at = timezone.now()
                schedule.save(update_fields=['updated_at'])
                schedule.delete()
                messages.success(request, f"Schedule for {equipment_name} deleted successfully.")

        except Exception as e:
            logger.error(f"Error deleting schedule {schedule_id} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to delete schedule: {str(e)}")
    else:
        logger.warning(f"Invalid request method for delete_schedule by user {request.user.username} on schedule {schedule_id}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def schedule_equipment(request, equipment_id):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_schedule', False):
            logger.warning(f"Access denied for user {request.user.username} on equipment {equipment_id}")
            messages.error(request, "You don't have permission to schedule equipment.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id': equipment_id}
        if access_context['access_type'] == 'department':
            filter_kwargs['department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            filter_kwargs['active_status'] = True
            equipment = get_object_or_404(Equipment, **filter_kwargs)
            workshop = get_object_or_404(Workshop, id=access_context['workshop_id'])
            name = equipment.description.name if equipment.description else 'N/A'

            # Completed history doesn't make a device scheduled; an open schedule does.
            if PPMSchedule.open_schedules().filter(equipment=equipment).exists():
                messages.error(request, f"Equipment {name} is already scheduled.")
                return redirect('ppm_dashboard')

            # The workshop's scheduling plan decides the month.
            from scheduling.planner import schedule_by_hand
            done, problems = schedule_by_hand([equipment.id], 'ppm')
            if done:
                messages.success(request, f"Equipment {name} scheduled: {done[0][1].message}.")
            else:
                reason = problems[0][1] if problems else "it could not be placed"
                messages.error(request, f"Equipment {name} cannot be scheduled: {reason}.")
            return redirect('ppm_dashboard')
        except Exception as e:
            logger.error(f"Error scheduling equipment {equipment_id} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to schedule equipment: {str(e)}")
    else:
        logger.warning(f"Invalid request method for schedule_equipment by user {request.user.username} on equipment {equipment_id}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')
