from .agent_prelude import LOG, now_kenyan, now_utc, now_iso, format_kenyan_time
from .agent_prelude import (
    KENYAN_TZ,
    PerformanceMonitor,
    encrypt_token,
    setup_logging,
    load_agent_config,
)
from .agent_prelude import load_config_from_unified_manager, load_config_from_env_fallback
from .agent_prelude import sleep_with_jitter, is_online, CERT_TABLES, DEFAULT_CONFIG
import json
from typing import Any, Dict, List, Optional, Tuple, Set
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
import hashlib
from cryptography.fernet import Fernet
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import ThreadedConnectionPool
import requests
import pytz
from .state_manager import StateManager
from .dependency_manager import DependencyManager
from .smart_delete import SmartDeleteMixin


class StatusReportingMixin(SmartDeleteMixin):
    """Status file writing and status summary for the UI."""
    def write_status_file(self, hq_online: bool, pending_changes: int = 0, last_sync: str = None):
        """
        Write sync agent status to file for UI monitoring

        Creates/updates agent_status.json with current sync state.
        Uses atomic write to prevent corruption.

        Args:
            hq_online: Whether HQ server is reachable
            pending_changes: Number of pending changes to upload
            last_sync: ISO timestamp of last successful sync

        Status file location: {state_dir}/agent_status.json
        """
        try:
            # self.state.state_dir IS the sync_state directory already
            # (StateManager is constructed with it directly) — do not
            # append another "sync_state" segment here, or this file lands
            # in sync_state/sync_state/ where the UI never looks for it.
            status_dir = self.state.state_dir
            status_dir.mkdir(parents=True, exist_ok=True)

            status_file = status_dir / "agent_status.json"

            # Best-effort counts for the UI (never let these break status writing).
            try:
                unreviewed_conflicts = self.get_conflict_count(unreviewed_only=True)
            except Exception:
                unreviewed_conflicts = 0
            try:
                schema_drift = self.get_schema_drift_count(unresolved_only=True)
            except Exception:
                schema_drift = 0

            # Prepare status data
            status_data = {
                "hq_online": hq_online,
                "pending_changes": pending_changes,
                "unreviewed_conflicts": unreviewed_conflicts,
                "schema_drift": schema_drift,
                "last_sync": last_sync or self.state.get("last_upload_time"),
                "last_update": now_iso(),
                "client_id": self.client_id,
                "machine_id": self.machine_id,
                "version": self.version,
                "tables_monitored": len(self.tables),
            }

            # Atomic write using temporary file
            import tempfile
            import shutil

            try:
                # Create temp file in same directory for atomic move
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    dir=status_file.parent,
                    delete=False,
                    suffix=".tmp",
                    prefix="agent_status_",
                ) as f:
                    json.dump(status_data, f, indent=2)
                    temp_path = f.name

                # Atomic replace
                shutil.move(temp_path, status_file)

                LOG.debug(
                    f"📊 Status updated: HQ={'online' if hq_online else 'offline'}, "
                    f"pending={pending_changes}"
                )

            except Exception as write_error:
                # Clean up temp file if it exists
                try:
                    if "temp_path" in locals() and Path(temp_path).exists():
                        Path(temp_path).unlink()
                except Exception:
                    pass
                raise write_error

        except Exception as e:
            LOG.debug(f"Could not write status file: {e}")

    def get_status_summary(self) -> dict:
        """
        Get current sync agent status summary

        Returns:
            dict: Current status including:
                - hq_online: bool
                - pending_changes: int
                - last_sync: str (ISO timestamp)
                - uptime: float (seconds)
        """
        try:
            status_file = self.state.state_dir / "agent_status.json"

            if status_file.exists():
                with open(status_file, "r") as f:
                    return json.load(f)
            else:
                return {
                    "hq_online": False,
                    "pending_changes": 0,
                    "last_sync": None,
                    "last_update": None,
                    "error": "Status file not found",
                }
        except Exception as e:
            return {"hq_online": False, "pending_changes": 0, "last_sync": None, "error": str(e)}

    def report_critical_failure(self, failure_type: str, message: str):
            """
            Push an immediate "device failure" report to HQ — the "overall
            downtime, not just sync" channel. Unlike the heartbeat/status-file
            path, this is meant to be called at the exact moment a critical
            local failure is detected (e.g. a sync thread permanently
            exhausted its restart budget), not on a timer, so HQ can alert
            (email/webhook) immediately instead of waiting for the ~10 min
            stale-client check cycle to eventually notice the device went
            quiet.

            Best-effort and non-blocking in spirit: any failure here (HQ
            unreachable, timeout, etc.) is logged and swallowed — a broken
            alert channel must never crash whatever code path detected the
            original failure. If HQ can't be reached, the stale-client
            watchdog on the HQ side is the fallback net (slower, but it
            doesn't depend on this call succeeding).
            """
            try:
                payload = {
                    "client_id": getattr(self, "client_id", "unknown"),
                    "machine_id": getattr(self, "machine_id", "unknown"),
                    "client_name": getattr(self, "client_name", ""),
                    "failure_type": failure_type,
                    "message": message,
                }
                requests.post(
                    f"{self.api_url}/report_device_failure",
                    json=payload,
                    headers=self._http_headers(),
                    timeout=10,
                )
                LOG.error("🚨 Reported critical failure to HQ: %s — %s", failure_type, message)
            except Exception as e:
                LOG.debug("Could not report critical failure to HQ (%s): %s", failure_type, e)
