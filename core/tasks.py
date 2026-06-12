# tasks.py
from celery import shared_task
from celery.exceptions import Retry
from django.core.management import call_command
from django.core.mail import mail_admins
from django.conf import settings
from django.utils import timezone
import logging
import io
import sys
from contextlib import redirect_stdout, redirect_stderr

logger = logging.getLogger(__name__)

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 2, 'countdown': 300},  # Retry twice, wait 5 minutes
    soft_time_limit=3600,  # 1 hour soft limit
    time_limit=7200,      # 2 hour hard limit
)
def run_daily_cleanup(self, days=30, max_records=50000):
    """
    Delete records older than 30 days with safety limits.
    
    Args:
        days: Records older than this will be deleted (default: 30)
        max_records: Safety limit for maximum records to delete
    """
    task_name = f"Daily {days}-day cleanup"
    logger.info(f"Starting {task_name} task (Task ID: {self.request.id})")
    
    # Capture command output
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    
    try:
        start_time = timezone.now()
        
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            call_command(
                "cleanup_old_records",
                days=days,
                max_records=max_records,
                batch_size=1000,
                force=True,  # Skip interactive prompts in automated runs
                delay=0.2,   # Slight delay to reduce DB load
            )
        
        end_time = timezone.now()
        duration = (end_time - start_time).total_seconds()
        
        # Log success
        stdout_content = stdout_capture.getvalue()
        logger.info(f"{task_name} completed successfully in {duration:.2f} seconds")
        logger.info(f"Command output: {stdout_content}")
        
        # Send success notification for significant operations (only in production)
        if "records deleted" in stdout_content and not settings.DEBUG:
            mail_admins(
                subject=f"{task_name} Completed Successfully",
                message=f"""
Task completed at: {end_time}
Duration: {duration:.2f} seconds
Results: {stdout_content}

Task ID: {self.request.id}
                """.strip()
            )
        
        return {
            'status': 'success',
            'duration': duration,
            'output': stdout_content,
            'task_id': str(self.request.id)
        }
        
    except Exception as exc:
        end_time = timezone.now()
        duration = (end_time - start_time).total_seconds() if 'start_time' in locals() else 0
        
        error_output = stderr_capture.getvalue()
        error_msg = f"{task_name} failed after {duration:.2f} seconds: {str(exc)}"
        
        logger.error(error_msg)
        if error_output:
            logger.error(f"Command stderr: {error_output}")
        
        # Send failure notification
        mail_admins(
            subject=f"{task_name} FAILED",
            message=f"""
Task failed at: {end_time}
Duration: {duration:.2f} seconds
Error: {str(exc)}
Stderr: {error_output}

Task ID: {self.request.id}
Retry attempt: {self.request.retries + 1}/3
                """.strip()
        )
        
        # Re-raise for Celery retry mechanism
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 1, 'countdown': 600},  # Retry once, wait 10 minutes
    soft_time_limit=7200,   # 2 hour soft limit
    time_limit=10800,      # 3 hour hard limit
)
def run_annual_cleanup(self, years=1, max_records=100000):
    """
    Delete records older than 1 year (annual cleanup) with higher safety limits.
    
    Args:
        years: Records older than this will be deleted (default: 1)
        max_records: Safety limit for maximum records to delete
    """
    task_name = f"Annual {years}-year cleanup"
    logger.info(f"Starting {task_name} task (Task ID: {self.request.id})")
    
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    
    try:
        start_time = timezone.now()
        
        # First do a dry run to check what would be deleted
        logger.info("Running dry-run first to estimate impact...")
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            call_command(
                "cleanup_old_records",
                years=years,
                dry_run=True,
                batch_size=2000,
            )
        
        dry_run_output = stdout_capture.getvalue()
        logger.info(f"Dry run results: {dry_run_output}")
        
        # Parse dry run to check if we're within limits
        if "Would delete" in dry_run_output:
            # Extract number if possible for validation
            import re
            match = re.search(r'Would delete (\d+) records', dry_run_output)
            if match:
                estimated_records = int(match.group(1))
                if estimated_records > max_records:
                    raise ValueError(
                        f"Estimated {estimated_records} records exceeds "
                        f"safety limit of {max_records}"
                    )
        
        # Now run the actual cleanup
        stdout_capture = io.StringIO()  # Reset capture
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            call_command(
                "cleanup_old_records",
                years=years,
                max_records=max_records,
                batch_size=2000,
                force=True,
                delay=0.5,  # Longer delay for annual cleanup
            )
        
        end_time = timezone.now()
        duration = (end_time - start_time).total_seconds()
        
        stdout_content = stdout_capture.getvalue()
        logger.info(f"{task_name} completed successfully in {duration:.2f} seconds")
        logger.info(f"Command output: {stdout_content}")
        
        # Always send notification for annual cleanup
        mail_admins(
            subject=f"{task_name} Completed",
            message=f"""
Task completed at: {end_time}
Duration: {duration:.2f} seconds

Dry run results:
{dry_run_output}

Actual results:
{stdout_content}

Task ID: {self.request.id}
            """.strip()
        )
        
        return {
            'status': 'success',
            'duration': duration,
            'dry_run_output': dry_run_output,
            'output': stdout_content,
            'task_id': str(self.request.id)
        }
        
    except Exception as exc:
        end_time = timezone.now()
        duration = (end_time - start_time).total_seconds() if 'start_time' in locals() else 0
        
        error_output = stderr_capture.getvalue()
        error_msg = f"{task_name} failed after {duration:.2f} seconds: {str(exc)}"
        
        logger.error(error_msg)
        if error_output:
            logger.error(f"Command stderr: {error_output}")
        
        # Send failure notification
        mail_admins(
            subject=f"{task_name} FAILED - URGENT",
            message=f"""
ANNUAL CLEANUP TASK FAILED - This requires immediate attention.

Task failed at: {end_time}
Duration: {duration:.2f} seconds
Error: {str(exc)}
Stderr: {error_output}

Task ID: {self.request.id}
Retry attempt: {self.request.retries + 1}/2
            """.strip()
        )
        
        raise self.retry(exc=exc)


# celery_settings.py or add to your settings.py
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    # Daily cleanup - runs every day at 2 AM (off-peak hours)
    "daily-cleanup-30days": {
        "task": "core.tasks.run_daily_cleanup",
        "schedule": crontab(hour=2, minute=0),  # 02:00 UTC daily
        "kwargs": {
            "days": 30, 
            "max_records": 50000
        },
        "options": {
            "expires": 3600,  # Task expires after 1 hour if not picked up
            "priority": 3,    # Lower priority than critical tasks
        },
    },
    
    # Annual cleanup - runs January 1st at 3 AM
    "annual-cleanup-1year": {
        "task": "core.tasks.run_annual_cleanup",
        "schedule": crontab(hour=3, minute=0, day_of_month=1, month_of_year=1),  # New Year's Day
        "kwargs": {
            "years": 1,
            "max_records": 100000
        },
        "options": {
            "expires": 14400,  # 4 hour expiry
            "priority": 4,     # Lower priority
        },
    },
}


