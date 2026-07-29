"""calSchedules views — Celery-backed init / auto-advance / normalize / reorganize triggers."""
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
        calibration_period = int(request.POST.get("calibration_period", 12))
        dry_run = request.POST.get("dry_run", "").lower() == "true"

        # Special-class groups bypass the per-month cap (all land on base_month).
        special_class_departments = request.POST.getlist("special_class_departments")
        special_class_descriptions = request.POST.getlist("special_class_descriptions")

        # Clamp to sane ranges
        base_month = max(1, min(12, base_month))
        max_departments = max(1, min(100, max_departments))
        max_descriptions = max(1, min(100, max_descriptions))
        if calibration_period not in (6, 12):
            calibration_period = 12

        logger.info(
            f"[{request_id}] Smart-reorg params: logic={new_planning_logic}, "
            f"base_month={base_month}, max_depts={max_departments}, "
            f"max_descs={max_descriptions}, period={calibration_period}, "
            f"special_depts={len(special_class_departments)}, "
            f"special_descs={len(special_class_descriptions)}, dry_run={dry_run}"
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
            calibration_period=calibration_period,
            special_class_departments=special_class_departments,
            special_class_descriptions=special_class_descriptions,
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
