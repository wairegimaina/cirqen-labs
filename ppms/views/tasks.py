"""ppms.views — Celery-backed init / reorganize / normalize triggers."""
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
def trigger_initialization(request):
    """Initialize PPM schedules with comprehensive error handling and logging"""
    # Generate unique request ID for tracking
    import uuid
    request_id = str(uuid.uuid4())[:8]

    logger.info(f"{'='*80}")
    logger.info(f"[REQ-{request_id}] PPM INITIALIZATION REQUEST STARTED")
    logger.info(f"[REQ-{request_id}] User: {request.user.username} (ID: {request.user.id})")
    logger.info(f"[REQ-{request_id}] Method: {request.method}")
    logger.info(f"[REQ-{request_id}] Path: {request.path}")
    logger.info(f"[REQ-{request_id}] IP: {request.META.get('REMOTE_ADDR', 'Unknown')}")
    logger.info(f"{'='*80}")

    access_context = get_user_access_context(request)

    if not access_context or not access_context['workshop_id'] or not access_context.get('can_schedule', False):
        logger.warning(
            f"[REQ-{request_id}] ❌ ACCESS DENIED: "
            f"user={request.user.username}, "
            f"has_context={access_context is not None}, "
            f"workshop_id={access_context.get('workshop_id') if access_context else None}, "
            f"can_schedule={access_context.get('can_schedule') if access_context else False}"
        )
        messages.error(request, "You don't have permission to initialize schedules.")
        return redirect('ppm_dashboard')

    workshop_id = access_context['workshop_id']
    request.session['workshop_id'] = str(workshop_id)
    request.session.modified = True

    logger.info(f"[REQ-{request_id}] ✅ ACCESS GRANTED")
    logger.info(f"[REQ-{request_id}] Workshop ID: {workshop_id}")
    logger.info(f"[REQ-{request_id}] Access Type: {access_context['access_type']}")
    logger.info(f"[REQ-{request_id}] Department ID: {access_context.get('department_id', 'N/A')}")
    logger.info(f"[REQ-{request_id}] Role: {access_context.get('role', 'N/A')}")
    logger.info(f"[REQ-{request_id}] Level: {access_context.get('level', 'N/A')}")

    if request.method == 'POST':
        logger.info(f"[REQ-{request_id}] {'─'*80}")
        logger.info(f"[REQ-{request_id}] PHASE 1: CELERY HEALTH CHECK")
        logger.info(f"[REQ-{request_id}] {'─'*80}")

        # ✅ CELERY HEALTH CHECK - Before processing any form data
        from celery import current_app
        from celery.app.control import Inspect
        import time

        health_check_start = time.time()

        try:
            logger.info(f"[REQ-{request_id}] Connecting to Celery broker...")
            logger.debug(f"[REQ-{request_id}] Broker URL: {current_app.conf.broker_url}")
            logger.debug(f"[REQ-{request_id}] Result Backend: {current_app.conf.result_backend}")

            # ✅ Check if Celery is properly installed
            try:
                import celery
                logger.debug(f"[REQ-{request_id}] Celery version: {celery.__version__}")
            except Exception as version_error:
                logger.warning(f"[REQ-{request_id}] Could not determine Celery version: {version_error}")

            # ✅ Test basic Celery connection first
            try:
                logger.info(f"[REQ-{request_id}] Testing Celery app connection...")
                # Simple connection test
                with current_app.connection_or_acquire() as conn:
                    conn.ensure_connection(max_retries=3, timeout=2)
                logger.info(f"[REQ-{request_id}] ✅ Broker connection successful")
            except Exception as conn_error:
                logger.error(f"[REQ-{request_id}] ❌ BROKER CONNECTION FAILED")
                logger.error(f"[REQ-{request_id}] Error: {str(conn_error)}")
                messages.error(
                    request,
                    f"Cannot connect to message broker: {str(conn_error)}. "
                    f"Please ensure Redis/RabbitMQ is running and accessible.",
                    extra_tags="celery broker"
                )
                return redirect('ppm_dashboard')

            i = Inspect(app=current_app)

            logger.info(f"[REQ-{request_id}] Checking worker stats...")

            # ✅ FIX: Remove timeout parameter - use default timeout
            stats = i.stats()

            health_check_duration = time.time() - health_check_start
            logger.info(f"[REQ-{request_id}] Health check completed in {health_check_duration:.2f}s")

            if not stats:
                logger.error(f"[REQ-{request_id}] ❌ CELERY HEALTH CHECK FAILED: No workers available")

                # Try to get more diagnostic info
                try:
                    active = i.active()
                    registered = i.registered()
                    logger.error(f"[REQ-{request_id}] Active tasks: {active}")
                    logger.error(f"[REQ-{request_id}] Registered tasks: {list(registered.keys()) if registered else 'None'}")
                except Exception as diag_error:
                    logger.error(f"[REQ-{request_id}] Could not retrieve diagnostic info: {diag_error}")

                messages.error(
                    request,
                    "No Celery workers are running. Please start a Celery worker with: "
                    "celery -A your_project worker -l info",
                    extra_tags="celery worker"
                )
                return redirect('ppm_dashboard')

            # Log detailed worker information
            logger.info(f"[REQ-{request_id}] ✅ CELERY HEALTH CHECK PASSED")
            logger.info(f"[REQ-{request_id}] Active workers: {len(stats)}")

            for worker_name, worker_stats in stats.items():
                logger.info(f"[REQ-{request_id}]   Worker: {worker_name}")
                logger.debug(f"[REQ-{request_id}]     Pool: {worker_stats.get('pool', {}).get('implementation', 'N/A')}")
                logger.debug(f"[REQ-{request_id}]     Max concurrency: {worker_stats.get('pool', {}).get('max-concurrency', 'N/A')}")
                logger.debug(f"[REQ-{request_id}]     Total tasks: {worker_stats.get('total', {})}")

            # Check active queues and registered tasks
            try:
                active_queues = i.active_queues()
                if active_queues:
                    logger.info(f"[REQ-{request_id}] Active queues detected:")
                    for worker, queues in active_queues.items():
                        logger.info(f"[REQ-{request_id}]   {worker}: {[q['name'] for q in queues]}")

                registered = i.registered()
                if registered:
                    logger.debug(f"[REQ-{request_id}] Registered tasks: {sum(len(tasks) for tasks in registered.values())} total")
            except Exception as queue_error:
                logger.debug(f"[REQ-{request_id}] Could not retrieve queue info: {queue_error}")

        except Exception as e:
            health_check_duration = time.time() - health_check_start
            logger.error(f"[REQ-{request_id}] ❌ CELERY CONNECTION ERROR (after {health_check_duration:.2f}s)")
            logger.error(f"[REQ-{request_id}] Error type: {type(e).__name__}")
            logger.error(f"[REQ-{request_id}] Error message: {str(e)}")
            logger.exception(f"[REQ-{request_id}] Full traceback:")

            # ✅ Provide specific guidance based on error type
            if "ModuleNotFoundError" in str(type(e).__name__) or "No module named" in str(e):
                error_msg = (
                    f"Celery installation is incomplete or corrupted: {str(e)}. "
                    f"Please reinstall Celery with: pip install --force-reinstall celery"
                )
            elif "Connection" in str(type(e).__name__) or "connection" in str(e).lower():
                error_msg = (
                    f"Cannot connect to message broker: {str(e)}. "
                    f"Please ensure Redis/RabbitMQ is running and accessible."
                )
            else:
                error_msg = f"Celery system error: {str(e)}. Please contact administrator."

            messages.error(request, error_msg, extra_tags="celery error")
            return redirect('ppm_dashboard')

        logger.info(f"[REQ-{request_id}] {'─'*80}")
        logger.info(f"[REQ-{request_id}] PHASE 2: FORM DATA PARSING")
        logger.info(f"[REQ-{request_id}] {'─'*80}")

        try:
            # Parse form data with validation
            try:
                planning_logic = request.POST.get('planning_logic', 'department')
                maintenance_period = int(request.POST.get('maintenance_period', 6))
                base_month = int(request.POST.get('base_month', 1))
                base_year = int(request.POST.get('base_year', datetime.today().year))
                max_departments = int(request.POST.get('max_departments', 100))
                max_descriptions = int(request.POST.get('max_descriptions', 100))
                preserve_existing = request.POST.get('preserve_existing') == 'true'
                selected_descriptions = request.POST.getlist('selected_descriptions')

                logger.info(f"[REQ-{request_id}] ✅ Form data parsed successfully")
                logger.info(f"[REQ-{request_id}]   Planning Logic: {planning_logic}")
                logger.info(f"[REQ-{request_id}]   Maintenance Period: {maintenance_period} months")
                logger.info(f"[REQ-{request_id}]   Base Date: {base_month}/{base_year}")
                logger.info(f"[REQ-{request_id}]   Max Departments: {max_departments}")
                logger.info(f"[REQ-{request_id}]   Max Descriptions: {max_descriptions}")
                logger.info(f"[REQ-{request_id}]   Preserve Existing: {preserve_existing}")
                logger.info(f"[REQ-{request_id}]   Selected Descriptions: {len(selected_descriptions)} items")

                if selected_descriptions:
                    logger.debug(f"[REQ-{request_id}]   Description IDs: {selected_descriptions[:10]}{'...' if len(selected_descriptions) > 10 else ''}")

            except (ValueError, TypeError) as e:
                logger.error(f"[REQ-{request_id}] ❌ FORM DATA VALIDATION ERROR")
                logger.error(f"[REQ-{request_id}] Error type: {type(e).__name__}")
                logger.error(f"[REQ-{request_id}] Error message: {str(e)}")
                logger.error(f"[REQ-{request_id}] POST data: {dict(request.POST)}")
                messages.error(request, f"Invalid input data: {str(e)}")
                return redirect('ppm_dashboard')

            logger.info(f"[REQ-{request_id}] {'─'*80}")
            logger.info(f"[REQ-{request_id}] PHASE 3: WORKSHOP & EQUIPMENT VALIDATION")
            logger.info(f"[REQ-{request_id}] {'─'*80}")

            # Verify workshop exists
            try:
                workshop = Workshop.objects.get(id=workshop_id)
                logger.info(f"[REQ-{request_id}] ✅ Workshop verified: '{workshop.name}' (ID: {workshop.id})")
            except Workshop.DoesNotExist:
                logger.error(f"[REQ-{request_id}] ❌ WORKSHOP NOT FOUND: {workshop_id}")
                messages.error(request, "Workshop not found. Please contact administrator.")
                return redirect('ppm_dashboard')

            # Get equipment filter for department-level users
            equipment_filter = None
            if access_context['access_type'] == 'department':
                logger.info(f"[REQ-{request_id}] Applying department-level filter...")
                equipment_filter = list(Equipment.objects.filter(
                    department_id=access_context['department_id'],
                    active_status=True
                ).values_list('id', flat=True))
                logger.info(f"[REQ-{request_id}] ✅ Department filter applied: {len(equipment_filter)} equipment items")
            else:
                logger.info(f"[REQ-{request_id}] Workshop-level access - no department filter")

            # Count active equipment
            if equipment_filter is not None:
                equipment_count = len(equipment_filter)
            else:
                equipment_count = Equipment.objects.filter(
                    workshop_id=workshop_id,
                    active_status=True
                ).count()

            logger.info(f"[REQ-{request_id}] Total active equipment: {equipment_count}")

            if equipment_count == 0:
                logger.warning(f"[REQ-{request_id}] ⚠️  NO ACTIVE EQUIPMENT FOUND")
                messages.warning(request, "No active equipment found to schedule.")
                return redirect('ppm_dashboard')

            # Check if equipment needs scheduling
            if preserve_existing:
                logger.info(f"[REQ-{request_id}] Checking existing schedules (preserve_existing=True)...")

                already_scheduled = PPMSchedule.objects.filter(
                    workshop_id=workshop_id,
                    equipment__active_status=True
                ).count()

                needs_scheduling = equipment_count - already_scheduled

                logger.info(f"[REQ-{request_id}]   Already scheduled: {already_scheduled}")
                logger.info(f"[REQ-{request_id}]   Needs scheduling: {needs_scheduling}")
                logger.info(f"[REQ-{request_id}]   Coverage: {(already_scheduled/equipment_count*100):.1f}%")

                if needs_scheduling <= 0:
                    logger.info(f"[REQ-{request_id}] ✅ All equipment already scheduled - nothing to do")
                    messages.info(request, "All active equipment is already scheduled.")
                    return redirect('ppm_dashboard')

            logger.info(f"[REQ-{request_id}] {'─'*80}")
            logger.info(f"[REQ-{request_id}] PHASE 4: CELERY TASK QUEUING")
            logger.info(f"[REQ-{request_id}] {'─'*80}")

            # Queue the Celery task
            try:
                task_queue_start = time.time()

                task_args = {
                    'workshop_id': str(workshop_id),
                    'planning_logic': planning_logic,
                    'maintenance_period': maintenance_period,
                    'base_month': base_month,
                    'base_year': base_year,
                    'max_departments': max_departments,
                    'max_descriptions': max_descriptions,
                    'selected_descriptions': selected_descriptions,
                    'preserve_existing': preserve_existing,
                    'equipment_filter': equipment_filter
                }

                logger.debug(f"[REQ-{request_id}] Task arguments prepared")
                logger.info(f"[REQ-{request_id}] Queuing task to Celery...")

                task = initialize_ppm_schedule_with_logic.delay(
                    str(workshop_id),
                    planning_logic,
                    maintenance_period,
                    base_month,
                    base_year,
                    max_departments,
                    max_descriptions,
                    selected_descriptions,
                    preserve_existing,
                )

                task_queue_duration = time.time() - task_queue_start
                logger.info(f"[REQ-{request_id}] ✅ TASK QUEUED in {task_queue_duration:.2f}s (ID: {task.id})")

                logger.info(f"[REQ-{request_id}] {'─'*80}")
                logger.info(f"[REQ-{request_id}] REQUEST COMPLETE - Redirecting user to dashboard")
                logger.info(f"[REQ-{request_id}] {'─'*80}")

                messages.success(
                    request,
                    f"PPM schedule initialization started for {workshop.name}! Task ID: {task.id}. "
                    f"The system will create {equipment_count} schedule(s) using {planning_logic} logic. "
                    f"You will receive a notification when the process completes.",
                    extra_tags="schedule success task"
                )

                return redirect('ppm_dashboard')

            except Exception as e:
                logger.error(f"[REQ-{request_id}] ❌ Failed to queue task: {str(e)}", exc_info=True)
                messages.error(
                    request,
                    f"Failed to queue schedule initialization: {str(e)}. "
                    f"Please check that a Celery worker is running.",
                    extra_tags="celery error"
                )
                return redirect('ppm_dashboard')

        except (ValueError, TypeError) as e:
            logger.error(f"[REQ-{request_id}] ❌ FORM DATA VALIDATION ERROR: {e}")
            messages.error(request, f"Invalid input data: {str(e)}")
            return redirect('ppm_dashboard')

    # GET request - just redirect to dashboard
    return redirect('ppm_dashboard')


@login_required
def trigger_smart_reorganize_ppm(request):
    """
    Handle the PPM Smart Reorganizer form submission.
    """
    import uuid as _uuid
    request_id = str(_uuid.uuid4())[:8]
    logger.info(f"[REQ-{request_id}] PPM Smart-reorganize request from user: {request.user.username}")

    if request.method != "POST":
        return redirect("ppm_dashboard")

    access_context = get_user_access_context(request)
    if not access_context or not access_context.get("can_schedule", False):
        messages.error(request, "You don't have permission to reorganize PPM schedules.", extra_tags="permission")
        return redirect("ppm_dashboard")

    try:
        new_planning_logic = request.POST.get("new_planning_logic", "department")
        base_month = int(request.POST.get("base_month", 1))
        max_departments = int(request.POST.get("max_departments", 100))
        max_descriptions = int(request.POST.get("max_descriptions", 100))
        dry_run = request.POST.get("dry_run", "").lower() == "true"

        base_month = max(1, min(12, base_month))
        max_departments = max(1, min(100, max_departments))
        max_descriptions = max(1, min(100, max_descriptions))

        workshop_id = access_context.get("workshop_id")

        logger.info(
            f"[REQ-{request_id}] Smart-reorg params: logic={new_planning_logic}, "
            f"base_month={base_month}, dry_run={dry_run}"
        )
    except (ValueError, TypeError) as exc:
        logger.error(f"[REQ-{request_id}] Invalid form data: {exc}")
        messages.error(request, f"Invalid input data: {exc}", extra_tags="validation")
        return redirect("ppm_dashboard")

    try:
        task = smart_reorganize_ppm_schedules.delay(
            new_planning_logic=new_planning_logic,
            base_month=base_month,
            max_departments=max_departments,
            max_descriptions=max_descriptions,
            workshop_id=str(workshop_id) if workshop_id else None,
            dry_run=dry_run,
        )

        if dry_run:
            messages.info(
                request,
                f"PPM Smart Reorganizer dry run started (Task ID: {task.id}). "
                f"Check logs to preview changes.",
                extra_tags="smart-reorg dry-run",
            )
        else:
            messages.success(
                request,
                f"PPM Smart Reorganizer started (Task ID: {task.id}). "
                f"Schedules are being realigned to '{new_planning_logic}' logic. "
                f"Completed schedules are protected.",
                extra_tags="smart-reorg",
            )
    except Exception as exc:
        logger.error(f"[REQ-{request_id}] Failed to queue smart-reorg task: {exc}")
        messages.error(
            request,
            f"Failed to start Smart Reorganizer: {exc}. Please ensure Celery is running.",
            extra_tags="smart-reorg error",
        )

    return redirect("ppm_dashboard")


def _normalize_ppm_helper(request, request_id):
    # This helper is kept for any future common logic
    pass


# ==================== DEPRECATED ORPHANED CODE (DO NOT EDIT) ====================

    # GET request - redirect to dashboard
    logger.info(f"[REQ-{request_id}] GET request - redirecting to dashboard")
    return redirect('ppm_dashboard')


@login_required
def trigger_sync_initialization(request):
    """
    Synchronous initialization - bypasses Celery for testing
    ONLY use this for debugging!
    """
    if not request.user.is_staff:
        messages.error(request, "Only staff can use sync initialization")
        return redirect('ppm_dashboard')

    access_context = get_user_access_context(request)

    if not access_context or not access_context['workshop_id']:
        messages.error(request, "No workshop access")
        return redirect('ppm_dashboard')

    workshop_id = access_context['workshop_id']

    if request.method == 'POST':
        try:
            planning_logic = request.POST.get('planning_logic', 'department')
            maintenance_period = int(request.POST.get('maintenance_period', 6))
            base_month = int(request.POST.get('base_month', 1))
            base_year = int(request.POST.get('base_year', datetime.today().year))
            max_departments = int(request.POST.get('max_departments', 100))
            max_descriptions = int(request.POST.get('max_descriptions', 100))
            preserve_existing = request.POST.get('preserve_existing') == 'true'

            logger.info(f"=== SYNC PPM Initialization Started ===")
            logger.info(f"User: {request.user.username}")
            logger.info(f"Workshop ID: {workshop_id}")

            # Import the actual task function
            from ppms.tasks import initialize_ppm_schedule_with_logic

            # Create a mock self object for bind parameter
            class MockSelf:
                def update_state(self, state, meta):
                    logger.info(f"Progress: {meta.get('percent', 0)}% - {meta.get('status', '')}")

            # Call directly without Celery
            result = initialize_ppm_schedule_with_logic(
                MockSelf(),
                str(workshop_id),
                planning_logic,
                maintenance_period,
                base_month,
                base_year,
                max_departments,
                max_descriptions,
                [],
                preserve_existing,
                None
            )

            logger.info(f"Sync initialization completed: {result}")

            if result.get('status') == 'success':
                messages.success(request, f"✓ {result.get('message')}")
            else:
                messages.error(request, f"✗ {result.get('message')}")

        except Exception as e:
            logger.error(f"Sync initialization error: {e}", exc_info=True)
            messages.error(request, f"Error: {str(e)}")

        return redirect('ppm_dashboard')

    return redirect('ppm_dashboard')


@login_required
def trigger_normalize_ppm(request):
    """
    Trigger PPM schedule normalization so all equipment in the same
    group (department OR description) lands in the same month.

    Only affects pending/pushed schedules in the active planning year.
    Completed schedules are never touched.
    """
    import uuid as _uuid
    request_id = str(_uuid.uuid4())[:8]
    logger.info(f"[REQ-{request_id}] PPM Normalize request from user: {request.user.username}")

    if request.method != 'POST':
        return redirect('ppm_dashboard')

    access_context = get_user_access_context(request)
    if not access_context or not access_context.get('can_schedule', False):
        messages.error(
            request,
            "You don't have permission to normalize PPM schedules.",
            extra_tags="permission schedule"
        )
        return redirect('ppm_dashboard')

    try:
        planning_logic    = request.POST.get('planning_logic', 'department')
        maintenance_period = int(request.POST.get('maintenance_period', 6))
        base_month        = int(request.POST.get('base_month', 1))
        max_departments   = int(request.POST.get('max_departments', 100))
        max_descriptions  = int(request.POST.get('max_descriptions', 100))

        # Validate
        if planning_logic not in ('department', 'description'):
            planning_logic = 'department'
        if not (1 <= base_month <= 12):
            base_month = 1
        if maintenance_period not in (3, 6, 9, 12):
            maintenance_period = 6

        # Determine which year will be normalized (for user feedback only)
        from datetime import date as _date
        today = _date.today()
        normalized_year = today.year + 1 if today.month > 6 else today.year

        workshop_id = access_context.get('workshop_id')

        logger.info(
            f"[REQ-{request_id}] Normalize params: logic={planning_logic}, "
            f"period={maintenance_period}m, base_month={base_month}, "
            f"year={normalized_year} (auto), workshop={workshop_id}"
        )

    except (ValueError, TypeError) as exc:
        logger.error(f"[REQ-{request_id}] Invalid form data: {exc}")
        messages.error(request, f"Invalid input data: {exc}", extra_tags="validation")
        return redirect('ppm_dashboard')

    try:
        task = normalize_ppm_schedules.delay(
            planning_logic=planning_logic,
            maintenance_period=maintenance_period,
            base_month=base_month,
            max_departments=max_departments,
            max_descriptions=max_descriptions,
            workshop_id=str(workshop_id) if workshop_id else None,
        )
        logger.info(f"[REQ-{request_id}] ✅ Normalize task queued: {task.id}")
        messages.success(
            request,
            f"PPM normalization started for {normalized_year} (auto-selected)! "
            f"Task ID: {task.id}. "
            f"All '{planning_logic}' groups will be aligned to a single month each. "
            f"Completed schedules are protected.",
            extra_tags="schedule normalization task"
        )
    except Exception as exc:
        logger.error(f"[REQ-{request_id}] Failed to queue normalize task: {exc}")
        messages.error(
            request,
            f"Failed to start normalization: {exc}. "
            f"Please check that a Celery worker is running.",
            extra_tags="normalization error"
        )

    return redirect('ppm_dashboard')
