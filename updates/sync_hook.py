"""
updates/sync_hook.py
====================
Plugs into the existing sync agent so that every machine automatically
checks for — and optionally applies — updates the moment it reconnects
to the HQ server.

HOW TO WIRE IT IN (one-time edit to sync_agent.py)
---------------------------------------------------
Find the heartbeat section of your sync agent and add 3 lines:

    # In your heartbeat/reconnect handler, after a successful HQ ping:
    from updates.sync_hook import UpdateSyncHook
    hook = UpdateSyncHook.get_instance()
    hook.on_hq_connected(client_id=self.client_id)

That's it. Everything else is automatic.

What happens on each HQ reconnect
----------------------------------
1. Machine reports its current version to HQ  (/api/updates/checkin/)
2. HQ responds with whether an update is available for this machine
3. If update available AND auto_apply is True   → applies silently in background
4. If update available AND auto_apply is False  → shows banner in Django UI
5. HQ records machine as "seen" with its version (for the broadcast dashboard)

Staged rollout support
-----------------------
HQ can target specific machine IDs or a percentage. Machines not in the
current rollout group will get update_available=False even if a newer
version exists.
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Sentinel file written by main.py watcher to detect new version
BASE_DIR: Path = settings.BASE_DIR
SENTINEL_FILE = BASE_DIR / ".restart_required"
VERSION_FILE = BASE_DIR / ".current_version"


def _current_version() -> str:
    """Read from .current_version file first, then settings."""
    if VERSION_FILE.exists():
        v = VERSION_FILE.read_text().strip()
        if v:
            return v
    return getattr(settings, "APP_VERSION", "1.0.0")


def _update_cfg() -> dict:
    cfg = getattr(settings, "UPDATE_SYSTEM", {})
    return {
        "server_url": cfg.get("server_url", "").rstrip("/"),
        "api_key": cfg.get("api_key", ""),
        "auto_apply": cfg.get("auto_apply_updates", False),
        "auto_apply_critical": cfg.get("auto_apply_critical", True),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Redis notification helper (optional but preferred)
# Lets the Django views/templates show a live "update available" toast
# without polling.
# ─────────────────────────────────────────────────────────────────────────────

def _notify_via_redis(event: str, data: dict):
    """Publish update events to Redis so the Django UI can display them."""
    try:
        import redis as redis_lib
        r = redis_lib.Redis(
            host=settings.REDIS_HOST if hasattr(settings, "REDIS_HOST") else "127.0.0.1",
            port=int(getattr(settings, "REDIS_PORT", 7788)),
            decode_responses=True,
        )
        payload = json.dumps({"event": event, "data": data, "ts": datetime.utcnow().isoformat()})
        r.publish("equiper:updates", payload)
        # Also set a key so late-joiners can read it
        r.setex(f"equiper:update:{event}", 3600, payload)
    except Exception as e:
        logger.debug("Redis notify failed (non-critical): %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Main hook class
# ─────────────────────────────────────────────────────────────────────────────

class UpdateSyncHook:
    """
    Singleton that the sync agent calls on every successful HQ connection.

    Thread-safe. Won't double-apply if called rapidly.
    """

    _instance: Optional["UpdateSyncHook"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._applying = threading.Event()
        self._last_check: Optional[datetime] = None
        self._min_check_gap = 300  # don't re-check within 5 minutes

    @classmethod
    def get_instance(cls) -> "UpdateSyncHook":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── called by sync agent ────────────────────────────────────────────────

    def on_hq_connected(self, client_id: str):
        """
        Call this every time the sync agent successfully reaches HQ.
        Non-blocking — spawns a background thread.
        """
        now = datetime.utcnow()

        # Throttle: skip if we just checked recently
        if self._last_check and (now - self._last_check).seconds < self._min_check_gap:
            return

        # Skip if already applying
        if self._applying.is_set():
            logger.debug("UpdateSyncHook: update already in progress, skipping")
            return

        self._last_check = now
        threading.Thread(
            target=self._check_and_handle,
            args=(client_id,),
            daemon=True,
            name="update-sync-hook",
        ).start()

    # ── internal ─────────────────────────────────────────────────────────────

    def _check_and_handle(self, client_id: str):
        cfg = _update_cfg()
        server_url = cfg["server_url"]

        if not server_url:
            return

        current = _current_version()
        logger.info("UpdateSyncHook: checking for updates (current=%s, machine=%s)", current, client_id)

        try:
            resp = requests.post(
                f"{server_url}/api/updates/checkin/",
                json={
                    "client_id": client_id,
                    "current_version": current,
                    "hostname": _hostname(),
                    "checked_at": datetime.utcnow().isoformat(),
                },
                headers={"X-Api-Key": cfg["api_key"]},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

        except requests.ConnectionError:
            logger.debug("UpdateSyncHook: HQ not reachable for update check")
            return
        except Exception as exc:
            logger.warning("UpdateSyncHook: check-in failed: %s", exc)
            return

        if not data.get("update_available"):
            logger.info("UpdateSyncHook: machine is up to date")
            return

        version = data["version"]
        is_critical = data.get("critical", False)
        logger.info("UpdateSyncHook: update available → v%s (critical=%s)", version, is_critical)

        # Notify the Django UI via Redis (shows a toast/banner)
        _notify_via_redis("update_available", {
            "version": version,
            "changes": data.get("changes", ""),
            "critical": is_critical,
            "size_mb": round(data.get("size_bytes", 0) / 1048576, 1),
        })

        # Decide whether to auto-apply
        should_apply = cfg["auto_apply"] or (is_critical and cfg["auto_apply_critical"])

        if should_apply:
            self._apply(data, client_id, cfg)
        else:
            logger.info(
                "UpdateSyncHook: update v%s available but auto_apply=False. "
                "User will see banner at /updates/",
                version,
            )

    def _apply(self, update_info: dict, client_id: str, cfg: dict):
        """Apply the update in the background."""
        if self._applying.is_set():
            return

        self._applying.set()

        try:
            version = update_info["version"]
            logger.info("UpdateSyncHook: auto-applying v%s …", version)

            _notify_via_redis("applying", {"version": version, "message": "Auto-applying update…"})

            import queue
            from updates.models import UpdatePackage, UpdateHistory
            from updates.updater import Updater

            # Ensure package is recorded in local DB
            pkg, _ = UpdatePackage.objects.update_or_create(
                version=version,
                defaults={
                    "changes": update_info.get("changes", ""),
                    "critical": update_info.get("critical", False),
                    "size_bytes": update_info.get("size_bytes", 0),
                    "checksum": update_info.get("checksum", ""),
                    "package_path": update_info.get("download_url", ""),
                    "min_version": update_info.get("min_version", "0.0.0"),
                    "manifest_data": update_info,
                    "is_active": True,
                },
            )

            history = UpdateHistory.objects.create(
                package=pkg,
                machine_id=client_id,
                client_id=client_id,
                status="downloading",
                auto_applied=True,
            )

            progress_q: queue.Queue = queue.Queue()

            def _log_progress():
                """Drain progress queue and log + notify Redis."""
                while True:
                    try:
                        msg = progress_q.get(timeout=60)
                        logger.info("UpdateSyncHook[%s]: [%s] %s", version, msg["event"], msg["data"].get("message", ""))
                        _notify_via_redis(msg["event"], msg["data"])
                        if msg["event"] in ("complete", "error"):
                            break
                    except queue.Empty:
                        break

            updater = Updater(
                package_url=pkg.package_path,
                version=version,
                progress_queue=progress_q,
                expected_checksum=pkg.checksum,
            )

            log_thread = threading.Thread(target=_log_progress, daemon=True)
            log_thread.start()

            updater.run()
            log_thread.join(timeout=5)

            history.mark_success()

            # Report success back to HQ so it updates the dashboard
            try:
                requests.post(
                    f"{cfg['server_url']}/api/updates/report/",
                    json={
                        "client_id": client_id,
                        "version": version,
                        "status": "success",
                        "applied_at": datetime.utcnow().isoformat(),
                    },
                    headers={"X-Api-Key": cfg["api_key"]},
                    timeout=5,
                )
            except Exception:
                pass  # Non-critical - will be recorded on next heartbeat

        except Exception as exc:
            logger.exception("UpdateSyncHook: auto-apply failed: %s", exc)
            _notify_via_redis("error", {"message": str(exc), "version": update_info.get("version", "?")})
        finally:
            self._applying.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: patch into sync agent with minimum code
# ─────────────────────────────────────────────────────────────────────────────

def install_into_sync_agent(sync_agent_instance):
    """
    Alternative to manually editing sync_agent.py.
    Call this after creating your sync agent:

        from updates.sync_hook import install_into_sync_agent
        install_into_sync_agent(my_agent)

    This monkey-patches the heartbeat method to also call the update hook.
    """
    hook = UpdateSyncHook.get_instance()
    original_heartbeat = getattr(sync_agent_instance, "send_heartbeat", None)

    if original_heartbeat is None:
        logger.warning("Could not find send_heartbeat on sync agent — hook not installed")
        return

    def patched_heartbeat(*args, **kwargs):
        result = original_heartbeat(*args, **kwargs)
        # Only fire hook if heartbeat succeeded (result is truthy / no exception)
        if result is not False:
            client_id = getattr(sync_agent_instance, "client_id", "unknown")
            hook.on_hq_connected(client_id=client_id)
        return result

    sync_agent_instance.send_heartbeat = patched_heartbeat
    logger.info("UpdateSyncHook: installed into sync agent heartbeat")


def _hostname() -> str:
    import socket
    return socket.gethostname()
