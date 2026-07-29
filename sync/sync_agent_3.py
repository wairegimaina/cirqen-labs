from .agent_prelude import LOG, now_kenyan, now_utc, now_iso, format_kenyan_time
from .agent_prelude import KENYAN_TZ, PerformanceMonitor, encrypt_token, setup_logging, load_agent_config
from .agent_prelude import load_config_from_unified_manager, load_config_from_env_fallback
from .agent_prelude import sleep_with_jitter, is_online, CERT_TABLES, DEFAULT_CONFIG
import uuid
import traceback
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

class UploadMixin(SmartDeleteMixin):
    """Upload path: event construction, upload_batch (with idempotency + backpressure), and upload/download checkpoints."""
    def _is_record_soft_deleted(self, row: dict, has_pending_delete: bool,
                                    has_active_status: bool, has_deleted_at: bool) -> bool:
            """Check if record is soft deleted"""
            # Check pending_delete flag
            if has_pending_delete:
                val = row.get('pending_delete')
                if val in (True, 'Y', '1', 't', 'true', 1):
                    return True

            # Check active_status flag
            if has_active_status:
                val = row.get('active_status')
                if val in (False, 'N', '0', 'f', 'false', 0):
                    return True

            # Check deleted_at timestamp
            if has_deleted_at:
                val = row.get('deleted_at')
                if val is not None:
                    return True

            return False
    def _create_soft_delete_event(self, row: dict, row_id: str, table: str,
                                    timestamp: datetime, conn, metrics: dict) -> dict:
            """Create event for soft deleted record"""
            has_deps = self.check_local_dependencies(conn, table, row_id)

            if has_deps:
                metrics['deactivations'] += 1
                LOG.info(f"   🟡 Deactivate (has deps): {row_id}")

                return {
                    "event_id": str(uuid.uuid4()),
                    "table": table,
                    "row_id": row_id,
                    "operation": "deactivate",
                    "data": row["row_data"],
                    "created_at": timestamp.isoformat(),
                    "source": "local",
                    "machine_id": self.machine_id,
                    "active_status": False,
                    "pending_delete": True,
                    "has_dependencies": True
                }
            else:
                metrics['hard_deletes'] += 1
                LOG.info(f"   🔴 Hard delete (no deps): {row_id}")

                return {
                    "event_id": str(uuid.uuid4()),
                    "table": table,
                    "row_id": row_id,
                    "operation": "d",
                    "data": {"id": row_id},
                    "created_at": timestamp.isoformat(),
                    "source": "local",
                    "machine_id": self.machine_id,
                    "has_dependencies": False
                }
    def _create_update_event(self, row: dict, row_id: str, table: str,
                                timestamp: datetime, has_pending_delete: bool,
                                has_active_status: bool, metrics: dict) -> dict:
            """Create event for active record update"""
            metrics['active_updates'] += 1
            LOG.debug(f"   ✅ Active update: {row_id}")

            event = {
                "event_id": str(uuid.uuid4()),
                "table": table,
                "row_id": row_id,
                "operation": "u",
                "data": row["row_data"],
                "created_at": timestamp.isoformat(),
                "source": "local",
                "machine_id": self.machine_id
            }

            # Include status fields
            if has_active_status:
                event["active_status"] = row.get("active_status")
            if has_pending_delete:
                event["pending_delete"] = row.get("pending_delete")

            return event
    def _log_change_summary(self, table: str, metrics: dict, query_time: float):
            """Log comprehensive change summary"""
            total_changes = (metrics['active_updates'] + metrics['soft_deletes'] +
                            metrics['hard_deletes'])

            if total_changes > 0:
                LOG.info(f"✅ {table} summary ({query_time:.2f}s):")
                LOG.info(f"   • {metrics['active_updates']} active updates")
                LOG.info(f"   • {metrics['deactivations']} deactivations (with deps)")
                LOG.info(f"   • {metrics['hard_deletes']} hard deletes (no deps)")

                if metrics['restored_then_deleted'] > 0:
                    LOG.info(f"   • {metrics['restored_then_deleted']} re-deletes ✨")
                if metrics['skipped_synced'] > 0:
                    LOG.debug(f"   • {metrics['skipped_synced']} skipped (already synced)")
            else:
                LOG.debug(f"ℹ️ {table}: No changes detected")
    def upload_batch(self, events: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
            """
            ✅ ENHANCED: Upload with better logging to show what's being uploaded
            """
            if not events:
                LOG.debug("📭 No events to upload")
                return True, None

            # Categorize events
            delete_events = [ev for ev in events if ev.get("operation") == "d"]
            status_events = [ev for ev in events if ev.get("operation") in ["activate", "deactivate"]]
            update_events = [ev for ev in events if ev.get("operation") == "u"]

            LOG.info(f"📤 Uploading batch of {len(events)} events:")
            if update_events:
                LOG.info(f"   • {len(update_events)} regular updates")
            if delete_events:
                LOG.info(f"   • {len(delete_events)} hard deletes")
            if status_events:
                LOG.info(f"   • {len(status_events)} status changes")

            url = f"{self.api_url}/upload"
            # Batch-level idempotency key: deterministic over the events' own
            # idempotency keys, so a retried identical batch carries the same key
            # and HQ can treat it as a no-op (safe at-least-once delivery).
            _keys = ",".join(
                sorted(str(e.get("idempotency_key") or e.get("event_id", "")) for e in events)
            )
            batch_key = hashlib.sha256(_keys.encode("utf-8")).hexdigest() if _keys else None
            data = {
                "events": events,
                "client_id": self.client_id,
                "machine_id": self.machine_id,
                "idempotency_key": batch_key,
            }
            headers = self._http_headers()

            try:
                r = requests.post(url, json=data, headers=headers, timeout=30)

                # Backpressure: honor throttling / server-busy so we back off
                # instead of hammering a struggling HQ.
                if r.status_code in (429, 503):
                    retry_after = r.headers.get("Retry-After")
                    LOG.warning("⏳ HQ throttling upload (%s)%s — backing off",
                                r.status_code,
                                f", Retry-After={retry_after}s" if retry_after else "")
                    return False, f"throttled:{r.status_code}:{retry_after or ''}"

                if r.status_code == 200:
                    LOG.info("✅ Batch uploaded successfully")
                    resp_json = r.json()

                    # ✅ CRITICAL: Update last_upload_time to the LATEST event timestamp
                    if events:
                        latest_event_time = max(
                            event.get("created_at", event.get("updated_at", ""))
                            for event in events
                        )
                        if latest_event_time:
                            self.set_last_upload_time(latest_event_time)
                            LOG.info(f"   📅 Updated checkpoint to: {latest_event_time}")

                    deferred_count = resp_json.get("deferred", 0)
                    if deferred_count > 0:
                        LOG.warning(f"   ⏸️ Server deferred {deferred_count} events (missing parent records)")

                    return True, None
                else:
                    error_msg = f"Upload failed: {r.status_code} - {r.text}"
                    LOG.warning(error_msg)
                    return False, error_msg

            except requests.RequestException as e:
                error_msg = f"Upload request failed: {str(e)}"
                LOG.warning(error_msg)
                return False, error_msg
            except Exception as e:
                error_msg = f"Unexpected upload error: {str(e)}"
                LOG.exception(error_msg)
                return False, error_msg
    def discover_recent_changes(self) -> List[Dict[str, Any]]:
            """
            ✅ ENHANCED: Discover all types of changes with better logging

            Scans for:
            1. Regular updates/inserts
            2. Soft deletes (with dependency checks)
            3. Restores (from audit_log)
            """
            last_upload_time = self.get_last_upload_time()

            pass

            all_events = []
            table_summary = {}

            for table in self.tables:
                LOG.debug(f"📋 Checking {table}...")

                # Get regular updates/inserts (includes soft deletes)
                table_events = self.fetch_recent_changes_for_table(table, last_upload_time)

                # Get restore events (from audit log)
                restore_events = self.detect_local_restores(table, last_upload_time)

                total_for_table = len(table_events) + len(restore_events)

                if total_for_table > 0:
                    # Categorize events for this table
                    updates = sum(1 for e in table_events if e.get("operation") == "u")
                    deletes = sum(1 for e in table_events if e.get("operation") == "d")
                    deactivates = sum(1 for e in table_events if e.get("operation") == "deactivate")

                    table_summary[table] = {
                        'updates': updates,
                        'deletes': deletes,
                        'deactivates': deactivates,
                        'restores': len(restore_events),
                        'total': total_for_table
                    }

                    summary_parts = []
                    if updates > 0:
                        summary_parts.append(f"{updates} updates")
                    if deletes > 0:
                        summary_parts.append(f"{deletes} deletes")
                    if deactivates > 0:
                        summary_parts.append(f"{deactivates} deactivates")
                    if len(restore_events) > 0:
                        summary_parts.append(f"{len(restore_events)} restores")

                    LOG.info(f"   ✅ {table}: {', '.join(summary_parts)}")

                all_events.extend(table_events)
                all_events.extend(restore_events)

            # Overall summary

            if all_events:
                delete_count = sum(1 for e in all_events if e.get("operation") == "d")
                restore_count = sum(1 for e in all_events if e.get("operation") == "activate")
                deactivate_count = sum(1 for e in all_events if e.get("operation") == "deactivate")
                update_count = len(all_events) - delete_count - restore_count - deactivate_count

                LOG.warning("=" * 80)
                LOG.warning(f"📊 CHANGES DETECTED: {len(all_events)} total")
                LOG.warning(f"   • Updates: {update_count}")
                if delete_count > 0:
                    LOG.warning(f"   • Deletes: {delete_count}")
                if deactivate_count > 0:
                    LOG.warning(f"   • Deactivates: {deactivate_count}")
                if restore_count > 0:
                    LOG.warning(f"   • Restores: {restore_count}")
                LOG.warning("=" * 80)

            return all_events
    def get_last_upload_time(self) -> str:
            """
            Get last upload checkpoint.

            If no checkpoint exists, use epoch so the upload loop scans ALL local
            records and pushes them to HQ — not just the last hour.
            """
            last_time = self.state.get("last_upload_time")

            if last_time:
                LOG.debug(f"📅 Last upload time from state: {last_time}")
                return last_time

            LOG.warning("⚠️  No upload checkpoint found — scanning ALL local records (epoch baseline)")
            return "1970-01-01T00:00:00+00:00"
    def set_last_upload_time(self, timestamp: str = None):
            """
            ✅ ENHANCED: Update last upload time with verification
            """
            ts = timestamp or now_iso()
            self.state.set("last_upload_time", ts)
            LOG.info(f"✅ Updated last_upload_time: {ts}")

            # Verify it was saved
            saved_ts = self.state.get("last_upload_time")
            if saved_ts != ts:
                LOG.error(f"❌ Failed to save last_upload_time! Got: {saved_ts}")
            else:
                LOG.debug(f"   ✅ Verified: timestamp saved correctly")
    def set_last_download_time(self, timestamp: str = None):
            """Update last download timestamp with dual persistence"""
            ts = timestamp or now_iso()
            self.state.set("last_download_time", ts)
            LOG.debug("Updated last_download_time: %s", ts)
    def _http_headers(self) -> Dict[str, str]:
            """Generate HTTP headers for API requests"""
            headers = {
                "Content-Type": "application/json",
                "User-Agent": f"CMMS-Sync-Agent/4.1.0 (Client-ID: {self.client_id})"
            }
            if self.auth_token:
                # ✅ FIXED: Use X-API-Key header (server expects this)
                headers["X-API-Key"] = self.auth_token
            # Keep HQ upload/download/event routing tied to this machine even when
            # request bodies are retried or queued.
            headers["X-Client-ID"] = self.client_id
            headers["X-Device-ID"] = self.client_id
            return headers
    def get_column_type_client(self, conn, table: str, column: str) -> str:
            _COLUMN_TYPE_CACHE = {}

            if not column:
                return "unknown"

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            # Use cache if available
            cache_key = f"{schema}.{tbl}.{column}"
            if cache_key in _COLUMN_TYPE_CACHE:
                return _COLUMN_TYPE_CACHE[cache_key]

            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT
                            data_type,
                            udt_name,
                            character_maximum_length,
                            column_default
                        FROM information_schema.columns
                        WHERE table_schema = %s
                        AND table_name = %s
                        AND column_name = %s
                        LIMIT 1
                    """, (schema, tbl.strip('"'), column))

                    result = cur.fetchone()
                    if not result:
                        LOG.warning(f"⚠️ Could not find column type for {schema}.{tbl}.{column}")
                        _COLUMN_TYPE_CACHE[cache_key] = "unknown"
                        return "unknown"

                    data_type = result["data_type"]
                    udt_name = result["udt_name"]
                    max_length = result["character_maximum_length"]
                    column_default = result["column_default"]

                    # --- Type detection logic ---
                    # Native boolean
                    if data_type == "boolean" or udt_name == "bool":
                        detected = "boolean"

                    # CHAR(1), VARCHAR(1), or bpchar(1)
                    elif (data_type in ("character", "character varying", "text") or udt_name == "bpchar") and (max_length == 1):
                        detected = "char1"

                    # ENUM-like (common in PostgreSQL when udt_name != builtins)
                    elif udt_name not in ("bpchar", "varchar", "text", "bool", "int4", "int8"):
                        detected = "enum"

                    # Generic text-based flag
                    elif data_type in ("character varying", "text"):
                        detected = "text"

                    # Fallback
                    else:
                        detected = "unknown"

                    _COLUMN_TYPE_CACHE[cache_key] = detected

                    LOG.debug(
                        f"🔍 Column type detected: {schema}.{tbl}.{column} → {detected} "
                        f"({data_type}, udt={udt_name}, max_len={max_length}, default={column_default})"
                    )

                    return detected

            except Exception as e:
                LOG.error(f"❌ Error getting column type for {table}.{column}: {e}")
                _COLUMN_TYPE_CACHE[cache_key] = "unknown"
                return "unknown"
    def convert_boolean_for_column_client(self, value, column_type):

            """
            Enhanced: Convert boolean-like value to the correct format for a given column type.

            Supports:
            ✅ boolean → True/False
            ✅ char1   → 'Y'/'N' or '1'/'0'
            ✅ text    → 'active'/'inactive'
            ✅ enum    → 'active'/'inactive' (default fallback)
            ✅ unknown → returns bool(value)
            """

            # Normalize incoming value first
            if isinstance(value, str):
                val_lower = value.strip().lower()
                if val_lower in ("1", "true", "t", "yes", "y", "active"):
                    value = True
                elif val_lower in ("0", "false", "f", "no", "n", "inactive"):
                    value = False
                else:
                    # unknown string – treat non-empty as True
                    value = bool(value)
            elif isinstance(value, (int, float)):
                value = bool(value)

            # Now handle conversion by target column type
            if column_type == "boolean":
                return bool(value)

            elif column_type == "char1":
                # Use 'Y'/'N' for better readability (vs. '1'/'0')
                return "Y" if value else "N"

            elif column_type in ("text", "varchar", "character varying"):
                return "active" if value else "inactive"

            elif column_type == "enum":
                # Many PostgreSQL enums for status use active/inactive or enabled/disabled
                return "active" if value else "inactive"

            elif column_type == "unknown":
                LOG.warning(f"⚠️ Unknown column type, defaulting to boolean: {value}")
                return bool(value)

            else:
                # Fallback for any type not explicitly handled
                LOG.debug(f"ℹ️ Unhandled type {column_type}, defaulting to bool: {value}")
                return bool(value)
    def apply_status_change_locally(self, table: str, payload: Dict[str, Any]) -> bool:
            """
            FIXED: Apply status change (activate/deactivate) to local database.
            Now prevents duplicate column assignments.
            """
            operation = payload.get("operation")
            row_id = payload.get("row_id")
            data = payload.get("data", {})
            active_status = payload.get("active_status")
            pending_delete = payload.get("pending_delete")
            source = payload.get("source", "unknown")

            # CRITICAL FIX: Determine correct values based on operation
            if operation == "deactivate":
                active_status = False
                pending_delete = True
            elif operation == "activate":
                active_status = True
                pending_delete = False
            else:
                # Fallback to payload values
                if active_status is None:
                    active_status = True
                if pending_delete is None:
                    pending_delete = False

            LOG.info(f"📋 Status change operation: {operation} for {table}[{row_id}]")
            LOG.debug(f"   Target state: active_status={active_status}, pending_delete={pending_delete}")
            LOG.debug(f"   Source: {source}")

            conn = None
            try:
                conn = self.pool.getconn()

                if "." in table:
                    schema, tbl = table.split(".", 1)
                else:
                    schema, tbl = "public", table

                quoted_table = f'"{tbl}"'
                full_table = f"{schema}.{quoted_table}"

                # === Check current local state ===
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(f"""
                        SELECT id, active_status, pending_delete, updated_at
                        FROM {full_table}
                        WHERE id = %s
                    """, (row_id,))

                    local_record = cur.fetchone()

                # === CRITICAL FIX: Skip if already in correct state ===
                if local_record:
                    current_active = local_record.get('active_status')
                    current_pending = local_record.get('pending_delete')

                    # Check if record is already in the target state
                    if operation == "activate":
                        already_correct = (current_active is True and current_pending is False)
                    elif operation == "deactivate":
                        already_correct = (current_active is False and current_pending is True)
                    else:
                        already_correct = False

                    if already_correct:
                        LOG.info(f"   ✅ Record already in correct state - skipping update")
                        LOG.debug(f"      Current: active={current_active}, pending={current_pending}")
                        return True

                    LOG.debug(f"   Current state: active={current_active}, pending={current_pending}")
                    LOG.debug(f"   Will update to: active={active_status}, pending={pending_delete}")

                # === Get schema info ===
                schema_info = self.get_table_schema_info(table)

                # === SYSTEM FIELDS TO EXCLUDE from data loop ===
                # These are handled separately to avoid duplicates
                SYSTEM_FIELDS = {
                    'id',           # Primary key - never update
                    'created_at',   # Creation timestamp - never update
                    'updated_at',   # Update timestamp - always set to NOW()
                    'active_status',   # Status field - handled separately
                    'pending_delete'   # Delete flag - handled separately
                }

                # === HANDLE ACTIVATE OPERATION ===
                if operation == "activate":
                    LOG.info("=" * 80)
                    LOG.info(f"✨ ACTIVATE OPERATION from HQ")
                    LOG.info(f"   Equipment ID: {row_id}")
                    LOG.info(f"   Source: {source}")

                    if not local_record:
                        LOG.info(f"   Record does NOT exist locally - will create")

                        # INSERT new record
                        from datetime import datetime, timezone as dt_timezone

                        insert_data = {}

                        # Add user data fields (excluding system fields)
                        for key, value in data.items():
                            if key not in SYSTEM_FIELDS:
                                insert_data[key] = value

                        # Set system fields
                        insert_data['id'] = row_id
                        insert_data['active_status'] = True
                        insert_data['pending_delete'] = False

                        # Set timestamps
                        now = datetime.now(dt_timezone.utc)
                        if 'created_at' not in data:
                            insert_data['created_at'] = now
                        else:
                            insert_data['created_at'] = data['created_at']
                        insert_data['updated_at'] = now

                        cols = list(insert_data.keys())
                        vals = [insert_data[c] for c in cols]

                        col_list = ", ".join([f'"{c}"' for c in cols])
                        placeholders = ", ".join(["%s"] * len(cols))

                        with conn.cursor() as cur:
                            insert_sql = f"""
                                INSERT INTO {full_table} ({col_list})
                                VALUES ({placeholders})
                                ON CONFLICT (id) DO UPDATE SET
                                active_status = EXCLUDED.active_status,
                                pending_delete = EXCLUDED.pending_delete,
                                updated_at = EXCLUDED.updated_at
                            """

                            cur.execute(insert_sql, vals)
                            LOG.info(f"   ✅ Created new record: {table}[{row_id}]")
                            conn.commit()
                            return True
                    else:
                        # UPDATE existing record
                        with conn.cursor(cursor_factory=RealDictCursor) as cur:
                            update_parts = []
                            update_values = []

                            # 1. Set status fields first
                            if schema_info.get('has_pending_delete'):
                                pd_type = self.get_column_type_client(conn, table, 'pending_delete')
                                pd_value = self.convert_boolean_for_column_client(False, pd_type)
                                update_parts.append('pending_delete = %s')
                                update_values.append(pd_value)
                                LOG.debug(f"   Setting pending_delete = {repr(pd_value)}")

                            if schema_info.get('has_active_status'):
                                as_type = self.get_column_type_client(conn, table, 'active_status')
                                as_value = self.convert_boolean_for_column_client(True, as_type)
                                update_parts.append('active_status = %s')
                                update_values.append(as_value)
                                LOG.debug(f"   Setting active_status = {repr(as_value)}")

                            # 2. Update other data fields (excluding system fields)
                            for key, value in data.items():
                                if key not in SYSTEM_FIELDS:
                                    update_parts.append(f'"{key}" = %s')
                                    update_values.append(value)
                                    LOG.debug(f"   Setting {key} from data")

                            # 3. Always set updated_at to NOW() (NEVER from data)
                            update_parts.append('updated_at = NOW()')

                            # 4. Add WHERE clause value
                            update_values.append(row_id)

                            update_sql = f"""
                                UPDATE {full_table}
                                SET {', '.join(update_parts)}
                                WHERE id = %s
                                RETURNING id
                            """

                            LOG.debug(f"   Executing UPDATE with {len(update_parts)} fields")
                            LOG.debug(f"   SQL: {update_sql}")

                            cur.execute(update_sql, update_values)

                            if cur.rowcount > 0:
                                LOG.info(f"   ✅ Activated: {table}[{row_id}]")
                                conn.commit()
                                return True
                            else:
                                LOG.warning(f"   ⚠️ Update affected 0 rows")
                                conn.rollback()
                                return False

                # === HANDLE DEACTIVATE OPERATION ===
                elif operation == "deactivate":
                    LOG.info(f"🔴 DEACTIVATE operation for {table}[{row_id}]")

                    if not local_record:
                        LOG.warning(f"   ⚠️ Record not found locally")
                        return True

                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        update_parts = []
                        update_values = []

                        if schema_info.get('has_pending_delete'):
                            pd_type = self.get_column_type_client(conn, table, 'pending_delete')
                            pd_value = self.convert_boolean_for_column_client(True, pd_type)
                            update_parts.append("pending_delete = %s")
                            update_values.append(pd_value)

                        if schema_info.get('has_active_status'):
                            as_type = self.get_column_type_client(conn, table, 'active_status')
                            as_value = self.convert_boolean_for_column_client(False, as_type)
                            update_parts.append("active_status = %s")
                            update_values.append(as_value)

                        if not update_parts:
                            LOG.warning(f"   ⚠️ No status columns found")
                            return False

                        # Always update timestamp (NEVER from data)
                        update_parts.append("updated_at = NOW()")
                        update_values.append(row_id)

                        update_sql = f"""
                            UPDATE {full_table}
                            SET {', '.join(update_parts)}
                            WHERE id = %s
                            RETURNING id
                        """

                        cur.execute(update_sql, update_values)

                        if cur.rowcount > 0:
                            LOG.info(f"   ✅ Deactivated {table}[{row_id}]")
                            conn.commit()
                            return True
                        else:
                            LOG.warning(f"   ⚠️ Record not found")
                            return False

                else:
                    LOG.warning(f"   ⚠️ Unknown operation: {operation}")
                    return False

            except Exception as e:
                LOG.error(f"Failed to apply status change for {table}[{row_id}]: {e}")
                LOG.error(traceback.format_exc())
                if conn:
                    conn.rollback()
                return False
            finally:
                if conn:
                    self.pool.putconn(conn)
