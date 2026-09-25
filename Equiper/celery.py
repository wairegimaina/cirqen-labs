from __future__ import absolute_import, unicode_literals
import os
from celery import Celery
from celery.signals import setup_logging
from celery.schedules import crontab
from celery.schedules import timedelta

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Equiper.settings")

# Create the Celery application instance
app = Celery("Equiper")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix in Django settings.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()


# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================


@setup_logging.connect
def config_loggers(*args, **kwargs):
    """
    Configure logging for Celery.
    This prevents Celery from overriding Django's logging configuration.
    """
    from logging.config import dictConfig
    from django.conf import settings

    if hasattr(settings, "LOGGING"):
        dictConfig(settings.LOGGING)


# ============================================================================
# CELERY BEAT SCHEDULE - AUTOMATIC RECONCILIATION SYSTEM
# ============================================================================

app.conf.beat_schedule = {
    # ============================================================================
    # NOTIFICATIONS
    # ============================================================================
    # Send queued email (offline-safe: failures back off and retry).
    "notifications-flush-outbox": {
        "task": "notifications.tasks.flush_outbox",
        "schedule": timedelta(minutes=1),
        "options": {"expires": 50},
    },
    # Hourly; acts once the EAT hour reaches NOTIFICATIONS_DIGEST_HOUR and only
    # on the machine with notifications.digest_sender set (hourly rather than a
    # fixed crontab so a machine switched on late still sends that day's digest).
    "notifications-daily-digest": {
        "task": "notifications.tasks.send_daily_digests",
        "schedule": crontab(minute=5),
        "options": {"expires": 3000},
    },
    # Local database backup (core.backups). Midday rather than overnight:
    # clinic machines are often switched off at night and beat does not
    # catch up on missed runs.
    "backup-local-database": {
        "task": "core.tasks.backup_local_database",
        "schedule": crontab(hour=12, minute=30),
        "options": {"expires": 6 * 3600},
    },
    # ============================================================================
    # SCHEDULING (PPM and calibration): one planner for both, see scheduling/
    # ============================================================================
    # Every workshop gets a plan; every device without an open schedule gets
    # one; completions that arrived through sync (no signal) get their next.
    "scheduling-run-plans": {
        "task": "scheduling.tasks.run_plans",
        "schedule": 1800.0,  # every 30 minutes
        "options": {"expires": 1500},
    },
    # Open schedules of equipment taken out of service are retired (history kept).
    "scheduling-retire-inactive": {
        "task": "scheduling.tasks.retire_inactive_equipment_schedules",
        "schedule": crontab(hour=3, minute=30),
        "options": {"expires": 3600},
    },
}


# ============================================================================
# CELERY CONFIGURATION
# ============================================================================

app.conf.update(
    # Serialization settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Timezone settings: crontab hours in beat_schedule are East Africa Time
    # (settings.CELERY_TIMEZONE). This used to force "UTC", so every job ran
    # three hours later than written (the 12:30 backup ran at 15:30 EAT).
    timezone="Africa/Nairobi",
    enable_utc=True,
    # Connection settings
    broker_connection_retry_on_startup=True,
    # Task settings
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    # Result backend settings
    result_expires=3600,  # Results expire after 1 hour
    result_backend_transport_options={"master_name": "mymaster"},
    # Worker settings
    worker_prefetch_multiplier=4,
    worker_max_tasks_per_child=1000,
)


# ============================================================================
# DEBUG AND TEST TASKS
# ============================================================================


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """
    Debug task to test Celery configuration.

    Usage:
        from Equiper.celery import debug_task
        debug_task.delay()
    """
    print(f"Request: {self.request!r}")
    return f"Debug task executed: {self.request!r}"


@app.task(bind=True, ignore_result=True)
def test_celery(self):
    """
    Simple test task to verify Celery is working.

    Usage:
        from Equiper.celery import test_celery
        test_celery.delay()
    """
    print("Celery is working correctly!")
    return "Success"


# ============================================================================
# CUSTOM PERIODIC TASKS SETUP
# ============================================================================


@app.on_after_configure.connect
def setup_custom_periodic_tasks(sender, **kwargs):
    """
    Setup additional custom periodic tasks dynamically.

    This is useful for tasks that need to be configured based on runtime conditions
    or for tasks that don't need to be in the main beat_schedule.

    Example:
        # Executes every 10 seconds
        sender.add_periodic_task(10.0, test_celery.s(), name='test every 10s')

        # Executes daily at midnight
        sender.add_periodic_task(
            crontab(hour=0, minute=0),
            test_celery.s(),
            name='test daily at midnight'
        )

        # Executes every Monday morning at 7:30 AM
        sender.add_periodic_task(
            crontab(hour=7, minute=30, day_of_week=1),
            test_celery.s(),
            name='test every monday morning'
        )
    """
    # Example: Add a quick health check every 5 minutes
    # sender.add_periodic_task(300.0, test_celery.s(), name='health-check-every-5m')


# ============================================================================
# TASK ERROR HANDLER
# ============================================================================


@app.task(bind=True, max_retries=3)
def error_handler(self, uuid):
    """
    Handle task errors and retry logic.

    Args:
        uuid: Task ID that failed
    """
    from celery.result import AsyncResult

    result = AsyncResult(uuid, app=app)

    if result.failed():
        print(f"Task {uuid} failed: {result.info}")
        # Add custom error handling logic here
        # e.g., send email notifications, log to external service, etc.


# ============================================================================
# USEFUL CRONTAB EXAMPLES FOR REFERENCE
# ============================================================================
"""
Common crontab patterns:

# Every minute
crontab()

# Every 5 minutes
crontab(minute='*/5')

# Every hour
crontab(minute=0)

# Every day at midnight
crontab(hour=0, minute=0)

# Every Monday at 8:00 AM
crontab(hour=8, minute=0, day_of_week=1)

# First day of every month at 9:00 AM
crontab(hour=9, minute=0, day_of_month=1)

# Every 15 minutes between 8 AM and 5 PM
crontab(minute='*/15', hour='8-17')

# Multiple days
crontab(hour=8, minute=0, day_of_week='1,3,5')  # Mon, Wed, Fri

# Every quarter (every 3 months) on the first day at midnight
crontab(hour=0, minute=0, day_of_month=1, month_of_year='1,4,7,10')
"""
app.conf.beat_schedule.update(
    {
        # ── Autonomous update check ───────────────────────────────────────────────
        # Fires every 6 hours. The task itself checks UpdateSettings.last_check
        # and UpdateSettings.check_interval_hours before doing any real work,
        # so there's no risk of hammering HQ even if you set this more aggressively.
        "updates.autonomous_check": {
            "task": "updates.tasks.check_and_apply_updates",
            "schedule": timedelta(hours=6),
            "options": {"expires": 3600},  # discard if worker is down > 1 h
        },
    }
)
