"""
updates/tasks.py
================
Autonomous update checking and auto-apply via Celery beat.

Celery beat schedule (add to settings.py or Equiper/celery.py):

    from celery.schedules import crontab

    CELERY_BEAT_SCHEDULE = {
        'updates.check_for_updates': {
            'task': 'updates.tasks.check_and_apply_updates',
            'schedule': crontab(minute=0, hour='*/6'),  # every 6 hours
            # or use: timedelta(hours=24) for once a day
        },
    }

The task respects UpdateSettings from the database:
  - auto_check_enabled   → skip entirely if False
  - check_interval_hours → won't re-check if last_check is too recent
  - auto_apply_updates   → auto-apply any available update
  - auto_apply_critical  → always auto-apply critical updates
"""

import logging
import queue
import socket
import threading
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from core.eat import fmt_eat

logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _hq_cfg() -> dict:
    cfg = getattr(settings, "UPDATE_SYSTEM", {})
    return {
        "server_url": cfg.get("server_url", "").rstrip("/"),
        "api_key":    cfg.get("api_key", ""),
        "enabled":    cfg.get("enabled", True),
    }


def _machine_id() -> str:
    try:
        from sync.device_id_generator import get_device_id
        return get_device_id()
    except Exception:
        return socket.gethostname()


def _current_version() -> str:
    return getattr(settings, "APP_VERSION", "1.0.0")


# ── main task ─────────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    name="updates.tasks.check_and_apply_updates",
    max_retries=3,
    default_retry_delay=300,   # retry after 5 min on transient failures
    ignore_result=True,
)
def check_and_apply_updates(self):
    """
    Autonomous update check + optional auto-apply.

    Flow:
      1. Load UpdateSettings — bail if checking is disabled.
      2. Honour check_interval_hours — skip if we checked recently.
      3. Hit HQ /api/updates/latest/ to see if a newer version exists.
      4. Record last_check time and save the UpdatePackage locally.
      5. If auto_apply is on (or update is critical + auto_apply_critical),
         download, validate, backup, apply files, run migrations, signal restart.
      6. On transient network errors → retry up to 3 times.
    """
    from .models import ClientMachine, UpdateHistory, UpdatePackage, UpdateSettings
    from .updater import Updater

    # ── 1. load settings ──────────────────────────────────────────────────────
    us = UpdateSettings.get_settings()

    if not us.auto_check_enabled:
        logger.info("[updates] Auto-check disabled in UpdateSettings — skipping.")
        return

    cfg = _hq_cfg()
    if not cfg["server_url"] or not cfg["api_key"]:
        logger.warning(
            "[updates] UPDATE_SYSTEM not configured (server_url / api_key missing). "
            "Add them to settings.py."
        )
        return

    # ── 2. honour check_interval_hours ────────────────────────────────────────
    if us.last_check:
        next_check = us.last_check + timedelta(hours=us.check_interval_hours)
        if timezone.now() < next_check:
            logger.info(
                "[updates] Skipping — last checked %s, next check at %s.",
                fmt_eat(us.last_check),
                fmt_eat(next_check),
            )
            return

    current  = _current_version()
    machine  = _machine_id()
    headers  = {"X-Api-Key": cfg["api_key"]}

    logger.info(
        "[updates] Checking for updates (current=%s, machine=%s) …", current, machine
    )

    # ── 3. hit HQ /api/updates/latest/ ───────────────────────────────────────
    try:
        resp = requests.get(
            f"{cfg['server_url']}/api/updates/latest/",
            params={"current_version": current, "machine_id": machine},
            headers=headers,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

    except requests.ConnectionError as exc:
        logger.warning("[updates] HQ unreachable: %s — will retry.", exc)
        raise self.retry(exc=exc)
    except requests.Timeout as exc:
        logger.warning("[updates] HQ timed out — will retry.")
        raise self.retry(exc=exc)
    except requests.HTTPError as exc:
        logger.error("[updates] HQ returned HTTP error: %s", exc)
        return
    except Exception as exc:
        logger.exception("[updates] Unexpected error during check: %s", exc)
        return

    # ── 4. record last_check + update machine record ──────────────────────────
    us.last_check = timezone.now()
    us.save(update_fields=["last_check"])

    ClientMachine.objects.update_or_create(
        machine_id=machine,
        defaults={
            "current_version": current,
            "last_check":      timezone.now(),
            "hostname":        machine,
        },
    )

    if not data.get("update_available"):
        logger.info("[updates] ✅ Machine is up to date (v%s).", current)
        return

    # ── 5. new version available ──────────────────────────────────────────────
    version      = data["version"]
    is_critical  = data.get("critical", False)
    download_url = data.get("download_url", "")
    checksum     = data.get("checksum", "")

    logger.info(
        "[updates] 🆕 Update available: v%s (critical=%s, size=%s bytes).",
        version, is_critical, data.get("size_bytes", "?"),
    )

    # Save package record so admins can see it in the dashboard even if
    # auto-apply is off.
    pkg, created = UpdatePackage.objects.update_or_create(
        version=version,
        defaults={
            "changes":       data.get("changes", ""),
            "critical":      is_critical,
            "size_bytes":    data.get("size_bytes", 0),
            "checksum":      checksum,
            "package_path":  download_url,
            "min_version":   data.get("min_version", "0.0.0"),
            "manifest_data": data,
            "source":        "hq_server",
            "is_active":     True,
            "fetched_at":    timezone.now(),
            "file_count":    data.get("file_count", 0),
        },
    )

    if created:
        logger.info("[updates] Package v%s saved to local DB.", version)

    # ── decide whether to auto-apply ──────────────────────────────────────────
    should_apply = us.auto_apply_updates or (is_critical and us.auto_apply_critical)

    if not should_apply:
        logger.info(
            "[updates] Update v%s is available but auto_apply is off. "
            "Visit /updates/ to apply manually.",
            version,
        )
        return

    # Check if an update is already in progress (guard against overlapping tasks)
    already_running = UpdateHistory.objects.filter(
        package__version=version,
        status__in=["downloading", "applying"],
    ).exists()

    if already_running:
        logger.info(
            "[updates] Update v%s is already being applied — skipping.", version
        )
        return

    # ── 6. apply the update ───────────────────────────────────────────────────
    logger.info("[updates] 🚀 Auto-applying update v%s …", version)

    history = UpdateHistory.objects.create(
        package=pkg,
        machine_id=machine,
        client_id=machine,
        status="downloading",
        auto_applied=True,
    )

    progress_q = queue.Queue()

    def _log_progress():
        """Drain the progress queue and write to logger."""
        while True:
            try:
                msg = progress_q.get(timeout=60)
                event   = msg["event"]
                message = msg["data"].get("message", "")
                pct     = msg["data"].get("progress", "")
                if pct != "":
                    logger.info("[updates][%s] %s (%s%%)", event, message, pct)
                else:
                    logger.info("[updates][%s] %s", event, message)
                if event in ("complete", "error"):
                    break
            except queue.Empty:
                break

    log_thread = threading.Thread(target=_log_progress, daemon=True)
    log_thread.start()

    updater = Updater(
        package_url=download_url,
        version=version,
        progress_queue=progress_q,
        expected_checksum=checksum,
        is_local_file=False,
        download_headers=headers,
    )

    try:
        updater.run()
        log_thread.join(timeout=10)

        history.mark_success()

        # Update machine's version in local DB
        ClientMachine.objects.filter(machine_id=machine).update(
            current_version=version,
            last_update=timezone.now(),
        )

        us_fresh = UpdateSettings.get_settings()
        us_fresh.last_successful_update = timezone.now()
        us_fresh.save(update_fields=["last_successful_update"])

        logger.info(
            "[updates] ✅ v%s applied successfully. "
            "A restart is required to activate the new code.",
            version,
        )

        # Report success back to HQ (best-effort)
        try:
            requests.post(
                f"{cfg['server_url']}/api/updates/report/",
                json={
                    "machine_id": machine,
                    "version":    version,
                    "status":     "success",
                },
                headers=headers,
                timeout=5,
            )
        except Exception:
            pass

    except Exception as exc:
        log_thread.join(timeout=5)
        history.mark_failed(str(exc))
        logger.error("[updates] ❌ Auto-apply of v%s failed: %s", version, exc)
