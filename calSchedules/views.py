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
from .models import CalibrationSchedule
from Inventory.models import Equipment, Department, EquipmentDescription
from .tasks import initialize_calibration_schedule_with_logic
from openpyxl import Workbook
from CalSoft.models import  CalibrationSession
from django.db import transaction
import logging
from django.db.models.functions import TruncMonth
from django.db.models import Q, Case, When, IntegerField, Count
logger = logging.getLogger(__name__)
from django.utils import timezone
from django.contrib.auth import get_user_model
from .tasks import (
    initialize_calibration_schedule_with_logic,
    auto_advance_completed_calibrations,
    normalize_existing_schedules,
    smart_reorganize_on_logic_change,
)
import uuid
User = get_user_model()
from .calibration_pdf_generator import create_calibration_pdf_response

def get_current_month_year():
    """
    Returns current month and year as integers
    """
    today = datetime.today()
    return today.month, today.year

# ✅ NEW: Overdue schedules checker
def check_overdue_schedules(schedules_queryset):
    """
    Check for overdue schedules
    """
    today = date.today()
    current_month = today.replace(day=1)

    # Overdue: scheduled month has passed
    overdue = schedules_queryset.filter(
        scheduled_month__lt=current_month,
        status__in=['pending', 'pushed']
    )

    # Warning: due this month
    warning = schedules_queryset.filter(
        scheduled_month=current_month,
        status__in=['pending', 'pushed']
    )

    return {
        'overdue_count': overdue.count(),
        'overdue_schedules': overdue,
        'warning_count': warning.count(),
        'warning_schedules': warning
    }


# ✅ NEW: Group waiting status checker
def check_group_waiting_status(schedule):
    """
    Check if schedule is waiting for group completion
    """
    if schedule.status != 'completed':
        return {'is_waiting': False}

    try:
        from .instant_reconciliation import check_group_completion_status

        planning_logic = schedule.planning_logic or 'department'

        status = check_group_completion_status(
            schedule.equipment,
            schedule.scheduled_month,
            planning_logic
        )

        # Get pending equipment details
        pending_equipment = []
        if not status['all_completed']:
            pending_members = status['members'].filter(
                status__in=['pending', 'pushed', 'in_progress']
            ).select_related('equipment__description')

            for member in pending_members:
                pending_equipment.append({
                    'id': member.equipment.id,
                    'description': member.equipment.description.name if member.equipment.description else 'N/A',
                    'serial_number': member.equipment.serial_number,
                    'status': member.status
                })

        return {
            'is_waiting': not status['all_completed'],
            'completed_count': status['completed'],
            'total_count': status['total'],
            'group_name': status['group_name'],
            'pending_equipment': pending_equipment
        }
    except ImportError:
        return {'is_waiting': False}
    except Exception as e:
        logger.error(f"Error checking group waiting status: {e}")
        return {'is_waiting': False}


def _get_logic_change_context(schedules_queryset):
    """
    Return template context variables for the logic-change warning banner.

    Computes:
      - current_planning_logic  – the dominant planning_logic in use
      - logic_change_warnings_count – number of schedules with a non-empty
                                      logic_change_warning field
      - logic_change_sample     – up to 5 of those schedules, each as a
                                  dict with keys: equipment_name,
                                  scheduled_month, warning_snippet

    Safe to call with any CalibrationSchedule queryset (filtered or not).
    Always returns a dict — never raises.
    """
    try:
        warned_qs = schedules_queryset.exclude(logic_change_warning='')
        count = warned_qs.count()

        # Determine the dominant current planning logic across ALL schedules
        # (not just warned ones, so we reflect the current state of the system).
        from django.db.models import Count as _Count
        logic_row = (
            schedules_queryset
            .exclude(planning_logic='')
            .values('planning_logic')
            .annotate(n=_Count('id'))
            .order_by('-n')
            .first()
        )
        current_logic = logic_row['planning_logic'] if logic_row else ''

        sample = []
        for sched in warned_qs.select_related('equipment__description')[:5]:
            warning_text = sched.logic_change_warning or ''
            # Produce a short snippet – first 80 chars of the warning
            snippet = warning_text[:80] + ('…' if len(warning_text) > 80 else '')
            sample.append({
                'equipment_name': (
                    sched.equipment.description.name
                    if sched.equipment and sched.equipment.description
                    else 'N/A'
                ),
                'scheduled_month': (
                    sched.scheduled_month.strftime('%B %Y')
                    if sched.scheduled_month else 'N/A'
                ),
                'warning_snippet': snippet,
            })

        return {
            'current_planning_logic': current_logic,
            'logic_change_warnings_count': count,
            'logic_change_sample': sample,
        }
    except Exception as exc:
        logger.error(f"[_get_logic_change_context] Error computing logic-change context: {exc}")
        return {
            'current_planning_logic': '',
            'logic_change_warnings_count': 0,
            'logic_change_sample': [],
        }


@login_required
def export_calibration_pdf(request):
    """
    Export Calibration schedules to PDF based on user access
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"Access denied for user {request.user.username} on export Calibration PDF")
        messages.error(request, "Access denied.")
        return redirect('schedule:calibration_dashboard')

    # Get filter parameters
    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')

    try:
        # Get schedules based on user access
        if access_context['access_type'] == 'department':
            schedules = CalibrationSchedule.objects.select_related(
                'equipment__department',
                'equipment__description'
            ).filter(
                equipment__department_id=access_context['department_id'],
                equipment__active_status=True
            ).order_by('scheduled_month', 'equipment__description__name')

            department = access_context['department']
            workshop = None

        else:
            schedules = CalibrationSchedule.objects.select_related(
                'equipment__department',
                'equipment__description'
            ).filter(
                equipment__active_status=True
            ).order_by('equipment__department__name', 'scheduled_month', 'equipment__description__name')

            # Check if filtering by specific department
            selected_department_id = request.GET.get('department')
            if selected_department_id:
                schedules = schedules.filter(equipment__department_id=selected_department_id)
                department = Department.objects.get(id=selected_department_id)
                workshop = None
            else:
                department = None
                workshop = access_context.get('workshop')

        # Apply month and year filters
        if month_filter:
            schedules = schedules.filter(scheduled_month__month=int(month_filter))
        if year_filter:
            schedules = schedules.filter(scheduled_month__year=int(year_filter))

        # Generate and return PDF
        return create_calibration_pdf_response(schedules, department, workshop)

    except Exception as e:
        logger.error(f"Error generating Calibration PDF for user {request.user.username}: {str(e)}", exc_info=True)
        messages.error(request, "Failed to generate PDF. Please try again.")
        return redirect('schedule:calibration_dashboard')

@login_required
def trigger_auto_advance_calibrations(request):
    """
    ✅ NEW: Manually trigger auto-advance of completed calibrations
    """
    if request.method == "POST":
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get("can_schedule", False):
            logger.warning(f"Access denied for user {request.user.username} on auto-advance calibrations")
            messages.error(
                request,
                "You don't have permission to advance calibration schedules.",
                extra_tags="permission schedule"
            )
            return redirect("schedule:calibration_dashboard")

        try:
            # Queue the auto-advance task
            task = auto_advance_completed_calibrations.delay()

            messages.success(
                request,
                f"Auto-advance task started (Task ID: {task.id}). "
                f"Completed calibrations will be moved to their next scheduled period.",
                extra_tags="schedule auto-advance task"
            )
            logger.info(f"Auto-advance task {task.id} queued by user {request.user.username}")

        except Exception as e:
            logger.error(f"Failed to trigger auto-advance for user {request.user.username}: {e}")
            messages.error(
                request,
                f"Failed to start auto-advance: {str(e)}",
                extra_tags="schedule auto-advance error"
            )

    return redirect("schedule:calibration_dashboard")


@login_required
def trigger_normalize_schedules(request):
    """
    ✅ SIMPLIFIED: Trigger normalization for CURRENT YEAR (automatic)

    No year selection needed - automatically normalizes the active planning year:
    - If current month <= June: Normalizes current year
    - If current month > June: Normalizes next year

    Signal-created schedules are automatically protected.
    """
    request_id = str(uuid.uuid4())[:8]
    logger.info(f"[{request_id}] Normalize request from user: {request.user.username}")

    if request.method == "POST":
        # Check permissions
        access_context = get_user_access_context(request)
        if not access_context or not access_context.get("can_schedule", False):
            logger.warning(f"[{request_id}] Access denied for user {request.user.username}")
            messages.error(
                request,
                "You don't have permission to normalize calibration schedules.",
                extra_tags="permission schedule"
            )
            return redirect("schedule:calibration_dashboard")

        # Get normalization parameters from form
        try:
            planning_logic = request.POST.get("planning_logic", "description_based")
            calibration_period = int(request.POST.get("calibration_period", 12))
            base_month = int(request.POST.get("base_month", 1))
            max_departments = int(request.POST.get("max_departments", 100))
            max_descriptions = int(request.POST.get("max_descriptions", 100))

            special_class_departments = request.POST.getlist("special_class_departments")
            special_class_descriptions = request.POST.getlist("special_class_descriptions")

            # ✅ Determine which year will be normalized (for display only)
            today = date.today()
            if today.month > 6:
                normalized_year = today.year + 1
            else:
                normalized_year = today.year

            logger.info(
                f"[{request_id}] Normalization params: logic={planning_logic}, "
                f"period={calibration_period}, start_month={base_month}, "
                f"year={normalized_year} (auto-selected)"
            )

        except (ValueError, TypeError) as e:
            logger.error(f"[{request_id}] Invalid form data: {str(e)}")
            messages.error(
                request,
                f"Invalid input data: {str(e)}",
                extra_tags="validation"
            )
            return redirect("schedule:calibration_dashboard")

        # Queue the normalization task
        try:
            logger.info(f"[{request_id}] Queuing normalization task to Celery...")

            # ✅ NOTE: No year parameter needed - it's automatic!
            task = normalize_existing_schedules.delay(
                planning_logic=planning_logic,
                calibration_period=calibration_period,
                base_month=base_month,
                max_departments=max_departments,
                max_descriptions=max_descriptions,
                special_class_departments=special_class_departments,
                special_class_descriptions=special_class_descriptions
            )

            logger.info(f"[{request_id}] ✅ Task queued successfully: {task.id}")

            messages.success(
                request,
                f"Normalization started for year {normalized_year} (automatically selected)! "
                f"Task ID: {task.id}. "
                f"Signal-created schedules will be protected to maintain calibration intervals.",
                extra_tags="schedule normalization task"
            )

        except Exception as task_error:
            logger.error(f"[{request_id}] Task queuing failed: {str(task_error)}", exc_info=True)
            messages.error(
                request,
                f"Failed to start normalization: {str(task_error)}. "
                f"Please ensure Celery worker is running.",
                extra_tags="schedule normalization error"
            )

    return redirect("schedule:calibration_dashboard")


@login_required
def trigger_smart_reorganize(request):
    """
    Handle the Smart Reorganizer form submission.

    Accepts POST with:
      - new_planning_logic  : 'date_based' or 'description_based'
      - base_month          : int 1-12 (default 1)
      - max_departments     : int (default 20)
      - max_descriptions    : int (default 20)
      - dry_run             : 'true' | absent

    Queues smart_reorganize_on_logic_change as a Celery task and redirects
    back to the dashboard with a flash message.
    """
    request_id = str(uuid.uuid4())[:8]
    logger.info(f"[{request_id}] Smart-reorganize request from user: {request.user.username}")

    if request.method != "POST":
        logger.info(f"[{request_id}] GET request — redirecting to dashboard")
        return redirect("schedule:calibration_dashboard")

    # ── Permission check ──────────────────────────────────────────────────────
    access_context = get_user_access_context(request)
    if not access_context or not access_context.get("can_schedule", False):
        logger.warning(f"[{request_id}] Access denied for user {request.user.username}")
        messages.error(
            request,
            "You don't have permission to reorganize calibration schedules.",
            extra_tags="permission schedule",
        )
        return redirect("schedule:calibration_dashboard")

    # ── Parse form data ───────────────────────────────────────────────────────
    try:
        new_planning_logic = request.POST.get("new_planning_logic", "date_based")
        base_month = int(request.POST.get("base_month", 1))
        max_departments = int(request.POST.get("max_departments", 20))
        max_descriptions = int(request.POST.get("max_descriptions", 20))
        dry_run = request.POST.get("dry_run", "").lower() == "true"

        # Clamp to sane ranges
        base_month = max(1, min(12, base_month))
        max_departments = max(1, min(100, max_departments))
        max_descriptions = max(1, min(100, max_descriptions))

        logger.info(
            f"[{request_id}] Smart-reorg params: logic={new_planning_logic}, "
            f"base_month={base_month}, max_depts={max_departments}, "
            f"max_descs={max_descriptions}, dry_run={dry_run}"
        )
    except (ValueError, TypeError) as exc:
        logger.error(f"[{request_id}] Invalid form data: {exc}")
        messages.error(
            request,
            f"Invalid input data: {exc}",
            extra_tags="validation",
        )
        return redirect("schedule:calibration_dashboard")

    # ── Queue Celery task ─────────────────────────────────────────────────────
    try:
        task = smart_reorganize_on_logic_change.delay(
            new_planning_logic=new_planning_logic,
            base_month=base_month,
            max_departments=max_departments,
            max_descriptions=max_descriptions,
            dry_run=dry_run,
        )

        logger.info(f"[{request_id}] ✅ Smart-reorg task queued: {task.id}")

        if dry_run:
            messages.info(
                request,
                f"Smart Reorganizer dry run started (Task ID: {task.id}). "
                f"Check the server logs to preview what would change — no schedules were modified.",
                extra_tags="schedule smart-reorg dry-run task",
            )
        else:
            messages.success(
                request,
                f"Smart Reorganizer started (Task ID: {task.id}). "
                f"Schedules are being realigned to '{new_planning_logic}' logic. "
                f"Protected schedules (completed, locked, signal-created) will not be changed.",
                extra_tags="schedule smart-reorg task",
            )

    except Exception as exc:
        logger.error(f"[{request_id}] Task queuing failed: {exc}", exc_info=True)
        messages.error(
            request,
            f"Failed to start Smart Reorganizer: {exc}. "
            f"Please ensure the Celery worker is running.",
            extra_tags="schedule smart-reorg error",
        )

    return redirect("schedule:calibration_dashboard")


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

            old_status = schedule.status
            old_month = schedule.scheduled_month

            # ✅ NEW: CHECK FOR CERTIFICATE
            session_exists = CalibrationSession.objects.filter(
                equipment=schedule.equipment,
                certificate__isnull=False,  # Must have certificate
                is_approved=True  # Must be approved
            ).exists()

            if not session_exists:
                messages.error(
                    request,
                    f"Cannot complete this calibration. Certificate must be generated and approved first. "
                    f"Please generate the certificate in the workshop system.",
                    extra_tags="certificate_required"
                )
                return redirect('schedule:calibration_dashboard')

            # Get calibration period (default to 12 months if not set)
            period = schedule.calibration_period or 12

            # Calculate next scheduled month
            next_month = schedule.scheduled_month + relativedelta(months=period)

            # Check if next schedule already exists
            existing = CalibrationSchedule.objects.filter(
                equipment=schedule.equipment,
                scheduled_month=next_month
            ).exists()

            if not existing:
                # Auto-advance to next period
                schedule.scheduled_month = next_month
                schedule.status = 'pending'
                schedule.save(update_fields=['scheduled_month', 'status'])

                # ✅ NEW: Check group status
                waiting_status = check_group_waiting_status(schedule)

                if waiting_status['is_waiting']:
                    messages.success(
                        request,
                        f"Calibration for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} "
                        f"completed! Waiting for {waiting_status['total_count'] - waiting_status['completed_count']} "
                        f"other equipment in {waiting_status['group_name']} to complete before rescheduling.",
                        extra_tags="waiting_reschedule"
                    )
                else:
                    messages.success(
                        request,
                        f"Calibration for {schedule.equipment.description.name if schedule.equipment.description else 'N/A'} "
                        f"completed! All equipment in group completed! Group will be rescheduled automatically.",
                        extra_tags="completed"
                    )

                logger.info(
                    f"User {request.user.username} completed and advanced schedule {schedule_id} "
                    f"from {old_month.strftime('%B %Y')} to {next_month.strftime('%B %Y')}"
                )
            else:
                # Just mark as completed if next schedule exists
                schedule.status = 'completed'
                schedule.save(update_fields=['status'])

                messages.warning(
                    request,
                    f"Calibration marked completed, but schedule for {next_month.strftime('%B %Y')} already exists. "
                    f"Manual adjustment may be needed.",
                    extra_tags="schedule complete warning"
                )
                logger.warning(
                    f"Could not advance schedule {schedule_id} - "
                    f"schedule for {next_month.strftime('%B %Y')} already exists"
                )

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
def export_department_calibration_pdf(request, dept_id):
    """
    Export Calibration schedules for a specific department to PDF
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"Access denied for user {request.user.username} on export department Calibration PDF")
        messages.error(request, "Access denied.")
        return redirect('schedule:calibration_dashboard')

    # Verify access to department
    if access_context['access_type'] == 'department':
        if str(access_context['department_id']) != str(dept_id):
            messages.error(request, "You don't have access to this department.")
            return redirect('schedule:calibration_dashboard')

    # Get filter parameters
    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')

    try:
        department = get_object_or_404(Department, id=dept_id)

        # Get schedules for this department
        schedules = CalibrationSchedule.objects.select_related(
            'equipment__department',
            'equipment__description'
        ).filter(
            equipment__department_id=dept_id,
            equipment__active_status=True
        ).order_by('scheduled_month', 'equipment__description__name')

        # Apply month and year filters
        if month_filter:
            schedules = schedules.filter(scheduled_month__month=int(month_filter))
        if year_filter:
            schedules = schedules.filter(scheduled_month__year=int(year_filter))

        # Generate and return PDF
        return create_calibration_pdf_response(schedules, department, None)

    except Exception as e:
        logger.error(f"Error generating department Calibration PDF for user {request.user.username}: {str(e)}", exc_info=True)
        messages.error(request, "Failed to generate PDF. Please try again.")
        return redirect('schedule:calibration_dashboard')


@login_required
def calibration_dashboard(request):
    """
    Main calibration dashboard view with enhanced search, overdue warnings, and group status
    ✅ ENHANCED: Added overdue checking and group waiting status
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"No access context for user {request.user.username}")
        messages.error(
            request,
            "No workshop or department access found. Please contact administrator.",
            extra_tags="access permission"
        )
        return redirect('custom_login')

    # Store session info
    if access_context.get("workshop_id"):
        request.session["workshop_id"] = str(access_context["workshop_id"])
    if access_context.get("department_id"):
        request.session["department_id"] = str(access_context["department_id"])
    request.session.modified = True

    # ✅ Get filter parameters (no defaults - only apply if explicitly provided)
    current_month, current_year = get_current_month_year()
    month_filter = request.GET.get('month')  # Don't default
    year_filter = request.GET.get('year')    # Don't default
    search_query = request.GET.get('search', '').strip()  # ✅ NEW: Search parameter

    # Base querysets based on access level
    if access_context["access_type"] == "department":
        departments = Department.objects.filter(id=access_context["department_id"])
        schedules_list = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).filter(
            equipment__department_id=access_context["department_id"],
            equipment__active_status=True
        )

        # FIX #1: Only exclude equipment that has an ACTIVE (non-completed) schedule.
        # Equipment whose only schedules are completed should appear as unscheduled
        # so operators know to create/auto-advance the next schedule.
        scheduled_equipment_ids = CalibrationSchedule.objects.filter(
            equipment__department_id=access_context["department_id"],
            equipment__active_status=True,
            status__in=["pending", "pushed", "in_progress", "overdue"],  # ← KEY FIX
        ).values_list("equipment_id", flat=True)

        unscheduled_equipment_list = Equipment.objects.filter(
            department_id=access_context["department_id"],
            active_status=True
        ).exclude(id__in=scheduled_equipment_ids).select_related("department", "description")

        equipment_descriptions = EquipmentDescription.objects.filter(
            equipment__department_id=access_context["department_id"],
            equipment__active_status=True
        ).distinct()
    else:
        departments = Department.objects.all()
        schedules_list = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).filter(
            equipment__active_status=True
        )

        # FIX #1: Only exclude equipment that has an ACTIVE (non-completed) schedule.
        scheduled_equipment_ids = CalibrationSchedule.objects.filter(
            equipment__active_status=True,
            status__in=["pending", "pushed", "in_progress", "overdue"],  # ← KEY FIX
        ).values_list("equipment_id", flat=True)

        unscheduled_equipment_list = Equipment.objects.filter(
            active_status=True
        ).exclude(
            id__in=scheduled_equipment_ids
        ).select_related("department", "description")

        equipment_descriptions = EquipmentDescription.objects.filter(
            equipment__active_status=True
        ).distinct()

    # Optional department filter
    selected_department_id = request.GET.get("department")
    show_all = request.GET.get("show_all") == "true"
    selected_department = None
    if selected_department_id and not show_all:
        selected_department = get_object_or_404(Department, id=selected_department_id)
        schedules_list = schedules_list.filter(equipment__department_id=selected_department_id)
        unscheduled_equipment_list = unscheduled_equipment_list.filter(department_id=selected_department_id)

    # ✅ ENHANCED: Apply search filter BEFORE pagination (works across all records)
    if search_query:
        schedules_list = schedules_list.filter(
            Q(equipment__description__name__icontains=search_query) |
            Q(equipment__model__icontains=search_query) |
            Q(equipment__serial_number__icontains=search_query) |
            Q(equipment__department__name__icontains=search_query)
        )
        unscheduled_equipment_list = unscheduled_equipment_list.filter(
            Q(description__name__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(serial_number__icontains=search_query) |
            Q(department__name__icontains=search_query)
        )
        logger.info(f"Search applied: '{search_query}' - Found {schedules_list.count()} schedules")

    # ✅ Apply month and year filters ONLY if provided
    if month_filter:
        try:
            schedules_list = schedules_list.filter(scheduled_month__month=int(month_filter))
            logger.info(f"Month filter applied: {month_filter}")
        except (ValueError, TypeError):
            logger.warning(f"Invalid month filter: {month_filter}")

    if year_filter:
        try:
            schedules_list = schedules_list.filter(scheduled_month__year=int(year_filter))
            logger.info(f"Year filter applied: {year_filter}")
        except (ValueError, TypeError):
            logger.warning(f"Invalid year filter: {year_filter}")

    # ✅ ENHANCED: Order by status priority (pending first), then department, then date
    schedules_list = schedules_list.annotate(
        status_priority=Case(
            When(status='pending', then=1),
            When(status='pushed', then=2),
            When(status='completed', then=3),
            default=4,
            output_field=IntegerField()
        )
    ).order_by(
        'status_priority',  # ✅ Pending schedules appear first
        'equipment__department__name',
        'scheduled_month',
        'equipment__description__name'
    )

    # ✅ NEW: Check for overdue and warning schedules
    overdue_info = check_overdue_schedules(schedules_list)

    # ✅ NEW: Add group waiting status to schedules
    schedules_with_status = []
    for schedule in schedules_list:
        schedule_dict = {
            'schedule': schedule,
            'waiting_status': check_group_waiting_status(schedule),
            'is_overdue': schedule in overdue_info['overdue_schedules'],
            'is_warning': schedule in overdue_info['warning_schedules']
        }
        schedules_with_status.append(schedule_dict)

    # Pagination for schedules
    page = request.GET.get("page", 1)
    paginator = Paginator(schedules_with_status, 25)
    try:
        schedules = paginator.page(page)
    except PageNotAnInteger:
        schedules = paginator.page(1)
    except EmptyPage:
        schedules = paginator.page(paginator.num_pages)

    # Pagination for unscheduled equipment
    unscheduled_page = request.GET.get("unscheduled_page", 1)
    unscheduled_paginator = Paginator(unscheduled_equipment_list, 25)
    try:
        unscheduled_equipment = unscheduled_paginator.page(unscheduled_page)
    except PageNotAnInteger:
        unscheduled_equipment = unscheduled_paginator.page(1)
    except EmptyPage:
        unscheduled_equipment = unscheduled_paginator.page(unscheduled_paginator.num_pages)

    # ── Completed schedules — last 30 days only (for the Completed tab) ──
    thirty_days_ago = timezone.now().date() - timedelta(days=30)

    completed_list_qs = CalibrationSchedule.objects.select_related(
        "equipment__department", "equipment__description"
    ).filter(
        status="completed",
        completed_date__gte=thirty_days_ago,   # last 30 days
        equipment__active_status=True,
    )

    # Respect the same department / search filters as the pending tab
    if access_context["access_type"] == "department":
        completed_list_qs = completed_list_qs.filter(
            equipment__department_id=access_context["department_id"]
        )
    if selected_department_id and not show_all:
        completed_list_qs = completed_list_qs.filter(
            equipment__department_id=selected_department_id
        )
    if search_query:
        completed_list_qs = completed_list_qs.filter(
            Q(equipment__description__name__icontains=search_query) |
            Q(equipment__model__icontains=search_query) |
            Q(equipment__serial_number__icontains=search_query) |
            Q(equipment__department__name__icontains=search_query)
        )

    completed_list_qs = completed_list_qs.order_by(
        "-completed_date", "equipment__department__name"
    )

    # Wrap in the same schedule_data dict format the template expects
    completed_with_status = [{"schedule": s} for s in completed_list_qs]

    completed_page = request.GET.get("cpage", 1)
    completed_paginator = Paginator(completed_with_status, 25)
    try:
        completed_schedules = completed_paginator.page(completed_page)
    except PageNotAnInteger:
        completed_schedules = completed_paginator.page(1)
    except EmptyPage:
        completed_schedules = completed_paginator.page(completed_paginator.num_pages)

    # ✅ Context with proper defaults for display
    month_choices = [(i, calendar.month_name[i]) for i in range(1, 13)]
    last_task_id = request.session.get('last_calibration_task_id', '')

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "schedules": schedules,
        "departments": departments,
        "selected_department": selected_department,
        "selected_department_id": selected_department_id if not show_all else None,
        "unscheduled_equipment": unscheduled_equipment,
        "equipment_descriptions": equipment_descriptions,
        "access_context": access_context,
        "user": request.user,
        'selected_month': month_filter if month_filter else str(current_month),
        'selected_year': year_filter if year_filter else str(current_year),
        'month_filter': month_filter,   # raw value — None when not applied
        'year_filter': year_filter,     # raw value — None when not applied
        'current_month': current_month,
        'current_year': current_year,
        'search_query': search_query,  # ✅ Pass search query to template
        'has_filters': bool(month_filter or year_filter or search_query),  # ✅ Indicator for active filters
        'completed_schedules': completed_schedules,  # ✅ NEW: Completed schedules for tab
        'month_choices': month_choices,
        'last_task_id': last_task_id,
        # ✅ NEW: Add stats and overdue info
        'stats': {
            'total': schedules_list.count(),
            'pending': schedules_list.filter(status='pending').count(),
            'pushed': schedules_list.filter(status='pushed').count(),
            'in_progress': schedules_list.filter(status='in_progress').count(),
            'completed': schedules_list.filter(status='completed').count(),
            'overdue': overdue_info['overdue_count'],
            'warning': overdue_info['warning_count']
        },
        'overdue_info': overdue_info,
        **_get_logic_change_context(schedules_list),
        # Full list of schedules flagged for smart reorganisation (shown in the modal)
        'logic_change_schedules': CalibrationSchedule.objects.exclude(
            logic_change_warning=''
        ).exclude(
            logic_change_warning__isnull=True
        ).filter(
            equipment__active_status=True,
            **(
                {'equipment__department_id': access_context['department_id']}
                if access_context['access_type'] == 'department' else {}
            )
        ).select_related(
            'equipment__description', 'equipment__department'
        ).order_by(
            'equipment__department__name', 'equipment__description__name'
        )[:100],
    }

    return render(request, "Calibrition/calScheduels.html", context)


@login_required
def calibration_by_department(request, dept_id):
    """
    View calibration schedules filtered by department
    ✅ FIXED: Search works across all pages
    ✅ FIXED: Date filters work independently
    ✅ FIXED: Pending schedules show first
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"No access context for user {request.user.username}")
        messages.error(
            request,
            "No workshop or department access found. Please contact administrator.",
            extra_tags="access permission"
        )
        return redirect("custom_login")

    departments = Department.objects.all()
    selected_department = get_object_or_404(Department, id=dept_id)

    # Verify access
    if access_context["access_type"] == "department":
        if str(access_context["department_id"]) != str(dept_id):
            messages.error(
                request,
                "You don't have access to this department.",
                extra_tags="access permission"
            )
            return redirect("schedule:calibration_dashboard")

    # ✅ Get filter parameters
    current_month, current_year = get_current_month_year()
    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')
    search_query = request.GET.get('search', '').strip()  # ✅ NEW

    # Base queryset for this department
    schedules_list = CalibrationSchedule.objects.select_related(
        "equipment__department", "equipment__description"
    ).filter(
        equipment__department_id=dept_id,
        equipment__active_status=True
    )

    # ✅ ENHANCED: Apply search filter
    if search_query:
        schedules_list = schedules_list.filter(
            Q(equipment__description__name__icontains=search_query) |
            Q(equipment__model__icontains=search_query) |
            Q(equipment__serial_number__icontains=search_query)
        )
        logger.info(f"Department {dept_id} search: '{search_query}' - Found {schedules_list.count()} schedules")

    # Apply month and year filters
    if month_filter:
        try:
            schedules_list = schedules_list.filter(scheduled_month__month=int(month_filter))
        except (ValueError, TypeError):
            logger.warning(f"Invalid month filter: {month_filter}")

    if year_filter:
        try:
            schedules_list = schedules_list.filter(scheduled_month__year=int(year_filter))
        except (ValueError, TypeError):
            logger.warning(f"Invalid year filter: {year_filter}")

    # ✅ ENHANCED: Order with pending first
    schedules_list = schedules_list.annotate(
        status_priority=Case(
            When(status='pending', then=1),
            When(status='pushed', then=2),
            When(status='completed', then=3),
            default=4,
            output_field=IntegerField()
        )
    ).order_by('status_priority', 'scheduled_month', 'equipment__description__name')

    # ✅ NEW: Check for overdue and warning schedules
    overdue_info = check_overdue_schedules(schedules_list)

    # ✅ NEW: Add group waiting status to schedules
    schedules_with_status = []
    for schedule in schedules_list:
        schedule_dict = {
            'schedule': schedule,
            'waiting_status': check_group_waiting_status(schedule),
            'is_overdue': schedule in overdue_info['overdue_schedules'],
            'is_warning': schedule in overdue_info['warning_schedules']
        }
        schedules_with_status.append(schedule_dict)

    # Pagination
    page = request.GET.get("page", 1)
    paginator = Paginator(schedules_with_status, 25)
    try:
        schedules = paginator.page(page)
    except PageNotAnInteger:
        schedules = paginator.page(1)
    except EmptyPage:
        schedules = paginator.page(paginator.num_pages)

    # Unscheduled equipment for this department
    # FIX #1: Only exclude equipment that has an ACTIVE (non-completed) schedule.
    scheduled_equipment_ids = CalibrationSchedule.objects.filter(
        equipment__department_id=dept_id,
        equipment__active_status=True,
        status__in=["pending", "pushed", "in_progress", "overdue"],  # ← KEY FIX
    ).values_list("equipment_id", flat=True)

    unscheduled_equipment_list = Equipment.objects.filter(
        department_id=dept_id,
        active_status=True
    ).exclude(id__in=scheduled_equipment_ids).select_related("department", "description")

    # ✅ ENHANCED: Apply search to unscheduled equipment
    if search_query:
        unscheduled_equipment_list = unscheduled_equipment_list.filter(
            Q(description__name__icontains=search_query) |
            Q(model__icontains=search_query) |
            Q(serial_number__icontains=search_query)
        )

    # Pagination for unscheduled
    unscheduled_page = request.GET.get("unscheduled_page", 1)
    unscheduled_paginator = Paginator(unscheduled_equipment_list, 25)
    try:
        unscheduled_equipment = unscheduled_paginator.page(unscheduled_page)
    except PageNotAnInteger:
        unscheduled_equipment = unscheduled_paginator.page(1)
    except EmptyPage:
        unscheduled_equipment = unscheduled_paginator.page(unscheduled_paginator.num_pages)

    # ── Completed schedules — last 30 days only (for the Completed tab) ──
    thirty_days_ago = timezone.now().date() - timedelta(days=30)
    completed_list_qs = CalibrationSchedule.objects.select_related(
        "equipment__department", "equipment__description"
    ).filter(
        status="completed",
        completed_date__gte=thirty_days_ago,
        equipment__department_id=dept_id,
        equipment__active_status=True,
    )
    if search_query:
        completed_list_qs = completed_list_qs.filter(
            Q(equipment__description__name__icontains=search_query) |
            Q(equipment__model__icontains=search_query) |
            Q(equipment__serial_number__icontains=search_query) |
            Q(equipment__department__name__icontains=search_query)
        )
    completed_list_qs = completed_list_qs.order_by("-completed_date")

    completed_with_status = [{"schedule": s} for s in completed_list_qs]
    completed_page = request.GET.get("cpage", 1)
    completed_paginator = Paginator(completed_with_status, 25)
    try:
        completed_schedules = completed_paginator.page(completed_page)
    except PageNotAnInteger:
        completed_schedules = completed_paginator.page(1)
    except EmptyPage:
        completed_schedules = completed_paginator.page(completed_paginator.num_pages)

    # Equipment descriptions for this department
    equipment_descriptions = EquipmentDescription.objects.filter(
        equipment__department_id=dept_id,
        equipment__active_status=True
    ).distinct()

    month_choices = [(i, calendar.month_name[i]) for i in range(1, 13)]
    last_task_id = request.session.get('last_calibration_task_id', '')

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "schedules": schedules,
        "departments": departments,
        "selected_department": selected_department,
        "selected_department_id": selected_department.id,
        "unscheduled_equipment": unscheduled_equipment,
        "equipment_descriptions": equipment_descriptions,
        "title": f"Calibration Equipment in {selected_department.name}",
        "access_context": access_context,
        "user": request.user,
        'selected_month': month_filter if month_filter else str(current_month),
        'selected_year': year_filter if year_filter else str(current_year),
        'month_filter': month_filter,   # raw value — None when not applied
        'year_filter': year_filter,     # raw value — None when not applied
        'current_month': current_month,
        'current_year': current_year,
        'search_query': search_query,  # ✅ Pass search query
        'has_filters': bool(month_filter or year_filter or search_query),
        'completed_schedules': completed_schedules,  # ✅ NEW: Completed schedules for tab
        'month_choices': month_choices,
        'last_task_id': last_task_id,
        # ✅ NEW: Add stats and overdue info
        'stats': {
            'total': schedules_list.count(),
            'pending': schedules_list.filter(status='pending').count(),
            'pushed': schedules_list.filter(status='pushed').count(),
            'in_progress': schedules_list.filter(status='in_progress').count(),
            'completed': schedules_list.filter(status='completed').count(),
            'overdue': overdue_info['overdue_count'],
            'warning': overdue_info['warning_count']
        },
        'overdue_info': overdue_info,
        **_get_logic_change_context(schedules_list),
        # Full list of schedules flagged for smart reorganisation (shown in the modal)
        'logic_change_schedules': CalibrationSchedule.objects.exclude(
            logic_change_warning=''
        ).exclude(
            logic_change_warning__isnull=True
        ).filter(
            equipment__active_status=True,
            equipment__department_id=dept_id,
        ).select_related(
            'equipment__description', 'equipment__department'
        ).order_by(
            'equipment__description__name'
        )[:100],
    }

    return render(request, "Calibrition/calScheduels.html", context)

def pending_calibrations(request):
    """
    View for pending and pushed calibrations only
    ✅ ENHANCED: Added search across all pages, pagination, and filter persistence
    """
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"No access context for user {request.user.username}")
        messages.error(
            request,
            "No workshop or department access found. Please contact administrator.",
            extra_tags="access permission",
        )
        return redirect("custom_login")

    # Save access context into session
    if access_context.get("workshop_id"):
        request.session["workshop_id"] = str(access_context["workshop_id"])
    if access_context.get("department_id"):
        request.session["department_id"] = str(access_context["department_id"])
    request.session.modified = True

    # ✅ Get filter parameters with proper defaults
    today = now()
    current_month = request.GET.get("month", str(today.month))
    current_year = request.GET.get("year", str(today.year))
    search_query = request.GET.get("search", "").strip()
    selected_department_id = request.GET.get("department", "")

    # ✅ Base queryset WITH active_status filter
    if access_context["access_type"] == "department":
        departments = Department.objects.filter(id=access_context["department_id"])
        schedules_list = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).filter(
            equipment__department_id=access_context["department_id"],
            equipment__active_status=True,
            status__in=["pending", "pushed"],
        )
    else:
        departments = Department.objects.all()
        schedules_list = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).filter(
            equipment__active_status=True,
            status__in=["pending", "pushed"]
        )

    # ✅ Department filter (for workshop-level users)
    selected_department = None
    if selected_department_id and access_context["access_type"] == "workshop":
        try:
            selected_department = Department.objects.get(id=selected_department_id)
            schedules_list = schedules_list.filter(equipment__department_id=selected_department_id)
            logger.info(f"Department filter applied: {selected_department.name}")
        except Department.DoesNotExist:
            logger.warning(f"Invalid department ID: {selected_department_id}")

    # ✅ Month & year filtering
    try:
        filter_month = int(current_month)
        filter_year = int(current_year)
        filter_date = date(filter_year, filter_month, 1)
        schedules_list = schedules_list.annotate(
            month=TruncMonth("scheduled_month")
        ).filter(month=filter_date)
        logger.info(f"Date filter applied: {filter_month}/{filter_year}")
    except (ValueError, TypeError):
        filter_date = date(today.year, today.month, 1)
        schedules_list = schedules_list.annotate(
            month=TruncMonth("scheduled_month")
        ).filter(month=filter_date)

    # ✅ ENHANCED: Search filter BEFORE pagination (works across all pages)
    if search_query:
        schedules_list = schedules_list.filter(
            Q(equipment__description__name__icontains=search_query)
            | Q(equipment__model__icontains=search_query)
            | Q(equipment__serial_number__icontains=search_query)
            | Q(equipment__department__name__icontains=search_query)
        )
        logger.info(f"Search applied: '{search_query}' - Found {schedules_list.count()} schedules")

    # ✅ ENHANCED: Order with pending first
    schedules_list = schedules_list.annotate(
        status_priority=Case(
            When(status='pending', then=1),
            When(status='pushed', then=2),
            default=3,
            output_field=IntegerField()
        )
    ).order_by(
        "status_priority",
        "equipment__department__name",
        "equipment__description__name",
        "scheduled_month"
    )

    # ✅ Pagination (50 items per page)
    paginator = Paginator(schedules_list, 50)
    page = request.GET.get("page", 1)
    try:
        schedules = paginator.page(page)
    except PageNotAnInteger:
        schedules = paginator.page(1)
    except EmptyPage:
        schedules = paginator.page(paginator.num_pages)

    # Statistics
    total_pending = schedules_list.filter(status="pending").count()
    total_pushed = schedules_list.filter(status="pushed").count()
    overdue_count = schedules_list.filter(scheduled_month__lt=filter_date).count()

    # ✅ Year options (extended to 2081)
    years = range(today.year - 5, 2082)

    context = {
        'show_sidebar': True,  # Enable sidebar with hamburger menu
        "schedules": schedules,
        "departments": departments,
        "selected_department": selected_department,
        "selected_department_id": selected_department_id,
        "access_context": access_context,
        "user": request.user,
        "current_month": int(current_month),
        "current_year": int(current_year),
        "years": years,
        "today": today.date(),
        "title": f"Pending Calibrations - {selected_department.name if selected_department else 'All Departments'}",
        "total_pending": total_pending,
        "total_pushed": total_pushed,
        "overdue_count": overdue_count,
        "search_query": search_query,
    }

    return render(request, "Calibrition/Pending-Calibrations.html", context)


def get_user_access_context(request):
    """
    Determine user's access level and return appropriate context based on UserProfile model.
    Returns: dict with 'access_type', 'workshop_id', 'department_id', 'workshop', 'department', 'role', 'level'.
    """
    try:
        profile = request.user.userprofile
        role = profile.role
        level = profile.level

        if role == 'Tech':
            if level == 'Engineer Incharge' and profile.workshop:
                return {
                    'access_type': 'workshop',
                    'workshop_id': profile.workshop.id,
                    'department_id': None,
                    'workshop': profile.workshop,
                    'department': None,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': True,
                    'can_edit': True,
                    'can_schedule': True
                }
            elif level == 'Engineer' and profile.department:
                return {
                    'access_type': 'department',
                    'workshop_id': profile.department.workshop_id if profile.department.workshop else None,
                    'department_id': profile.department.id,
                    'workshop': profile.department.workshop if profile.department.workshop else None,
                    'department': profile.department,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': False,
                    'can_edit': True,
                    'can_schedule': True
                }
            elif profile.workshop:
                return {
                    'access_type': 'workshop',
                    'workshop_id': profile.workshop.id,
                    'department_id': None,
                    'workshop': profile.workshop,
                    'department': None,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': True,
                    'can_edit': True,
                    'can_schedule': True
                }
        elif role == 'NIC' and profile.department:
            return {
                'access_type': 'department',
                'workshop_id': profile.department.workshop_id if profile.department.workshop else None,
                'department_id': profile.department.id,
                'workshop': profile.department.workshop if profile.department.workshop else None,
                'department': profile.department,
                'role': role,
                'level': level,
                'can_manage_all_departments': False,
                'can_edit': False,  # NIC cannot edit
                'can_schedule': False  # NIC cannot schedule
            }
        elif role == 'HOD':
            if profile.workshop:
                return {
                    'access_type': 'workshop',
                    'workshop_id': profile.workshop.id,
                    'department_id': None,
                    'workshop': profile.workshop,
                    'department': None,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': True,
                    'can_edit': True,
                    'can_schedule': True
                }
            elif profile.department:
                return {
                    'access_type': 'department',
                    'workshop_id': profile.department.workshop_id if profile.department.workshop else None,
                    'department_id': profile.department.id,
                    'workshop': profile.department.workshop if profile.department.workshop else None,
                    'department': profile.department,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': False,
                    'can_edit': True,
                    'can_schedule': True
                }

        workshop_id = request.session.get('workshop_id')
        if workshop_id:
            try:
                workshop = Workshop.objects.get(id=workshop_id)
                return {
                    'access_type': 'workshop',
                    'workshop_id': workshop_id,
                    'department_id': None,
                    'workshop': workshop,
                    'department': None,
                    'role': role,
                    'level': level,
                    'can_manage_all_departments': True,
                    'can_edit': True,
                    'can_schedule': True
                }
            except Workshop.DoesNotExist:
                logger.warning(f"Workshop ID {workshop_id} not found in session for user {request.user.username}")
                pass
    except AttributeError as e:
        logger.warning(f"User {request.user.username} profile access error: {e}")

    return None


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
            scheduled_month = datetime.today().replace(day=1) + relativedelta(months=1)

            # Check if already scheduled
            if CalibrationSchedule.objects.filter(equipment=equipment).exists():
                messages.error(
                    request,
                    f"Equipment {equipment.description.name if equipment.description else 'N/A'} is already scheduled for calibration.",
                    extra_tags="schedule conflict",
                )
                return redirect("schedule:calibration_dashboard")

            # ✅ ENHANCED: Get planning logic from system
            common_logic = CalibrationSchedule.objects.filter(
                active_status=True,
                planning_logic__isnull=False
            ).values('planning_logic').annotate(
                count=Count('id')
            ).order_by('-count').first()

            planning_logic = common_logic['planning_logic'] if common_logic else 'department'

            # ✅ ENHANCED: USE GROUP-AWARE INITIALIZATION
            try:
                from .instant_reconciliation import initialize_new_equipment_schedule

                schedule = initialize_new_equipment_schedule(
                    equipment,
                    planning_logic=planning_logic,
                    period=12
                )

                if schedule:
                    messages.success(
                        request,
                        f"Equipment {equipment.description.name if equipment.description else 'N/A'} "
                        f"scheduled for {schedule.scheduled_month.strftime('%B %Y')} "
                        f"(aligned with group).",
                        extra_tags="schedule"
                    )
                    logger.info(
                        f"Equipment {equipment.id} scheduled for {schedule.scheduled_month} "
                        f"by user {request.user.username}"
                    )
                else:
                    messages.error(request, "Failed to create schedule.")

            except ImportError:
                # Fallback to old method if instant_reconciliation not available
                schedule = CalibrationSchedule.objects.create(
                    equipment=equipment,
                    scheduled_month=date.today().replace(day=1),
                    status='pending'
                )
                messages.success(request, f"Equipment scheduled.")

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

        try:
            task = initialize_calibration_schedule_with_logic.delay(
                None,  # workshop not restricted anymore
                planning_logic,
                calibration_period,
                base_month,
                base_year,
                max_departments,
                max_descriptions,
                [],
                False,
                equipment_ids,
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


# ==================== CALIBRATION TRIGGER ====================
@login_required
def trigger_calibration_initialization(request):
    """
    Trigger calibration schedule initialization for ALL equipment
    ✅ UPDATED: Added special class selection and always normalize
    """
    request_id = str(uuid.uuid4())[:8]

    logger.info(f"{'='*80}")
    logger.info(f"[REQ-{request_id}] CALIBRATION INITIALIZATION REQUEST STARTED")
    logger.info(f"[REQ-{request_id}] User: {request.user.username} (ID: {request.user.id})")
    logger.info(f"[REQ-{request_id}] Method: {request.method}")
    logger.info(f"{'='*80}")

    access_context = get_user_access_context(request)

    if not access_context or not access_context.get("can_schedule", False):
        logger.warning(
            f"[REQ-{request_id}] ❌ ACCESS DENIED: "
            f"user={request.user.username}, "
            f"has_context={access_context is not None}, "
            f"can_schedule={access_context.get('can_schedule') if access_context else False}"
        )
        messages.error(
            request,
            "You don't have permission to initialize calibration schedules.",
            extra_tags="permission schedule"
        )
        return redirect("schedule:calibration_dashboard")

    logger.info(f"[REQ-{request_id}] ✅ ACCESS GRANTED")
    logger.info(f"[REQ-{request_id}] Access Type: {access_context['access_type']}")
    logger.info(f"[REQ-{request_id}] Role: {access_context.get('role', 'N/A')}")

    if request.method == "POST":
        logger.info(f"[REQ-{request_id}] {'─'*80}")
        logger.info(f"[REQ-{request_id}] PHASE 1: FORM DATA PARSING")
        logger.info(f"[REQ-{request_id}] {'─'*80}")

        try:
            # Parse form data
            try:
                planning_logic = request.POST.get("planning_logic", "department")
                calibration_period = int(request.POST.get("calibration_period", 12))
                base_month = int(request.POST.get("base_month", 1))
                base_year = int(request.POST.get("base_year", datetime.today().year))
                max_departments = int(request.POST.get("max_departments", 100))
                max_descriptions = int(request.POST.get("max_descriptions", 100))
                preserve_existing = request.POST.get("preserve_existing") == "true"

                # ✅ NEW: Special class selection
                special_class_departments = request.POST.getlist("special_class_departments")
                special_class_descriptions = request.POST.getlist("special_class_descriptions")

                # ✅ CHANGED: Always normalize existing schedules
                normalize_existing = True

                logger.info(f"[REQ-{request_id}] ✅ Form data parsed successfully")
                logger.info(f"[REQ-{request_id}]   Planning Logic: {planning_logic}")
                logger.info(f"[REQ-{request_id}]   Calibration Period: {calibration_period} months")
                logger.info(f"[REQ-{request_id}]   Base Date: {base_month}/{base_year}")
                logger.info(f"[REQ-{request_id}]   Preserve Existing: {preserve_existing}")
                logger.info(f"[REQ-{request_id}]   Normalize Existing: {normalize_existing} (Always Active)")
                logger.info(f"[REQ-{request_id}]   Special Departments: {special_class_departments}")
                logger.info(f"[REQ-{request_id}]   Special Descriptions: {special_class_descriptions}")

            except (ValueError, TypeError) as e:
                logger.error(f"[REQ-{request_id}] ❌ FORM DATA VALIDATION ERROR: {str(e)}")
                messages.error(request, f"Invalid input data: {str(e)}")
                return redirect("schedule:calibration_dashboard")

            logger.info(f"[REQ-{request_id}] {'─'*80}")
            logger.info(f"[REQ-{request_id}] PHASE 2: EQUIPMENT VALIDATION")
            logger.info(f"[REQ-{request_id}] {'─'*80}")

            # Determine which equipment IDs this user can schedule
            if access_context["access_type"] == "department":
                equipment_ids = [
                    str(eq_id) for eq_id in Equipment.objects.filter(
                        department_id=access_context["department_id"],
                        active_status=True
                    ).values_list("id", flat=True)
                ]
                logger.info(f"[REQ-{request_id}] Department-level: {len(equipment_ids)} equipment")
            else:
                equipment_ids = [
                    str(eq_id) for eq_id in Equipment.objects.filter(
                        active_status=True
                    ).values_list("id", flat=True)
                ]
                logger.info(f"[REQ-{request_id}] Workshop/Admin-level: {len(equipment_ids)} equipment")

            if not equipment_ids:
                logger.warning(f"[REQ-{request_id}] ⚠️ NO ACTIVE EQUIPMENT FOUND")
                messages.warning(request, "No active equipment found to schedule.")
                return redirect("schedule:calibration_dashboard")

            logger.info(f"[REQ-{request_id}] {'─'*80}")
            logger.info(f"[REQ-{request_id}] PHASE 3: CELERY TASK QUEUING")
            logger.info(f"[REQ-{request_id}] {'─'*80}")

            # Queue the task
            try:
                logger.info(f"[REQ-{request_id}] Queuing task to Celery...")
                task = initialize_calibration_schedule_with_logic.delay(
                    planning_logic=planning_logic,  # ✅ Use keyword arguments
                    calibration_period=calibration_period,
                    base_month=base_month,
                    base_year=base_year,
                    max_departments=max_departments,
                    max_descriptions=max_descriptions,
                    selected_descriptions=[],
                    preserve_existing=preserve_existing,
                    specific_equipment_ids=equipment_ids,
                    normalize_existing=normalize_existing,  # ✅ This parameter exists now
                    special_class_departments=special_class_departments,  # ✅ This too
                    special_class_descriptions=special_class_descriptions,  # ✅ And this
                )
                logger.info(f"[REQ-{request_id}] ✅ TASK QUEUED SUCCESSFULLY")
                logger.info(f"[REQ-{request_id}]   Task ID: {task.id}")
                logger.info(f"[REQ-{request_id}]   Task State: {task.state}")

                request.session['last_calibration_task_id'] = str(task.id)
                request.session['last_calibration_task_time'] = timezone.now().isoformat()
                request.session['init_task_start'] = timezone.now().isoformat()
                request.session.modified = True

                messages.success(
                    request,
                    f"Calibration schedule initialization started successfully! "
                    f"Task ID: {task.id}. This may take a few minutes. "
                    f"Normalizing all existing schedules to ensure consistency...",
                    extra_tags="schedule initialization task",
                )

                logger.info(f"[REQ-{request_id}] {'='*80}")
                logger.info(f"[REQ-{request_id}] ✅ REQUEST COMPLETED SUCCESSFULLY")
                logger.info(f"[REQ-{request_id}] {'='*80}")

            except Exception as task_error:
                error_type = type(task_error).__name__
                error_msg = str(task_error)

                logger.error(f"[REQ-{request_id}] ❌ TASK QUEUING FAILED")
                logger.error(f"[REQ-{request_id}] Error Type: {error_type}")
                logger.error(f"[REQ-{request_id}] Error Message: {error_msg}")
                logger.exception(f"[REQ-{request_id}] Full traceback:")

                if "Connection" in error_type or "connection" in error_msg.lower():
                    user_message = (
                        f"Cannot connect to message broker: {error_msg}. "
                        f"Please ensure Redis/RabbitMQ is running."
                    )
                elif "ModuleNotFoundError" in error_type or "No module named" in error_msg:
                    user_message = (
                        f"Celery module error: {error_msg}. "
                        f"Please ensure Celery is properly installed."
                    )
                elif "timeout" in error_msg.lower():
                    user_message = (
                        f"Connection timeout: {error_msg}. "
                        f"Please check if Celery worker is running and broker is accessible."
                    )
                else:
                    user_message = (
                        f"Task queuing failed ({error_type}): {error_msg}. "
                        f"Please ensure Celery worker is running."
                    )

                messages.error(
                    request,
                    user_message,
                    extra_tags="schedule initialization error",
                )

        except Exception as e:
            logger.error(f"[REQ-{request_id}] ❌ UNEXPECTED ERROR: {str(e)}")
            logger.exception(f"[REQ-{request_id}] Full traceback:")

            messages.error(
                request,
                f"An unexpected error occurred: {str(e)}",
                extra_tags="schedule initialization error"
            )

        return redirect("schedule:calibration_dashboard")

    logger.info(f"[REQ-{request_id}] GET request - redirecting to dashboard")
    return redirect("schedule:calibration_dashboard")


@login_required
def export_calibration_excel(request):
    """Export calibration schedules to Excel"""
    access_context = get_user_access_context(request)
    if not access_context:
        logger.warning(f"Access denied for user {request.user.username} on export calibration")
        messages.error(
            request,
            "Access denied.",
            extra_tags="access permission",
        )
        return redirect("schedule:calibration_dashboard")

    if access_context["access_type"] == "department":
        schedules = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).filter(
            equipment__department_id=access_context["department_id"]
        )
    else:
        schedules = CalibrationSchedule.objects.select_related(
            "equipment__department", "equipment__description"
        ).all()

    selected_department_id = request.GET.get("department")
    if selected_department_id:
        schedules = schedules.filter(equipment__department_id=selected_department_id)

    wb = Workbook()
    ws = wb.active
    ws.title = "Calibration Schedules"
    ws.append(
        ["Description", "Model", "Serial Number", "Department", "Status", "Scheduled Month", "Calibration Period"]
    )

    for sched in schedules:
        ws.append([
            sched.equipment.description.name if sched.equipment.description else "N/A",
            sched.equipment.model or "N/A",
            sched.equipment.serial_number or "N/A",
            sched.equipment.department.name if sched.equipment.department else "N/A",
            sched.status.title(),
            sched.scheduled_month.strftime("%B %Y") if sched.scheduled_month else "N/A",
            f"{sched.calibration_period} Months" if sched.calibration_period else "N/A",
        ])

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = "attachment; filename=Calibration_Schedules.xlsx"
    wb.save(response)
    return response


@login_required
def clear_calibration_department_filter(request):
    """Clear department filter for calibration dashboard"""
    return redirect('schedule:calibration_dashboard')


# ==================== NEW API ENDPOINTS ====================


@login_required
def get_overdue_status(request):
    """
    ✅ NEW API ENDPOINT
    API endpoint to get overdue schedule information
    """
    try:
        access_context = get_user_access_context(request)
        if not access_context:
            return JsonResponse({'error': 'Access denied'}, status=403)

        # Get schedules
        if access_context['access_type'] == 'department':
            schedules = CalibrationSchedule.objects.filter(
                equipment__department_id=access_context['department_id'],
                equipment__active_status=True
            )
        else:
            schedules = CalibrationSchedule.objects.filter(
                equipment__active_status=True
            )

        overdue_info = check_overdue_schedules(schedules)

        return JsonResponse({
            'overdue_count': overdue_info['overdue_count'],
            'warning_count': overdue_info['warning_count'],
            'total': schedules.count()
        })

    except Exception as e:
        logger.error(f"Error getting overdue status: {str(e)}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def get_waiting_groups(request):
    """
    ✅ NEW API ENDPOINT
    API endpoint to get schedules waiting for group completion
    """
    try:
        access_context = get_user_access_context(request)
        if not access_context:
            return JsonResponse({'error': 'Access denied'}, status=403)

        # Get completed schedules
        if access_context['access_type'] == 'department':
            completed = CalibrationSchedule.objects.filter(
                equipment__department_id=access_context['department_id'],
                status='completed'
            )
        else:
            completed = CalibrationSchedule.objects.filter(
                status='completed'
            )

        # Filter for those that have is_locked=False
        if hasattr(CalibrationSchedule, 'is_locked'):
            completed = completed.filter(is_locked=False)

        waiting_groups = []
        for schedule in completed:
            waiting_status = check_group_waiting_status(schedule)
            if waiting_status['is_waiting']:
                waiting_groups.append({
                    'schedule_id': str(schedule.id),
                    'group_name': waiting_status['group_name'],
                    'completed': waiting_status['completed_count'],
                    'total': waiting_status['total_count'],
                    'pending_equipment': waiting_status['pending_equipment']
                })

        return JsonResponse({
            'waiting_count': len(waiting_groups),
            'waiting_groups': waiting_groups
        })

    except Exception as e:
        logger.error(f"Error getting waiting groups: {str(e)}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def get_group_status(request, schedule_id):
    """
    API endpoint to get group completion status for a specific schedule
    """
    try:
        schedule = get_object_or_404(CalibrationSchedule, id=schedule_id)
        waiting_status = check_group_waiting_status(schedule)

        return JsonResponse({
            'is_waiting': waiting_status['is_waiting'],
            'completed_count': waiting_status.get('completed_count', 0),
            'total_count': waiting_status.get('total_count', 0),
            'group_name': waiting_status.get('group_name', ''),
            'pending_equipment': waiting_status.get('pending_equipment', [])
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def get_calibration_task_status(request, task_id):
    """
    Return JSON status of a calibration initialization task.
    Used by front-end polling to trigger table refresh on completion.
    """
    from celery.result import AsyncResult

    result = AsyncResult(task_id)
    done = result.ready()
    state = result.state
    info = result.info if done else None

    return JsonResponse({
        'task_id': task_id,
        'ready': done,
        'state': state,
        'info': info
    })


# ==================== AJAX VIEWS ====================

# AJAX helpers (using existing get_user_access_context)

def _apply_ajax_common_filters(qs, request, access_context):
    search = request.GET.get('search', '').strip()
    month = request.GET.get('month', '').strip()
    year = request.GET.get('year', '').strip()
    show_all = request.GET.get('show_all') == 'true'
    department = request.GET.get('department', '').strip()

    if access_context['access_type'] == 'department':
        qs = qs.filter(equipment__department_id=access_context['department_id'])
    elif department:
        qs = qs.filter(equipment__department_id=department)

    if search:
        qs = qs.filter(
            Q(equipment__description__name__icontains=search) |
            Q(equipment__model__icontains=search) |
            Q(equipment__serial_number__icontains=search) |
            Q(equipment__department__name__icontains=search)
        )

    if not show_all:
        if month and year:
            qs = qs.filter(scheduled_month__month=int(month), scheduled_month__year=int(year))
        elif month:
            qs = qs.filter(scheduled_month__month=int(month))
        elif year:
            qs = qs.filter(scheduled_month__year=int(year))
        else:
            today = datetime.today().replace(day=1)
            qs = qs.filter(
                scheduled_month__gte=today - timedelta(days=90),
                scheduled_month__lte=today + timedelta(days=90),
            )

    return qs


def _ajax_schedule_to_dict(schedule, is_overdue=False, is_warning=False, waiting_status=None):
    eq = schedule.equipment
    return {
        'schedule': {
            'id': schedule.id,
            'status': schedule.status,
            'planning_logic': schedule.planning_logic or '',
            'is_locked': schedule.is_locked,
            'logic_change_warning': schedule.logic_change_warning or '',
            'scheduled_month_display': schedule.scheduled_month.strftime('%B %Y') if schedule.scheduled_month else '',
            'completed_date_display': schedule.completed_date.strftime('%d %b %Y') if schedule.completed_date else '',
            'overdue_since': '',
            'equipment_description': eq.description.name if eq.description else '',
            'serial_number': eq.serial_number or '',
            'model': eq.model or '',
            'department_name': eq.department.name if eq.department else '',
        },
        'is_overdue': is_overdue,
        'is_warning': is_warning,
        'waiting_status': waiting_status or {'is_waiting': False},
    }


@login_required
def ajax_schedules(request):
    today = datetime.today().date()
    access_context = get_user_access_context(request)

    qs = CalibrationSchedule.objects.select_related(
        'equipment__department', 'equipment__description'
    ).filter(
        status__in=['pending', 'pushed', 'in_progress'],
        equipment__active_status=True,
    )
    qs = _apply_ajax_common_filters(qs, request, access_context)
    qs = qs.order_by('scheduled_month', 'equipment__department__name')

    paginator = Paginator(qs, 25)
    page = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    results = []
    for s in page_obj:
        m = s.scheduled_month
        is_overdue = m and m < today.replace(day=1)
        is_warning = m and (today.replace(day=1) <= m <= today.replace(day=1) + timedelta(days=30))
        results.append(_ajax_schedule_to_dict(s, is_overdue=is_overdue, is_warning=is_warning))

    meta = {
        'count': paginator.count,
        'num_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'overdue_count': qs.filter(scheduled_month__lt=today.replace(day=1)).count(),
    }

    return JsonResponse({'results': results, 'meta': meta})


@login_required
def ajax_completed_schedules(request):
    access_context = get_user_access_context(request)
    thirty_days_ago = datetime.today().date() - timedelta(days=30)

    qs = CalibrationSchedule.objects.select_related(
        'equipment__department', 'equipment__description'
    ).filter(
        status='completed',
        completed_date__gte=thirty_days_ago,
        equipment__active_status=True,
    )

    if access_context['access_type'] == 'department':
        qs = qs.filter(equipment__department_id=access_context['department_id'])
    else:
        dept = request.GET.get('department', '').strip()
        if dept:
            qs = qs.filter(equipment__department_id=dept)

    search = request.GET.get('search', '').strip()
    if search:
        qs = qs.filter(
            Q(equipment__description__name__icontains=search) |
            Q(equipment__model__icontains=search) |
            Q(equipment__serial_number__icontains=search) |
            Q(equipment__department__name__icontains=search)
        )

    qs = qs.order_by('-completed_date', 'equipment__department__name')

    paginator = Paginator(qs, 25)
    page = request.GET.get('cpage', 1)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    results = [_ajax_schedule_to_dict(s) for s in page_obj]

    meta = {
        'count': paginator.count,
        'num_pages': paginator.num_pages,
        'current_page': page_obj.number,
    }

    return JsonResponse({'results': results, 'meta': meta})


@login_required
def ajax_unscheduled_equipment(request):
    access_context = get_user_access_context(request)

    scheduled_ids = CalibrationSchedule.objects.filter(
        equipment__active_status=True,
        status__in=['pending', 'pushed', 'in_progress', 'overdue'],
    ).values_list('equipment_id', flat=True)

    qs = Equipment.objects.filter(active_status=True).exclude(id__in=scheduled_ids).select_related('department', 'description')

    if access_context['access_type'] == 'department':
        qs = qs.filter(department_id=access_context['department_id'])
    else:
        dept = request.GET.get('department', '').strip()
        if dept:
            qs = qs.filter(department_id=dept)

    search = request.GET.get('search', '').strip()
    if search:
        qs = qs.filter(
            Q(description__name__icontains=search) |
            Q(model__icontains=search) |
            Q(serial_number__icontains=search) |
            Q(department__name__icontains=search)
        )

    qs = qs.order_by('department__name', 'description__name')

    paginator = Paginator(qs, 25)
    page = request.GET.get('upage', 1)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    results = [
        {
            'id': eq.id,
            'description': eq.description.name if eq.description else '',
            'serial_number': eq.serial_number or '',
            'model': eq.model or '',
            'department_name': eq.department.name if eq.department else '',
        }
        for eq in page_obj
    ]

    meta = {
        'count': paginator.count,
        'num_pages': paginator.num_pages,
        'current_page': page_obj.number,
    }

    return JsonResponse({'results': results, 'meta': meta})


@login_required
def ajax_schedule_stats(request):
    access_context = get_user_access_context(request)
    today = datetime.today().date()

    base = CalibrationSchedule.objects.filter(equipment__active_status=True)
    if access_context['access_type'] == 'department':
        base = base.filter(equipment__department_id=access_context['department_id'])
    elif request.GET.get('department'):
        base = base.filter(equipment__department_id=request.GET['department'])

    thirty_ago = today - timedelta(days=30)

    return JsonResponse({
        'total': base.exclude(status='completed').count(),
        'pending': base.filter(status__in=['pending', 'pushed', 'in_progress']).count(),
        'overdue': base.filter(status='overdue').count(),
        'completed': base.filter(status='completed', completed_date__gte=thirty_ago).count(),
        'warnings': base.exclude(logic_change_warning='').exclude(logic_change_warning__isnull=True).count(),
    })
