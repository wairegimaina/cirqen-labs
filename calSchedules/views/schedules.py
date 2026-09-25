"""calSchedules views — single-schedule actions (push/complete/edit/delete/schedule)."""
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from datetime import datetime, timedelta, date
import calendar
from dateutil.relativedelta import relativedelta
from workshop.models import Workshop
from ..models import CalibrationSchedule
from Inventory.models import Equipment, Department, EquipmentDescription
from openpyxl import Workbook
from CalSoft.models import CalibrationSession
from django.db import transaction
import logging
from django.db.models.functions import TruncMonth
from django.db.models import Q, Case, When, IntegerField, Count
logger = logging.getLogger(__name__)
from django.utils import timezone
from django.contrib.auth import get_user_model
import uuid
User = get_user_model()
from ..calibration_pdf_generator import create_calibration_pdf_response

# sibling modules in this package
from .helpers import get_user_access_context


@login_required
def mark_calibration_completed(request, schedule_id):
    """
    ✅ ENHANCED: Mark a calibration schedule as completed with certificate check
    """
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on calibration schedule {schedule_id}")
            messages.error(
                request,
                "You don't have permission to modify calibration schedules.",
                extra_tags="permission schedule"
            )
            return redirect('schedule:calibration_dashboard')

        filter_kwargs = {'id': schedule_id}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']

        try:
            schedule = get_object_or_404(CalibrationSchedule, **filter_kwargs)

            # ✅ NEW: Check if locked
            if hasattr(schedule, 'is_locked') and schedule.is_locked:
                messages.error(
                    request,
                    "This schedule is locked and cannot be modified.",
                    extra_tags="locked"
                )
                return redirect('schedule:calibration_dashboard')

            # ✅ Prevent editing completed schedules
            if schedule.status == 'completed':
                messages.warning(
                    request,
                    "This schedule is already completed and cannot be modified.",
                    extra_tags="schedule complete warning"
                )
                return redirect('schedule:calibration_dashboard')

            # A calibration is complete when its session has an approved
            # certificate. (This used to query fields CalibrationSession does
            # not have, so it always failed; and on success it moved the same
            # schedule forward a period instead of recording the completion.)
            session = CalibrationSession.objects.filter(
                schedule=schedule,
                certificate_number__isnull=False,
                status__in=("approved", "approved_pending_certificate"),
            ).exclude(certificate_number="").order_by("-timestamp").first()
            if session is None:
                messages.error(
                    request,
                    "Cannot complete this calibration yet: it needs an approved calibration "
                    "session with a certificate.",
                    extra_tags="certificate_required"
                )
                return redirect('schedule:calibration_dashboard')

            schedule.status = 'completed'
            schedule.completed_date = timezone.localdate(session.timestamp)
            schedule.save()  # the plan gives the device its next schedule
            name = schedule.equipment.description.name if schedule.equipment.description else 'N/A'
            messages.success(
                request,
                f"Calibration for {name} recorded as done ({session.certificate_number}).",
                extra_tags="completed"
            )
            logger.info(f"User {request.user.username} completed calibration schedule {schedule_id}")

        except Exception as e:
            logger.error(f"Error marking calibration schedule {schedule_id} as completed for user {request.user.username}: {e}")
            messages.error(
                request,
                f"Failed to mark calibration schedule as completed: {str(e)}",
                extra_tags="schedule complete error"
            )
    else:
        logger.warning(f"Invalid request method for mark_calibration_completed by user {request.user.username} on schedule {schedule_id}")
        messages.error(
            request,
            "Invalid request method.",
            extra_tags="validation method"
        )

    return redirect('schedule:calibration_dashboard')


@login_required
def push_calibration_schedule(request, schedule_id):
    """Push a calibration schedule forward by 1 month (works system-wide)"""
    if request.method == "POST":
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get("can_edit", False):
            logger.warning(f"Access denied for user {request.user.username} on calibration schedule {schedule_id}")
            messages.error(
                request,
                "You don't have permission to modify calibration schedules.",
                extra_tags="permission schedule",
            )
            return redirect("schedule:calibration_dashboard")

        # Restrict department-level users, global for others
        filter_kwargs = {"id": schedule_id}
        if access_context["access_type"] == "department":
            filter_kwargs["equipment__department_id"] = access_context["department_id"]

        try:
            schedule = get_object_or_404(CalibrationSchedule, **filter_kwargs)

            # ✅ NEW: Check if locked
            if hasattr(schedule, 'is_locked') and schedule.is_locked:
                messages.error(
                    request,
                    "This schedule is locked and cannot be modified.",
                    extra_tags="locked"
                )
                return redirect("schedule:calibration_dashboard")

            new_scheduled_month = schedule.scheduled_month + relativedelta(months=1)

            # Prevent duplicate schedule
            if CalibrationSchedule.objects.filter(
                equipment=schedule.equipment, scheduled_month=new_scheduled_month
            ).exists():
                messages.error(
                    request,
                    f"Cannot push calibration schedule for "
                    f"{schedule.equipment.description.name if schedule.equipment.description else 'N/A'} "
                    f"to {new_scheduled_month.strftime('%B %Y')} as it already exists.",
                    extra_tags="schedule conflict push",
                )
                return redirect("schedule:calibration_dashboard")

            schedule.scheduled_month = new_scheduled_month
            schedule.status = "pushed"
            schedule.save()

            messages.success(
                request,
                f"Calibration schedule for "
                f"{schedule.equipment.description.name if schedule.equipment.description else 'N/A'} "
                f"pushed to {new_scheduled_month.strftime('%B %Y')}.",
                extra_tags="schedule push success",
            )
        except Exception as e:
            logger.error(f"Error pushing calibration schedule {schedule_id} for user {request.user.username}: {e}")
            messages.error(
                request,
                f"Failed to push calibration schedule: {str(e)}",
                extra_tags="schedule push error",
            )
    else:
        logger.warning(f"Invalid request method for push_calibration_schedule by user {request.user.username}")
        messages.error(
            request,
            "Invalid request method.",
            extra_tags="validation method",
        )

    return redirect("schedule:calibration_dashboard")


@login_required
def edit_calibration_schedule(request, schedule_id):
    """Edit a calibration schedule - CANNOT edit completed schedules"""
    access_context = get_user_access_context(request)
    if not access_context or not access_context.get("can_edit", False):
        logger.warning(f"Access denied for user {request.user.username} on calibration schedule {schedule_id}")
        messages.error(
            request,
            "You don't have permission to edit calibration schedules.",
            extra_tags="permission schedule",
        )
        return redirect("schedule:calibration_dashboard")

    # Department-level restriction; global otherwise
    filter_kwargs = {"id": schedule_id}
    if access_context["access_type"] == "department":
        filter_kwargs["equipment__department_id"] = access_context["department_id"]

    schedule = get_object_or_404(CalibrationSchedule, **filter_kwargs)

    # ✅ NEW: Check if locked
    if hasattr(schedule, 'is_locked') and schedule.is_locked:
        messages.error(
            request,
            "This schedule is locked and cannot be modified.",
            extra_tags="locked"
        )
        return redirect("schedule:calibration_dashboard")

    # ✅ Prevent editing completed schedules
    if schedule.status == 'completed':
        messages.error(
            request,
            f"Cannot edit completed calibration schedules. "
            f"This schedule has already been processed.",
            extra_tags="schedule edit error"
        )
        return redirect("schedule:calibration_dashboard")

    if request.method == "POST":
        scheduled_month = request.POST.get("scheduled_month")
        status = request.POST.get("status")
        calibration_period = int(
            request.POST.get("calibration_period", schedule.calibration_period)
        )

        try:
            scheduled_month = datetime.strptime(scheduled_month, "%Y-%m").replace(day=1)

            # Prevent duplicate schedule
            if CalibrationSchedule.objects.filter(
                equipment=schedule.equipment, scheduled_month=scheduled_month
            ).exclude(id=schedule.id).exists():
                messages.error(
                    request,
                    f"Cannot update calibration schedule for "
                    f"{schedule.equipment.description.name if schedule.equipment.description else 'N/A'} "
                    f"as a schedule already exists for {scheduled_month.strftime('%B %Y')}.",
                    extra_tags="schedule conflict edit",
                )
                return redirect("schedule:calibration_dashboard")

            # Update fields
            schedule.scheduled_month = scheduled_month
            schedule.status = status
            schedule.calibration_period = calibration_period
            schedule.save()

            messages.success(
                request,
                f"Calibration schedule for "
                f"{schedule.equipment.description.name if schedule.equipment.description else 'N/A'} "
                f"updated successfully.",
                extra_tags="schedule edit success",
            )
        except ValueError:
            logger.error(f"Invalid date format for calibration schedule {schedule_id}: {scheduled_month}")
            messages.error(
                request,
                "Invalid date format.",
                extra_tags="validation date",
            )
        except Exception as e:
            logger.error(f"Error updating calibration schedule {schedule_id} for user {request.user.username}: {e}")
            messages.error(
                request,
                f"Failed to update calibration schedule: {str(e)}",
                extra_tags="schedule edit error",
            )
        return redirect("schedule:calibration_dashboard")

    return render(
        request,
        "Calibrition/calibration.html",
        {"schedule": schedule, "access_context": access_context},
    )


@login_required
def delete_calibration_schedule(request, schedule_id):
    """Delete a calibration schedule"""
    if request.method == "POST":
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get("can_edit", False):
            logger.warning(f"Access denied for user {request.user.username} on calibration schedule {schedule_id}")
            messages.error(
                request,
                "You don't have permission to delete calibration schedules.",
                extra_tags="permission schedule",
            )
            return redirect("schedule:calibration_dashboard")

        # Department-level restriction only
        filter_kwargs = {"id": schedule_id}
        if access_context["access_type"] == "department":
            filter_kwargs["equipment__department_id"] = access_context["department_id"]

        try:
            schedule = get_object_or_404(CalibrationSchedule, **filter_kwargs)

            # ✅ NEW: Check if locked
            if hasattr(schedule, 'is_locked') and schedule.is_locked:
                messages.error(
                    request,
                    "This schedule is locked and cannot be deleted.",
                    extra_tags="locked"
                )
                return redirect("schedule:calibration_dashboard")

            equipment_name = (
                schedule.equipment.description.name if schedule.equipment.description else "N/A"
            )

            # Check if schedule has pending_delete field (Boolean)
            if hasattr(schedule, 'pending_delete'):
                if getattr(schedule, 'status', None) in ['in_progress', 'completed']:
                    messages.error(
                        request,
                        f"Cannot delete calibration schedule with status: {getattr(schedule, 'status', 'N/A')}",
                        extra_tags="schedule delete error",
                    )
                else:
                    schedule.pending_delete = True
                    schedule.updated_at = timezone.now()
                    schedule.save(update_fields=['pending_delete', 'updated_at'])
                    messages.success(
                        request,
                        f"Calibration schedule for {equipment_name} marked for deletion.",
                        extra_tags="schedule delete pending success",
                    )
            # Check if schedule has status field that supports pending_delete state
            elif hasattr(schedule, 'status'):
                if schedule.status in ['in_progress', 'completed']:
                    messages.error(
                        request,
                        f"Cannot delete calibration schedule with status: {schedule.status}",
                        extra_tags="schedule delete error",
                    )
                elif schedule.status == 'pending_delete':
                    # Update updated_at before deletion
                    schedule.updated_at = timezone.now()
                    schedule.save(update_fields=['updated_at'])
                    schedule.delete()
                    messages.success(
                        request,
                        f"Calibration schedule for {equipment_name} deleted successfully.",
                        extra_tags="schedule delete success",
                    )
                else:
                    schedule.status = 'pending_delete'
                    schedule.updated_at = timezone.now()
                    schedule.save(update_fields=['status', 'updated_at'])
                    messages.success(
                        request,
                        f"Calibration schedule for {equipment_name} marked for deletion.",
                        extra_tags="schedule delete pending success",
                    )
            else:
                # No status or pending_delete field - update updated_at and delete immediately
                schedule.updated_at = timezone.now()
                schedule.save(update_fields=['updated_at'])
                schedule.delete()
                messages.success(
                    request,
                    f"Calibration schedule for {equipment_name} deleted successfully.",
                    extra_tags="schedule delete success",
                )

        except Exception as e:
            logger.error(f"Error deleting calibration schedule {schedule_id} for user {request.user.username}: {e}")
            messages.error(
                request,
                f"Failed to delete calibration schedule: {str(e)}",
                extra_tags="schedule delete error",
            )
    else:
        logger.warning(f"Invalid request method for delete_calibration_schedule by user {request.user.username} on schedule {schedule_id}")
        messages.error(
            request,
            "Invalid request method.",
            extra_tags="validation method",
        )

    return redirect("schedule:calibration_dashboard")


@login_required
def schedule_calibration_equipment(request, equipment_id):
    """Schedule individual equipment for calibration - WITH active_status filter"""
    if request.method == "POST":
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get("can_schedule", False):
            logger.warning(f"Access denied for user {request.user.username} on equipment {equipment_id}")
            messages.error(
                request,
                "You don't have permission to schedule equipment for calibration.",
                extra_tags="permission schedule",
            )
            return redirect("schedule:calibration_dashboard")

        # Department-level restriction only WITH active_status filter
        filter_kwargs = {
            "id": equipment_id,
            "active_status": True  # ✅ ADDED - Only schedule active equipment
        }
        if access_context["access_type"] == "department":
            filter_kwargs["department_id"] = access_context["department_id"]

        try:
            equipment = get_object_or_404(Equipment, **filter_kwargs)
            name = equipment.description.name if equipment.description else "N/A"

            # Completed history doesn't make a device scheduled; an open schedule does.
            if CalibrationSchedule.open_schedules().filter(equipment=equipment).exists():
                messages.error(
                    request,
                    f"Equipment {name} is already scheduled for calibration.",
                    extra_tags="schedule conflict",
                )
                return redirect("schedule:calibration_dashboard")

            # The workshop's scheduling plan decides the month.
            from scheduling.planner import schedule_by_hand
            done, problems = schedule_by_hand([equipment.id], "calibration")
            if done:
                messages.success(request, f"Equipment {name} scheduled: {done[0][1].message}.",
                                 extra_tags="schedule")
            else:
                reason = problems[0][1] if problems else "it could not be placed"
                messages.error(request, f"Equipment {name} cannot be scheduled: {reason}.",
                               extra_tags="schedule create error")
            return redirect("schedule:calibration_dashboard")

        except Exception as e:
            logger.error(f"Error scheduling equipment {equipment_id} for calibration for user {request.user.username}: {e}")
            messages.error(
                request,
                f"Failed to schedule equipment for calibration: {str(e)}",
                extra_tags="schedule create error",
            )
    else:
        logger.warning(f"Invalid request method for schedule_calibration_equipment by user {request.user.username}")
        messages.error(
            request,
            "Invalid request method.",
            extra_tags="validation method",
        )

    return redirect("schedule:calibration_dashboard")
