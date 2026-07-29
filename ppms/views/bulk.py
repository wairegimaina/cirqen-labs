"""ppms.views — bulk schedule actions."""
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
def bulk_delete_schedules(request):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on bulk delete")
            messages.error(request, "You don't have permission to delete schedules.")
            return redirect('ppm_dashboard')

        schedule_ids = request.POST.getlist('schedule_ids')
        if not schedule_ids:
            logger.warning(f"No schedule IDs provided for bulk delete by user {request.user.username}")
            messages.error(request, "No schedules selected.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id__in': schedule_ids}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedules = PPMSchedule.objects.filter(**filter_kwargs)
            count = schedules.count()
            if count == 0:
                logger.warning(f"No valid schedules found for deletion with IDs {schedule_ids} by user {request.user.username}")
                messages.error(request, "No valid schedules found for deletion.")
                return redirect('ppm_dashboard')

            current_time = timezone.now()

            if hasattr(PPMSchedule, 'pending_delete'):
                in_progress_schedules = schedules.filter(status__in=['in_progress', 'completed'])
                eligible_schedules = schedules.exclude(status__in=['in_progress', 'completed'])
                eligible_count = eligible_schedules.count()
                eligible_schedules.update(pending_delete=True, updated_at=current_time)
                blocked_count = in_progress_schedules.count()

                if eligible_count > 0:
                    message = f"{eligible_count} schedule(s) marked for deletion"
                    if blocked_count > 0:
                        message += f". {blocked_count} schedule(s) could not be processed (in progress or completed)"
                    messages.success(request, message)
                else:
                    messages.error(request, "No schedules could be processed.")

            elif hasattr(PPMSchedule, 'status'):
                pending_delete_schedules = schedules.filter(status='pending_delete')
                in_progress_schedules = schedules.filter(status__in=['in_progress', 'completed'])
                other_schedules = schedules.exclude(status__in=['pending_delete', 'in_progress', 'completed'])

                pending_delete_count = pending_delete_schedules.count()
                if pending_delete_count > 0:
                    pending_delete_schedules.update(updated_at=current_time)
                    pending_delete_schedules.delete()

                other_count = other_schedules.count()
                other_schedules.update(status='pending_delete', updated_at=current_time)
                blocked_count = in_progress_schedules.count()
                total_processed = pending_delete_count + other_count

                if total_processed > 0:
                    message = f"{total_processed} schedule(s) processed successfully"
                    if pending_delete_count > 0 and other_count > 0:
                        message += f" ({pending_delete_count} deleted, {other_count} marked for deletion)"
                    elif pending_delete_count > 0:
                        message += f" ({pending_delete_count} deleted)"
                    elif other_count > 0:
                        message += f" ({other_count} marked for deletion)"

                    if blocked_count > 0:
                        message += f". {blocked_count} schedule(s) could not be processed (in progress or completed)"

                    messages.success(request, message)
                else:
                    messages.error(request, "No schedules could be processed.")
            else:
                schedules.update(updated_at=current_time)
                schedules.delete()
                messages.success(request, f"{count} schedule(s) deleted successfully.")

        except Exception as e:
            logger.error(f"Error deleting schedules {schedule_ids} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to delete schedules: {str(e)}")
    else:
        logger.warning(f"Invalid request method for bulk_delete_schedules by user {request.user.username}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def bulk_mark_completed(request):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on bulk mark completed")
            messages.error(request, "You don't have permission to modify schedules.")
            return redirect('ppm_dashboard')

        schedule_ids = request.POST.getlist('schedule_ids')
        if not schedule_ids:
            logger.warning(f"No schedule IDs provided for bulk mark completed by user {request.user.username}")
            messages.error(request, "No schedules selected.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id__in': schedule_ids}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedules = PPMSchedule.objects.filter(**filter_kwargs)
            count = 0
            for schedule in schedules:
                schedule.status = 'completed'
                schedule.save()
                count += 1
            if count == 0:
                messages.error(request, "No schedules were marked as completed.")
            else:
                messages.success(request, f"{count} schedule(s) marked as completed.")
        except Exception as e:
            logger.error(f"Error marking schedules {schedule_ids} as completed for user {request.user.username}: {e}")
            messages.error(request, f"Failed to mark schedules as completed: {str(e)}")
    else:
        logger.warning(f"Invalid request method for bulk_mark_completed by user {request.user.username}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def bulk_push_schedules(request):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on bulk push")
            messages.error(request, "You don't have permission to modify schedules.")
            return redirect('ppm_dashboard')

        schedule_ids = request.POST.getlist('schedule_ids')
        if not schedule_ids:
            logger.warning(f"No schedule IDs provided for bulk push by user {request.user.username}")
            messages.error(request, "No schedules selected.")
            return redirect('ppm_dashboard')

        filter_kwargs = {'id__in': schedule_ids}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']
        else:
            filter_kwargs['workshop_id'] = access_context['workshop_id']

        try:
            schedules = PPMSchedule.objects.filter(**filter_kwargs)
            count = 0
            for schedule in schedules:
                new_scheduled_month = schedule.scheduled_month + relativedelta(months=1)
                if not PPMSchedule.objects.filter(equipment=schedule.equipment, scheduled_month=new_scheduled_month).exists():
                    schedule.scheduled_month = new_scheduled_month
                    schedule.status = 'pushed'
                    schedule.save()
                    count += 1
            if count == 0:
                messages.error(request, "No schedules were pushed.")
            else:
                messages.success(request, f"{count} schedule(s) pushed by 1 month.")
        except Exception as e:
            logger.error(f"Error pushing schedules {schedule_ids} for user {request.user.username}: {e}")
            messages.error(request, f"Failed to push schedules: {str(e)}")
    else:
        logger.warning(f"Invalid request method for bulk_push_schedules by user {request.user.username}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')


@login_required
def bulk_schedule_unscheduled(request):
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_schedule', False):
            logger.warning(f"Access denied for user {request.user.username} on bulk schedule")
            messages.error(request, "You don't have permission to schedule equipment.")
            return redirect('ppm_dashboard')

        equipment_ids = request.POST.getlist('equipment_ids')
        if not equipment_ids:
            logger.warning(f"No equipment IDs provided for bulk schedule by user {request.user.username}")
            messages.error(request, "No equipment selected.")
            return redirect('ppm_dashboard')

        planning_logic = request.POST.get('planning_logic', 'department')
        maintenance_period = int(request.POST.get('maintenance_period', 6))
        base_month = int(request.POST.get('base_month', datetime.today().month))
        base_year = int(request.POST.get('base_year', datetime.today().year))
        max_departments = int(request.POST.get('max_departments', 20))
        max_descriptions = int(request.POST.get('max_descriptions', 20))

        if access_context['access_type'] == 'department':
            valid_equipment_ids = Equipment.objects.filter(
                id__in=equipment_ids,
                department_id=access_context['department_id'],
                active_status=True
            ).values_list('id', flat=True)
            equipment_ids = list(valid_equipment_ids)

        try:
            task = initialize_ppm_schedule_with_logic.delay(
                str(access_context['workshop_id']),
                planning_logic,
                maintenance_period,
                base_month,
                base_year,
                max_departments,
                max_descriptions,
                [],
                False,
                equipment_ids
            )
            messages.info(request, f"Bulk scheduling started (Task ID: {task.id}). Please check back later.")
        except Exception as e:
            logger.error(f"Failed to trigger bulk_schedule_unscheduled for user {request.user.username}: {e}")
            messages.error(request, f"Failed to schedule equipment: {str(e)}")
    else:
        logger.warning(f"Invalid request method for bulk_schedule_unscheduled by user {request.user.username}")
        messages.error(request, "Invalid request method.")
    return redirect('ppm_dashboard')
