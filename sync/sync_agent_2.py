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
from .agent_prelude import SyncDirection, DEVICE_ID_MODULE_AVAILABLE, get_or_create_client_id
import os
import time
import uuid
import logging
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


class SchemaAndChangeDetectionMixin(SmartDeleteMixin):
    """DB pool/schema introspection, download checkpoint, and timestamp-based change detection (the legacy poller)."""
    def perform_mirror_sync_now(self, direction: str = "bidirectional") -> Dict:
        """Perform immediate mirror sync"""
        if not self.mirror:
            return {"success": False, "error": "Mirror not initialized"}

        try:
            LOG.info("🔄 Manual mirror sync triggered...")

            direction_map = {
                "bidirectional": SyncDirection.BIDIRECTIONAL,
                "hq_to_local": SyncDirection.HQ_TO_LOCAL,
                "local_to_hq": SyncDirection.LOCAL_TO_HQ,
            }

            sync_direction = direction_map.get(direction, SyncDirection.BIDIRECTIONAL)

            if not self.mirror.connect():
                return {"success": False, "error": "Connection failed"}

            try:
                stats = self.mirror.mirror_all_tables(
                    tables=self.tables,
                    direction=sync_direction,
                    conflict_strategy="last_write_wins",
                )

                total_synced_to_hq = sum(s.synced_to_hq for s in stats)
                total_synced_to_local = sum(s.synced_to_local for s in stats)
                total_conflicts = sum(s.conflicts for s in stats)
                total_errors = sum(s.errors for s in stats)
                total_changes = total_synced_to_hq + total_synced_to_local

                if total_changes > 0:
                    LOG.info(f"📡 Broadcasting {total_changes} changes...")
                    self.mirror.broadcast_changes_to_agents(stats)

                return {
                    "success": True,
                    "tables_processed": len(stats),
                    "synced_to_hq": total_synced_to_hq,
                    "synced_to_local": total_synced_to_local,
                    "conflicts_resolved": total_conflicts,
                    "errors": total_errors,
                    "changes_broadcasted": total_changes > 0,
                }
            finally:
                self.mirror.disconnect()

        except Exception as e:
            LOG.error(f"❌ Mirror sync failed: {e}")
            return {"success": False, "error": str(e)}

    def mirror_sync_loop(self):
        """Background thread for periodic mirror sync"""
        interval_seconds = int(self.mirror_interval_hours * 3600)

        LOG.info("🔄 Mirror sync loop started (interval: %.1fh)", self.mirror_interval_hours)

        sync_count = 0

        while not self.stop_event.is_set():
            try:
                sync_count += 1

                LOG.info("")
                LOG.info("🔄 Scheduled Mirror Sync #%d", sync_count)

                result = self.perform_mirror_sync_now(direction="bidirectional")

                if result["success"]:
                    total_changes = result.get("synced_to_hq", 0) + result.get("synced_to_local", 0)

                    if total_changes > 0:
                        LOG.info(f"✅ {total_changes} changes synced")
                    else:
                        LOG.info(f"✅ All databases consistent")
                else:
                    LOG.error(f"❌ Mirror sync failed: {result.get('error')}")

                next_sync = datetime.now() + timedelta(seconds=interval_seconds)
                LOG.info(f"⏰ Next sync: {next_sync.strftime('%Y-%m-%d %H:%M:%S')}")

            except Exception as e:
                LOG.error(f"❌ Mirror loop error: {e}")

            for _ in range(interval_seconds):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        LOG.info("🔄 Mirror sync loop exiting")

    def get_mirror_status(self) -> Dict:
        """Get mirror system status"""
        if not self.mirror:
            return {"enabled": False, "status": "not_initialized"}

        return {
            "enabled": self.mirror_enabled,
            "status": "active",
            "interval_hours": self.mirror_interval_hours,
            "tables_monitored": len(self.tables),
            "client_id": self.client_id,
        }

    def get_table_schema_info(self, table):
        """Get schema information about table columns"""
        if table in self._table_schema_cache:
            return self._table_schema_cache[table]

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Fetch ALL columns so we can filter out HQ-only columns
                # that haven't been migrated to this client yet.
                cur.execute(
                    """
                            SELECT column_name, data_type, udt_name, character_maximum_length
                            FROM information_schema.columns
                            WHERE table_schema = %s
                            AND table_name = %s
                        """,
                    (schema, tbl),
                )

                columns = cur.fetchall()
                all_col_names = {c["column_name"] for c in columns}

                LOG.debug(f"Schema check for {table}: Found {len(columns)} columns")

                schema_info = {
                    "has_pending_delete": "pending_delete" in all_col_names,
                    "has_active_status": "active_status" in all_col_names,
                    "columns": all_col_names,  # full set for upsert filtering
                }

                self._table_schema_cache[table] = schema_info

                LOG.debug(f"Schema info for {table}: {schema_info}")
                return schema_info
        except Exception as e:
            LOG.warning(f"Could not fetch schema info for {table}: {e}")
            LOG.exception(e)
            return {"has_pending_delete": False, "has_active_status": False, "columns": set()}
        finally:
            if conn:
                self.pool.putconn(conn)

    def get_json_columns(self, table):
        """Get list of JSON/JSONB columns for a table"""
        if table in self._json_columns_cache:
            return self._json_columns_cache[table]

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = %s
                        AND table_name = %s
                        AND data_type IN ('json', 'jsonb')
                    """,
                    (schema, tbl),
                )

                json_cols = [row[0] for row in cur.fetchall()]
                self._json_columns_cache[table] = json_cols
                return json_cols
        except Exception as e:
            LOG.warning(f"Could not fetch JSON columns for {table}: {e}")
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def verify_tables_have_updated_at(self):
        """Verify that all configured tables have updated_at columns"""
        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                valid_tables = []
                missing_created_at = []
                for table in self.tables:
                    if "." in table:
                        schema, tbl = table.split(".", 1)
                    else:
                        schema, tbl = "public", table

                    # Check updated_at (required for the poller) AND created_at
                    # (required by the mirror recovery path) in one query.
                    cur.execute(
                        """
                            SELECT
                                bool_or(column_name = 'updated_at') AS has_updated,
                                bool_or(column_name = 'created_at') AS has_created
                            FROM information_schema.columns
                            WHERE table_schema = %s AND table_name = %s
                        """,
                        (schema, tbl),
                    )
                    row = cur.fetchone()
                    has_updated = bool(row and row[0])
                    has_created = bool(row and row[1])

                    if has_updated:
                        valid_tables.append(table)
                        LOG.debug("✓ Table has updated_at: %s", table)
                        if not has_created:
                            missing_created_at.append(table)
                    else:
                        LOG.warning("✗ Table missing updated_at, skipping: %s", table)

                self.tables = valid_tables
                LOG.info(
                    "Verified %d/%d tables have updated_at",
                    len(valid_tables),
                    len(self.config["tables"]),
                )

                # created_at is a hard requirement of the mirror (it SELECTs
                # created_at when reconciling). Surface this loudly so a table
                # that syncs fine via the poller doesn't silently break recovery.
                if missing_created_at:
                    LOG.warning(
                        "⚠️  %d synced table(s) lack a created_at column — the mirror "
                        "recovery path REQUIRES it and will error for these: %s",
                        len(missing_created_at),
                        ", ".join(missing_created_at),
                    )

        except Exception as e:
            LOG.error("Failed to verify tables: %s", e)
        finally:
            if conn:
                self.pool.putconn(conn)

    def get_machine_identifier(self):
        """Get unique machine identifier based on MAC address"""
        env_machine_id = os.getenv("MACHINE_ID")
        if env_machine_id:
            return env_machine_id

        try:
            mac_int = uuid.getnode()
            mac_hex = ":".join(f"{(mac_int >> ele) & 0xff:02x}" for ele in range(40, -1, -8))
            mac_hash = hashlib.sha1(mac_hex.encode()).hexdigest()[:12]
            return f"mac-{mac_hash}"
        except Exception:
            try:
                hostname = os.uname().nodename
                return f"host-{hostname}"
            except:
                return f"machine-{str(uuid.uuid4())[:8]}"

    def _create_db_pool(self, db_cfg: Dict[str, Any]) -> ThreadedConnectionPool:
        """Create PostgreSQL connection pool"""
        try:
            pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=5,
                host=db_cfg["host"],
                port=int(db_cfg.get("port", 5432)),
                database=db_cfg["dbname"],
                user=db_cfg["user"],
                password=db_cfg["password"],
            )
            LOG.info("✅ PostgreSQL connection pool created")
            return pool
        except Exception as e:
            LOG.error("Failed to create DB connection pool: %s", e)
            raise

    def auto_register_client(self) -> str:
        """Auto-register or retrieve client ID"""
        if DEVICE_ID_MODULE_AVAILABLE:
            # Try calling with state_manager parameter first
            try:
                client_id = get_or_create_client_id(state_manager=self.state)
            except TypeError:
                # Fallback: function doesn't accept state_manager parameter
                try:
                    client_id = get_or_create_client_id()
                except TypeError:
                    # Function might need no arguments at all
                    client_id = get_or_create_client_id
                    if callable(client_id):
                        client_id = client_id()
        else:
            # Generate fallback client ID
            client_id = self.state.get_client_id()
            if not client_id:
                import uuid
                import hashlib

                mac = uuid.getnode()
                mac_hash = hashlib.sha1(str(mac).encode()).hexdigest()[:12]
                client_id = f"mac-{mac_hash}"
                self.state.set_client_id(client_id)

        LOG.info(f"Client ID: {client_id}")
        return client_id

    def get_last_download_time(self) -> str:
        """
        Get last successful download time.

        If no checkpoint exists (fresh install or reinstall that wiped state),
        we set a flag so start() knows to run a full mirror sync instead of
        relying on the audit-log-based download, which only knows about changes
        since the audit log started — it cannot reconstruct a full DB from scratch.
        """
        last_time = self.state.get("last_download_time")
        if last_time:
            return last_time

        # No checkpoint = state was wiped (reinstall) or this is a new machine.
        # Signal that a full mirror is needed by setting the flag — start() reads
        # this before launching threads.
        LOG.warning("⚠️  No download checkpoint found — state was wiped or this is a new install.")
        LOG.warning("   Will use epoch timestamp for audit-log download, but a mirror sync")
        LOG.warning("   is strongly recommended to recover records predating the audit log.")
        self._needs_mirror_sync = True

        # ── FIX: Also wipe the upload checkpoint so the upload loop re-scans
        # ALL local records and pushes them to HQ.  Without this, a reinstall
        # that still has stale data keeps a recent last_upload_time and the
        # upload loop skips every record that predates it — HQ never receives
        # the existing local data.
        existing_upload_ts = self.state.get("last_upload_time")
        if existing_upload_ts:
            LOG.warning(
                "⚠️  Wiping stale upload checkpoint (%s) so all local records "
                "are re-uploaded to HQ after reinstall.",
                existing_upload_ts,
            )
            self.state.set("last_upload_time", None)

        # Use epoch so the audit-log download at least fetches everything audited
        return "1970-01-01T00:00:00+00:00"

    def detect_local_restores(self, table: str, since_ts: str) -> List[Dict]:
        """
        ✅ FIXED: Detect records that were restored (activated) locally.
        Now properly clears soft delete tracking to allow re-deletion.

        This function:
        1. Finds restore operations from audit_log
        2. Verifies records are actually active now
        3. Clears soft delete tracking for restored records
        4. Returns restore events for sync to HQ
        """
        conn = None
        try:
            conn = self.pool.getconn()

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            quoted_table = f'"{tbl}"'
            full_table = f"{schema}.{quoted_table}"

            # Check if table has status columns
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = %s
                        AND table_name = %s
                        AND column_name IN ('pending_delete', 'active_status')
                    """,
                    (schema, tbl),
                )

                status_columns = {row[0] for row in cur.fetchall()}

            if not status_columns:
                LOG.debug(
                    f"   Table {table} doesn't support restore operations (no status columns)"
                )
                return []

            # Find restore operations from audit log
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                        SELECT
                            event_id,
                            row_id,

                            received_at
                        FROM audit_log
                        WHERE table_name = %s
                        AND operation = 'r'
                        AND received_at > %s
                        ORDER BY received_at ASC
                        LIMIT 100
                    """,
                    (table, since_ts),
                )

                audit_restores = cur.fetchall()

                if not audit_restores:
                    LOG.debug(f"   No restore operations found in audit_log for {table}")
                    return []

                LOG.info(
                    f"📋 Found {len(audit_restores)} restore operations in audit_log for {table}"
                )

                restore_events = []
                restored_ids = []  # Track IDs to clear from soft delete tracking

                for audit_record in audit_restores:
                    row_id = str(audit_record["row_id"])
                    event_id = audit_record["event_id"]
                    audit_data = audit_record.get("data", {})

                    # Verify the record still exists and is active
                    cur.execute(
                        f"""
                            SELECT
                                id,
                                to_jsonb(t.*) as row_data,
                                updated_at,
                                active_status,
                                pending_delete
                            FROM {full_table} t
                            WHERE id = %s
                        """,
                        (row_id,),
                    )

                    current_record = cur.fetchone()

                    if not current_record:
                        LOG.warning(
                            f"   ⚠️ Record {table}[{row_id}] not found (may have been deleted)"
                        )
                        continue

                    # Verify it's actually active now
                    is_active = (
                        current_record.get("active_status") == True
                        or current_record.get("pending_delete") == False
                    )

                    if not is_active:
                        LOG.warning(f"   ⚠️ Record {table}[{row_id}] is not active (skipping)")
                        continue

                    # Create restore event for sync
                    restore_event = {
                        "event_id": event_id or f"restore-{table}-{row_id}-{uuid.uuid4().hex[:8]}",
                        "table": table,
                        "row_id": row_id,
                        "operation": "activate",
                        "data": current_record["row_data"],
                        "created_at": current_record["updated_at"].isoformat(),
                        "source": "local",
                        "machine_id": self.machine_id,
                        "active_status": True,
                        "pending_delete": False,
                        "metadata": {
                            "restore_operation": True,
                            "restored_from_audit": True,
                            "audit_data": audit_data,
                        },
                    }

                    restore_events.append(restore_event)
                    restored_ids.append(row_id)  # Track for cleanup
                    LOG.info(f"✅ Detected local restore: {table}[{row_id}]")

                if restore_events:
                    LOG.info(f"🎉 Prepared {len(restore_events)} restore events for sync")

                    # 🔥 CRITICAL FIX: Clear soft delete tracking for restored records
                    if restored_ids:
                        state_key = f"synced_soft_deletes_{table}"
                        synced_soft_deletes_str = self.state.get(state_key, "")
                        synced_soft_deletes = (
                            set(synced_soft_deletes_str.split(","))
                            if synced_soft_deletes_str
                            else set()
                        )

                        cleared_count = 0
                        for restored_id in restored_ids:
                            if restored_id in synced_soft_deletes:
                                synced_soft_deletes.discard(restored_id)
                                cleared_count += 1
                                LOG.debug(f"   🧹 Cleared soft delete tracking for {restored_id}")

                        # Save updated tracking
                        if cleared_count > 0:
                            self.state.set(state_key, ",".join(synced_soft_deletes))
                            LOG.info(
                                f"   ✨ Cleared {cleared_count} records from soft delete tracking"
                            )
                            LOG.info(f"   📝 These records can now be deleted again if needed")

                return restore_events

        except Exception as e:
            LOG.error(f"Error detecting restores for {table}: {e}")
            LOG.exception(e)
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def fetch_recent_changes_for_table(self, table: str, since_ts: str, limit: int = 500):
        """
        ⚡ ENHANCED: Optimized change detection with batching, caching, and better error handling

        Key improvements:
        1. ✅ Proper detection of all change types (updates, soft deletes, re-deletes)
        2. ⚡ Batch processing for large result sets
        3. 🎯 Smart caching of schema info and state
        4. 📊 Enhanced metrics and logging
        5. 🔒 Better transaction handling
        6. 🚀 Index hints for faster queries
        """
        conn = None
        start_time = time.time()

        try:
            conn = self.pool.getconn()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Parse table name
                if "." in table:
                    schema, tbl = table.split(".", 1)
                else:
                    schema, tbl = "public", table

                quoted_table = f'"{tbl}"'
                full_table_name = f"{schema}.{quoted_table}"

                # ⚡ Parse and validate timestamp
                since_dt = self._parse_timestamp(since_ts)

                # 🎯 Cache schema info (avoid repeated lookups)
                cache_key = f"schema_info_{table}"
                schema_info = getattr(self, "_schema_cache", {}).get(cache_key)

                if not schema_info:
                    schema_info = self.get_table_schema_info(table)
                    if not hasattr(self, "_schema_cache"):
                        self._schema_cache = {}
                    self._schema_cache[cache_key] = schema_info

                has_pending_delete = schema_info.get("has_pending_delete", False)
                has_active_status = schema_info.get("has_active_status", False)
                has_deleted_at = schema_info.get("has_deleted_at", False)

                # ⚡ OPTIMIZED QUERY with index hints
                query = f"""
                        SELECT
                            id,
                            to_jsonb(t.*) as row_data,
                            updated_at,
                            created_at
                            {', pending_delete' if has_pending_delete else ''}
                            {', active_status' if has_active_status else ''}
                            {', deleted_at' if has_deleted_at else ''}
                        FROM {full_table_name} t
                        WHERE updated_at > %s::timestamptz
                        ORDER BY updated_at ASC
                        LIMIT %s
                    """

                # Optional query-plan capture. Gated behind an EXPLICIT opt-in
                # (sync.explain_queries / SYNC_EXPLAIN=1), NOT the DEBUG log level:
                # the sync logger defaults to DEBUG, and EXPLAIN ANALYZE *executes*
                # the query an extra time — doubling DB work on every poll of every
                # table. Also use plain EXPLAIN (no ANALYZE) so it never executes.
                if os.getenv("SYNC_EXPLAIN", "0") == "1" or self.sync_cfg.get("explain_queries"):
                    try:
                        cur.execute(f"EXPLAIN {query}", (since_dt, limit))
                        explain_result = cur.fetchall()
                        LOG.debug(
                            f"📊 Query plan for {table}:\n{explain_result[0] if explain_result else 'N/A'}"
                        )
                    except Exception:
                        pass

                cur.execute(query, (since_dt, limit))
                rows = cur.fetchall()

                query_time = time.time() - start_time

                if not rows:
                    return []

                # 🎯 Load soft delete tracking state once
                state_key = f"synced_soft_deletes_{table}"
                synced_soft_deletes = self._load_soft_delete_state(state_key)

                LOG.info(f"📋 Processing {len(rows)} changed records in {table}")
                LOG.debug(f"   📊 Tracking {len(synced_soft_deletes)} soft deletes")

                # 📊 Initialize metrics
                metrics = {
                    "active_updates": 0,
                    "soft_deletes": 0,
                    "hard_deletes": 0,
                    "skipped_synced": 0,
                    "restored_then_deleted": 0,
                    "deactivations": 0,
                }

                changes = []
                new_soft_deletes = []
                batch_size = 100  # Process in batches for better memory management

                # ⚡ Process records in batches
                for batch_start in range(0, len(rows), batch_size):
                    batch = rows[batch_start : batch_start + batch_size]
                    batch_changes = self._process_record_batch(
                        batch,
                        table,
                        conn,
                        has_pending_delete,
                        has_active_status,
                        has_deleted_at,
                        synced_soft_deletes,
                        new_soft_deletes,
                        metrics,
                    )
                    changes.extend(batch_changes)

                # 💾 Persist updated soft delete tracking
                if new_soft_deletes:
                    self._save_soft_delete_state(state_key, synced_soft_deletes, new_soft_deletes)

                # 📊 Log comprehensive summary
                self._log_change_summary(table, metrics, query_time)

                return changes

        except Exception as e:
            LOG.error(f"❌ Error fetching changes for {table}: {e}")
            LOG.exception(e)
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def _parse_timestamp(self, since_ts: str) -> datetime:
        """Parse timestamp with fallback handling"""
        if isinstance(since_ts, datetime):
            return since_ts

        try:
            return datetime.fromisoformat(since_ts.replace("Z", "+00:00"))
        except Exception as e:
            LOG.warning(f"⚠️ Invalid timestamp: {since_ts}, using 1 hour ago")
            return datetime.now(timezone.utc) - timedelta(hours=1)

    def _load_soft_delete_state(self, state_key: str) -> set:
        """Load and parse soft delete tracking state"""
        state_str = self.state.get(state_key, "")
        if not state_str:
            return set()

        try:
            return set(state_str.split(","))
        except Exception as e:
            LOG.warning(f"⚠️ Failed to parse soft delete state: {e}")
            return set()

    def _save_soft_delete_state(self, state_key: str, current_set: set, new_deletes: list):
        """Save soft delete tracking with size management"""
        current_set.update(new_deletes)

        # Keep reasonable size (max 10,000 IDs)
        if len(current_set) > 10000:
            current_set = set(list(current_set)[-10000:])

        self.state.set(state_key, ",".join(current_set))
        LOG.debug(f"   💾 Saved {len(new_deletes)} new soft deletes ({len(current_set)} total)")

    def _process_record_batch(
        self,
        batch: list,
        table: str,
        conn,
        has_pending_delete: bool,
        has_active_status: bool,
        has_deleted_at: bool,
        synced_soft_deletes: set,
        new_soft_deletes: list,
        metrics: dict,
    ) -> list:
        """Process a batch of records efficiently"""
        changes = []

        for row in batch:
            event_timestamp = (
                row.get("updated_at") or row.get("created_at") or datetime.now(timezone.utc)
            )
            row_id = str(row["id"])

            # 🔍 Determine current delete status
            is_soft_deleted = self._is_record_soft_deleted(
                row, has_pending_delete, has_active_status, has_deleted_at
            )

            was_tracked = row_id in synced_soft_deletes

            # ⚡ Skip already-synced soft deletes (optimization)
            if is_soft_deleted and was_tracked:
                metrics["skipped_synced"] += 1
                LOG.debug(f"   ⏭️ Skip already-synced: {row_id}")
                continue

            # 🔄 Detect re-delete scenario
            if is_soft_deleted and not was_tracked and synced_soft_deletes:
                metrics["restored_then_deleted"] += 1
                LOG.info(f"   🔄 Re-delete detected: {row_id}")

            # 🎯 Process based on status
            if is_soft_deleted:
                change_event = self._create_soft_delete_event(
                    row, row_id, table, event_timestamp, conn, metrics
                )
                new_soft_deletes.append(row_id)
            else:
                change_event = self._create_update_event(
                    row,
                    row_id,
                    table,
                    event_timestamp,
                    has_pending_delete,
                    has_active_status,
                    metrics,
                )

            changes.append(change_event)

        return changes
