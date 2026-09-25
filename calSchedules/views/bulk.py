"""calSchedules views — bulk schedule actions."""
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
from ..tasks import initialize_calibration_schedule_with_logic
from openpyxl import Workbook
from CalSoft.models import CalibrationSession
from django.db import transaction
import logging
from django.db.models.functions import TruncMonth
from django.db.models import Q, Case, When, IntegerField, Count
logger = logging.getLogger(__name__)
from django.utils import timezone
from django.contrib.auth import get_user_model
from ..tasks import initialize_calibration_schedule_with_logic, auto_advance_completed_calibrations, normalize_existing_schedules, smart_reorganize_on_logic_change
import uuid
User = get_user_model()
from ..calibration_pdf_generator import create_calibration_pdf_response

# sibling modules in this package
from .helpers import get_user_access_context


@login_required
def bulk_mark_calibration_completed(request):
    """
    ✅ ENHANCED: Bulk mark calibration schedules as completed with certificate check
    """
    if request.method == 'POST':
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get('can_edit', False):
            logger.warning(f"Access denied for user {request.user.username} on bulk mark calibration completed")
            messages.error(
                request,
                "You don't have permission to modify calibration schedules.",
                extra_tags="permission schedule bulk"
            )
            return redirect('schedule:calibration_dashboard')

        schedule_ids = request.POST.getlist('schedule_ids')
        if not schedule_ids:
            logger.warning(f"No calibration schedule IDs provided for bulk mark completed by user {request.user.username}")
            messages.error(
                request,
                "No calibration schedules selected.",
                extra_tags="validation selection"
            )
            return redirect('schedule:calibration_dashboard')

        filter_kwargs = {'id__in': schedule_ids}
        if access_context['access_type'] == 'department':
            filter_kwargs['equipment__department_id'] = access_context['department_id']

        try:
            completed_count = 0
            no_certificate_count = 0
            locked_count = 0
            no_certificate_list = []

            with transaction.atomic():
                for schedule_id in schedule_ids:
                    try:
                        schedule = CalibrationSchedule.objects.get(id=schedule_id)

                        # ✅ NEW: Skip if locked
                        if hasattr(schedule, 'is_locked') and schedule.is_locked:
                            locked_count += 1
                            continue

                        # Skip if already completed
                        if schedule.status == 'completed':
                            continue

                        # ✅ NEW: CHECK FOR CERTIFICATE
                        session_exists = CalibrationSession.objects.filter(
                            equipment=schedule.equipment,
                            certificate__isnull=False,
                            is_approved=True
                        ).exists()

                        if not session_exists:
                            no_certificate_count += 1
                            no_certificate_list.append(
                                f"{schedule.equipment.description.name if schedule.equipment.description else 'N/A'} "
                                f"({schedule.equipment.serial_number})"
                            )
                            continue

                        # Complete it
                        schedule.status = 'completed'
                        schedule.completed_date = date.today()
                        schedule.save(update_fields=['status', 'completed_date'])

                        completed_count += 1

                    except CalibrationSchedule.DoesNotExist:
                        continue

            # ✅ NEW: Show results
            if completed_count > 0:
                messages.success(
                    request,
                    f"Successfully completed {completed_count} calibration(s).",
                    extra_tags="bulk_complete"
                )

            if no_certificate_count > 0:
                cert_list_str = ", ".join(no_certificate_list[:5])
                if len(no_certificate_list) > 5:
                    cert_list_str += f" and {len(no_certificate_list) - 5} more"

                messages.warning(
                    request,
                    f"Skipped {no_certificate_count} calibration(s) without certificates: {cert_list_str}. "
                    f"Please generate and approve certificates first.",
                    extra_tags="certificate_required"
                )

            if locked_count > 0:
                messages.warning(
                    request,
                    f"Skipped {locked_count} locked schedule(s).",
                    extra_tags="locked"
                )

            logger.info(
                f"Bulk complete: {completed_count} completed, {no_certificate_count} no cert, "
                f"{locked_count} locked by user {request.user.username}"
            )

        except Exception as e:
            logger.error(f"Error marking calibration schedules {schedule_ids} as completed for user {request.user.username}: {e}")
            messages.error(
                request,
                f"Failed to mark calibration schedules as completed: {str(e)}",
                extra_tags="schedule bulk complete error"
            )
    else:
        logger.warning(f"Invalid request method for bulk_mark_calibration_completed by user {request.user.username}")
        messages.error(
            request,
            "Invalid request method.",
            extra_tags="validation method"
        )

    return redirect('schedule:calibration_dashboard')


@login_required
def bulk_delete_calibration_schedules(request):
    """Bulk delete calibration schedules"""
    if request.method == "POST":
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get("can_edit", False):
            logger.warning(f"Access denied for user {request.user.username} on bulk delete calibration")
            messages.error(
                request,
                "You don't have permission to delete calibration schedules.",
                extra_tags="permission schedule bulk",
            )
            return redirect("schedule:calibration_dashboard")

        schedule_ids = request.POST.getlist("schedule_ids")
        if not schedule_ids:
            logger.warning(f"No calibration schedule IDs provided for bulk delete by user {request.user.username}")
            messages.error(
                request,
                "No calibration schedules selected.",
                extra_tags="validation selection",
            )
            return redirect("schedule:calibration_dashboard")

        # Department-level restriction only
        filter_kwargs = {"id__in": schedule_ids}
        if access_context["access_type"] == "department":
            filter_kwargs["equipment__department_id"] = access_context["department_id"]

        try:
            schedules = CalibrationSchedule.objects.filter(**filter_kwargs)
            count = schedules.count()
            if count == 0:
                logger.warning(f"No valid calibration schedules found for deletion with IDs {schedule_ids} by user {request.user.username}")
                messages.error(
                    request,
                    "No valid calibration schedules found for deletion.",
                    extra_tags="validation selection",
                )
                return redirect("schedule:calibration_dashboard")

            # Add pending_delete logic for bulk operations with updated_at
            current_time = timezone.now()

            # Check if model has pending_delete field (Boolean)
            if hasattr(CalibrationSchedule, 'pending_delete'):
                # Separate schedules by status and lock status
                locked_schedules = schedules.filter(is_locked=True)
                in_progress_schedules = schedules.filter(status__in=['in_progress', 'completed'])
                eligible_schedules = schedules.exclude(is_locked=True).exclude(status__in=['in_progress', 'completed'])

                # Mark eligible schedules as pending_delete = True and update updated_at
                eligible_count = eligible_schedules.count()
                eligible_schedules.update(pending_delete=True, updated_at=current_time)

                locked_count = locked_schedules.count()
                blocked_count = in_progress_schedules.count()

                if eligible_count > 0:
                    message = f"{eligible_count} calibration schedule(s) marked for deletion"
                    if locked_count > 0:
                        message += f". {locked_count} schedule(s) are locked and cannot be deleted"
                    if blocked_count > 0:
                        message += f". {blocked_count} schedule(s) could not be processed (in progress or completed)"

                    messages.success(
                        request,
                        message,
                        extra_tags="schedule bulk delete success",
                    )
                else:
                    messages.error(
                        request,
                        "No calibration schedules could be processed.",
                        extra_tags="validation selection",
                    )

            # Check if model has status field that supports pending_delete state
            elif hasattr(CalibrationSchedule, 'status'):
                # Separate schedules by status
                pending_delete_schedules = schedules.filter(status='pending_delete')
                in_progress_schedules = schedules.filter(status__in=['in_progress', 'completed'])
                other_schedules = schedules.exclude(status__in=['pending_delete', 'in_progress', 'completed'])

                # Update updated_at and delete schedules already marked as pending_delete
                pending_delete_count = pending_delete_schedules.count()
                if pending_delete_count > 0:
                    pending_delete_schedules.update(updated_at=current_time)
                    pending_delete_schedules.delete()

                # Mark other eligible schedules as pending_delete and update updated_at
                other_count = other_schedules.count()
                other_schedules.update(status='pending_delete', updated_at=current_time)

                # Count schedules that cannot be deleted
                blocked_count = in_progress_schedules.count()

                total_processed = pending_delete_count + other_count

                if total_processed > 0:
                    message = f"{total_processed} calibration schedule(s) processed successfully"
                    if pending_delete_count > 0 and other_count > 0:
                        message += f" ({pending_delete_count} deleted, {other_count} marked for deletion)"
                    elif pending_delete_count > 0:
                        message += f" ({pending_delete_count} deleted)"
                    elif other_count > 0:
                        message += f" ({other_count} marked for deletion)"

                    if blocked_count > 0:
                        message += f". {blocked_count} schedule(s) could not be processed (in progress or completed)"

                    messages.success(
                        request,
                        message,
                        extra_tags="schedule bulk delete success",
                    )
                else:
                    messages.error(
                        request,
                        "No calibration schedules could be processed.",
                        extra_tags="validation selection",
                    )
            else:
                # No status or pending_delete field - update updated_at and delete all immediately
                schedules.update(updated_at=current_time)
                schedules.delete()
                messages.success(
                    request,
                    f"{count} calibration schedule(s) deleted successfully.",
                    extra_tags="schedule bulk delete success",
                )

        except Exception as e:
            logger.error(f"Error deleting calibration schedules {schedule_ids} for user {request.user.username}: {e}")
            messages.error(
                request,
                f"Failed to delete calibration schedules: {str(e)}",
                extra_tags="schedule bulk delete error",
            )
    else:
        logger.warning(f"Invalid request method for bulk_delete_calibration_schedules by user {request.user.username}")
        messages.error(
            request,
            "Invalid request method.",
            extra_tags="validation method",
        )

    return redirect("schedule:calibration_dashboard")


@login_required
def bulk_push_calibration_schedules(request):
    """Bulk push calibration schedules forward by 1 month"""
    if request.method == "POST":
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get("can_edit", False):
            logger.warning(f"Access denied for user {request.user.username} on bulk push calibration")
            messages.error(
                request,
                "You don't have permission to modify calibration schedules.",
                extra_tags="permission schedule bulk",
            )
            return redirect("schedule:calibration_dashboard")

        schedule_ids = request.POST.getlist("schedule_ids")
        if not schedule_ids:
            logger.warning(f"No calibration schedule IDs provided for bulk push by user {request.user.username}")
            messages.error(
                request,
                "No calibration schedules selected.",
                extra_tags="validation selection",
            )
            return redirect("schedule:calibration_dashboard")

        # Department-level restriction only
        filter_kwargs = {"id__in": schedule_ids}
        if access_context["access_type"] == "department":
            filter_kwargs["equipment__department_id"] = access_context["department_id"]

        try:
            schedules = CalibrationSchedule.objects.filter(**filter_kwargs)
            count = 0
            locked_count = 0

            for schedule in schedules:
                # ✅ NEW: Skip locked schedules
                if hasattr(schedule, 'is_locked') and schedule.is_locked:
                    locked_count += 1
                    continue

                new_scheduled_month = schedule.scheduled_month + relativedelta(months=1)
                if not CalibrationSchedule.objects.filter(
                    equipment=schedule.equipment, scheduled_month=new_scheduled_month
                ).exists():
                    schedule.scheduled_month = new_scheduled_month
                    schedule.status = "pushed"
                    schedule.save()
                    count += 1

            if count == 0:
                if locked_count > 0:
                    messages.error(
                        request,
                        f"No calibration schedules were pushed. {locked_count} schedule(s) are locked.",
                        extra_tags="schedule bulk push conflict",
                    )
                else:
                    messages.error(
                        request,
                        "No calibration schedules were pushed.",
                        extra_tags="schedule bulk push conflict",
                    )
            else:
                message = f"{count} calibration schedule(s) pushed by 1 month."
                if locked_count > 0:
                    message += f" {locked_count} schedule(s) are locked and were not pushed."

                messages.success(
                    request,
                    message,
                    extra_tags="schedule bulk push success",
                )
        except Exception as e:
            logger.error(f"Error pushing calibration schedules {schedule_ids} for user {request.user.username}: {e}")
            messages.error(
                request,
                f"Failed to push calibration schedules: {str(e)}",
                extra_tags="schedule bulk push error",
            )
    else:
        logger.warning(f"Invalid request method for bulk_push_calibration_schedules by user {request.user.username}")
        messages.error(
            request,
            "Invalid request method.",
            extra_tags="validation method",
        )

    return redirect("schedule:calibration_dashboard")


@login_required
def bulk_schedule_unscheduled_calibration(request):
    """Bulk schedule unscheduled equipment for calibration - WITH active_status filter"""
    if request.method == "POST":
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get("can_schedule", False):
            logger.warning(f"Access denied for user {request.user.username} on bulk schedule calibration")
            messages.error(
                request,
                "You don't have permission to schedule equipment for calibration.",
                extra_tags="permission schedule bulk",
            )
            return redirect("schedule:calibration_dashboard")

        equipment_ids = request.POST.getlist("equipment_ids")
        if not equipment_ids:
            logger.warning(f"No equipment IDs provided for bulk calibration schedule by user {request.user.username}")
            messages.error(
                request,
                "No equipment selected.",
                extra_tags="validation selection",
            )
            return redirect("schedule:calibration_dashboard")

        planning_logic = request.POST.get("planning_logic", "department")
        calibration_period = int(request.POST.get("calibration_period", 12))
        base_month = int(request.POST.get("base_month", datetime.today().month))
        base_year = int(request.POST.get("base_year", datetime.today().year))
        max_departments = int(request.POST.get("max_departments", 100))
        max_descriptions = int(request.POST.get("max_descriptions", 100))

        # Restrict if department-level and convert UUIDs to strings
        # ✅ ADDED active_status=True filter
        if access_context["access_type"] == "department":
            equipment_ids = [
                str(eq_id) for eq_id in Equipment.objects.filter(
                    id__in=equipment_ids,
                    department_id=access_context["department_id"],
                    active_status=True  # ✅ ADDED - Only schedule active equipment
                ).values_list("id", flat=True)
            ]
        else:
            # Verify all equipment IDs are active before scheduling
            equipment_ids = [
                str(eq_id) for eq_id in Equipment.objects.filter(
                    id__in=equipment_ids,
                    active_status=True  # ✅ ADDED - Only schedule active equipment
                ).values_list("id", flat=True)
            ]

        # Check if any valid equipment IDs remain after filtering
        if not equipment_ids:
            messages.error(
                request,
                "No valid active equipment selected for scheduling.",
                extra_tags="validation selection",
            )
            return redirect("schedule:calibration_dashboard")

        # Planned workshops: the plan places them now.
        from scheduling.planner import schedule_by_hand
        done, problems, unplanned = schedule_by_hand(equipment_ids, "calibration")
        if done:
            messages.success(request, f"{len(done)} equipment scheduled by the workshop's plan.",
                             extra_tags="schedule bulk create")
        if problems:
            messages.warning(request, f"{len(problems)} equipment could not be placed by the plan; "
                                      "see Scheduling > Unscheduled for the reasons.",
                             extra_tags="schedule bulk create")
        equipment_ids = [str(i) for i in unplanned]
        if not equipment_ids:
            return redirect("schedule:calibration_dashboard")

        try:
            # Keywords: the task is not bound, and a leading None used to shift
            # every argument (planning_logic became None, calibration_period
            # 'department'). normalize_existing=False: scheduling a few devices
            # must not repack every other schedule.
            task = initialize_calibration_schedule_with_logic.delay(
                planning_logic=planning_logic,
                calibration_period=calibration_period,
                base_month=base_month,
                base_year=base_year,
                max_departments=max_departments,
                max_descriptions=max_descriptions,
                selected_descriptions=[],
                preserve_existing=True,
                specific_equipment_ids=equipment_ids,
                normalize_existing=False,
            )
            messages.info(
                request,
                f"Bulk calibration scheduling started (Task ID: {task.id}). Please check back later.",
                extra_tags="schedule bulk create task",
            )
        except Exception as e:
            logger.error(f"Failed to trigger bulk_schedule_unscheduled_calibration for user {request.user.username}: {e}")
            messages.error(
                request,
                f"Failed to schedule equipment for calibration: {str(e)}",
                extra_tags="schedule bulk create error",
            )
    else:
        logger.warning(f"Invalid request method for bulk_schedule_unscheduled_calibration by user {request.user.username}")
        messages.error(
            request,
            "Invalid request method.",
            extra_tags="validation method",
        )

    return redirect("schedule:calibration_dashboard")
