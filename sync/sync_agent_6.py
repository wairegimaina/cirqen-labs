from .agent_prelude import LOG, now_kenyan, now_utc, now_iso, format_kenyan_time
from .agent_prelude import KENYAN_TZ, PerformanceMonitor, encrypt_token, setup_logging, load_agent_config
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
try:
    from .sql_ident import identifier, qualified
except ImportError:  # loaded as a top-level module with sync/ on sys.path
    from sql_ident import identifier, qualified

class ApplyRemoteUpdateMixin(SmartDeleteMixin):
    """apply_remote_update_locally: the download-apply path incl. conflict quarantine, deterministic LWW, schema-drift guard, and smart delete."""
    def apply_remote_update_locally(self, table: str, payload: Dict[str, Any]) -> bool:
            """
            Apply a remote update to local DB with comprehensive smart delete support and auto-recovery.

            ✅ ENHANCED VERSION 5.0 - With NULL validation and better error handling

            Handles:
            - Regular updates/inserts with conflict resolution
            - Status changes (activate/deactivate)
            - Smart deletes with dependency-aware cascade
            - Automatic FK-violation fallback to soft delete
            - Auto-recovery of missing parent records from HQ
            - ✅ NEW: Data validation before insert/update
            - ✅ NEW: Enhanced NULL violation error handling
            - ✅ NEW: Better diagnostic logging
            """
            conn = None
            try:
                conn = self.pool.getconn()

                operation = payload.get("operation", "u")
                data = payload.get("data", {})
                row_id = payload.get("row_id")
                remote_updated_at = payload.get("last_modified") or payload.get("updated_at")
                source = payload.get("source", "unknown")

                if "." in table:
                    schema, tbl = table.split(".", 1)
                else:
                    schema, tbl = "public", table

                full_table = qualified(schema, tbl)

                # ============================================================
                # Guard: skip broadcast/mirror-sync signals entirely.
                # operation='b' or table='system' are sentinel rows that
                # og_server writes to audit_log to notify clients; they are
                # not real DB rows and must never be upserted locally.
                # ============================================================
                if operation == "b" or tbl == "system":
                    LOG.debug(f"⏭️  Skipping broadcast signal: op={operation} table={table}")
                    return True

                # Debug logging for non-delete operations
                if operation not in ['d']:
                    LOG.debug(f"📥 Processing update: {table}[{row_id}] op={operation}")

                # ============================================================
                # Handle status change operations (activate/deactivate)
                # ============================================================
                if operation in ["activate", "deactivate"]:
                    LOG.info(f"📋 Status change operation: {operation} for {table}[{row_id}]")
                    return self.apply_status_change_locally(table, payload)

                # ============================================================
                # ENHANCED: Handle DELETE with smart delete system
                # ============================================================
                if operation == 'd':
                    LOG.info(f"🗑️  DELETE from HQ: {table}[{row_id}] source={source}")

                    with conn.cursor() as cur:
                        # First, check if record exists locally
                        cur.execute(f"SELECT id FROM {full_table} WHERE id = %s", (row_id,))
                        record_exists = cur.fetchone()

                        if not record_exists:
                            LOG.debug(f"✓ Record already deleted locally: {table}[{row_id}]")
                            return True

                        LOG.info(f"🔍 Record exists locally. Checking delete strategy...")

                        # Get table schema info
                        schema_info = self.get_table_schema_info(table)

                        LOG.debug(f"   Table schema:")
                        LOG.debug(f"      has_pending_delete: {schema_info['has_pending_delete']}")
                        LOG.debug(f"      has_active_status: {schema_info['has_active_status']}")

                        # ============================================================
                        # PHASE 1: Attempt hard delete first
                        # ============================================================
                        try:
                            LOG.info(f"🔨 Phase 1: Attempting hard delete...")

                            cur.execute(f"DELETE FROM {full_table} WHERE id = %s", (row_id,))
                            deleted_count = cur.rowcount

                            if deleted_count > 0:
                                conn.commit()
                                LOG.info(f"✅ Hard delete successful: {table}[{row_id}]")
                                LOG.info(f"   Deleted {deleted_count} record(s)")
                                LOG.info("=" * 70)
                                return True
                            else:
                                LOG.warning(f"⚠️  Record not found during delete: {table}[{row_id}]")
                                return True

                        except psycopg2.errors.ForeignKeyViolation as fk_error:
                            # ============================================================
                            # PHASE 2: FK Violation - Must use soft delete
                            # ============================================================
                            conn.rollback()

                            LOG.warning("")
                            LOG.warning("=" * 70)
                            LOG.warning("⚠️  HARD DELETE FAILED - FOREIGN KEY CONSTRAINT")
                            LOG.warning("=" * 70)

                            # Extract FK constraint details from error
                            error_str = str(fk_error)
                            LOG.warning(f"   Error: {error_str[:200]}")

                            # Parse which table is blocking
                            if "still referenced from table" in error_str:
                                blocking_table = error_str.split('table "')[-1].split('"')[0]
                                LOG.warning(f"   Blocking table: {blocking_table}")

                            # Check if table supports soft delete
                            if not schema_info['has_pending_delete'] and not schema_info['has_active_status']:
                                LOG.error("")
                                LOG.error("❌ CANNOT SOFT DELETE")
                                LOG.error(f"   Table '{table}' has no status columns")
                                LOG.error("   Options:")
                                LOG.error("   1. Add pending_delete or active_status columns")
                                LOG.error("   2. Manually delete dependent records first")
                                LOG.error("   3. Contact administrator")
                                LOG.error("=" * 70)
                                return False

                            LOG.warning("")
                            LOG.warning("🔄 Phase 2: Converting to SOFT DELETE with dependency cascade...")
                            LOG.warning("=" * 70)

                            # ============================================================
                            # Use smart delete handler for comprehensive cascade
                            # ============================================================
                            try:
                                from sync.soft_delete_handler import (
                                    perform_smart_delete,
                                    SoftDeleteDependencyChecker
                                )

                                LOG.info("✓ Smart delete handler loaded")
                                LOG.info("")

                                # Execute smart delete with full dependency cascade
                                success, operation_type, message, changes = perform_smart_delete(
                                    conn=conn,
                                    table=table,
                                    row_id=row_id,
                                    client_id=f"agent-{self.client_id}",
                                    force_hard_delete=False
                                )

                                if success:
                                    LOG.info("")
                                    LOG.info("=" * 70)
                                    LOG.info(f"✅ SMART DELETE CASCADE SUCCESSFUL")
                                    LOG.info(f"   Operation: {operation_type.upper()}")
                                    LOG.info(f"   Primary record: {table}[{row_id}]")
                                    LOG.info(f"   Total changes: {len(changes)} records affected")
                                    LOG.info(f"   Cascaded: {len(changes) - 1} dependent records")
                                    LOG.info(f"   Message: {message}")
                                    LOG.info("=" * 70)

                                    # Log affected tables summary
                                    if changes:
                                        affected_tables = {}
                                        for change in changes:
                                            tbl = change.get('table', 'unknown')
                                            affected_tables[tbl] = affected_tables.get(tbl, 0) + 1

                                        LOG.info("")
                                        LOG.info("📊 Cascade Summary:")
                                        for tbl, count in sorted(affected_tables.items()):
                                            LOG.info(f"   • {tbl}: {count} record(s)")
                                        LOG.info("")

                                    return True
                                else:
                                    LOG.error("")
                                    LOG.error("=" * 70)
                                    LOG.error(f"❌ SMART DELETE FAILED")
                                    LOG.error(f"   Error: {message}")
                                    LOG.error("=" * 70)
                                    return False

                            except ImportError as import_error:
                                # ============================================================
                                # FALLBACK: Manual soft delete without cascade
                                # ============================================================
                                LOG.warning("")
                                LOG.warning("⚠️  Smart delete handler not available")
                                LOG.warning(f"   Error: {import_error}")
                                LOG.warning("")
                                LOG.warning("🔄 Falling back to SIMPLE SOFT DELETE (no cascade)")
                                LOG.warning("   ⚠️  WARNING: Dependent records will NOT be soft deleted")
                                LOG.warning("=" * 70)

                                # Simple soft delete without dependency cascade
                                update_parts = []

                                if schema_info['has_pending_delete']:
                                    update_parts.append("pending_delete = TRUE")
                                    LOG.info("   Setting: pending_delete = TRUE")

                                if schema_info['has_active_status']:
                                    update_parts.append("active_status = FALSE")
                                    LOG.info("   Setting: active_status = FALSE")

                                if update_parts:
                                    update_parts.append("updated_at = NOW()")
                                    update_sql = f"""
                                        UPDATE {full_table}
                                        SET {', '.join(update_parts)}
                                        WHERE id = %s
                                    """

                                    cur.execute(update_sql, (row_id,))
                                    conn.commit()

                                    LOG.info("")
                                    LOG.info("✅ Simple soft delete applied (PRIMARY RECORD ONLY)")
                                    LOG.info("   ⚠️  Dependent records NOT cascaded")
                                    LOG.info("   Consider installing soft_delete_handler.py for full cascade")
                                    LOG.info("=" * 70)
                                    return True
                                else:
                                    LOG.error("")
                                    LOG.error("❌ Cannot soft delete: no status columns available")
                                    LOG.error("=" * 70)
                                    return False

                            except Exception as smart_delete_error:
                                LOG.error("")
                                LOG.error("=" * 70)
                                LOG.error(f"💥 SMART DELETE ERROR")
                                LOG.error(f"   {str(smart_delete_error)}")
                                LOG.error("=" * 70)
                                LOG.exception(smart_delete_error)
                                return False

                        except Exception as delete_error:
                            conn.rollback()
                            LOG.error("")
                            LOG.error("=" * 70)
                            LOG.error(f"💥 DELETE OPERATION ERROR")
                            LOG.error(f"   Table: {table}")
                            LOG.error(f"   Row ID: {row_id}")
                            LOG.error(f"   Error: {str(delete_error)}")
                            LOG.error("=" * 70)
                            LOG.exception(delete_error)
                            return False

                # ============================================================
                # Handle regular UPDATE/INSERT operations
                # ============================================================
                with conn.cursor() as cur:
                    # Conflict resolution with timezone normalization
                    if self.config["sync"].get("conflict_resolution") == "last_write_wins" and remote_updated_at:
                        cur.execute(f"""
                            SELECT updated_at
                            FROM {full_table}
                            WHERE id = %s
                        """, (row_id,))

                        result = cur.fetchone()
                        if result and result[0]:
                            local_updated_at = result[0]

                            # Deterministic, convergent LWW: newer (updated_at,
                            # tiebreak-key) wins; exact ties broken by a stable
                            # key so client and HQ agree on the same winner.
                            winner = self.resolve_conflict(
                                local_ts=local_updated_at,
                                remote_ts=remote_updated_at,
                                local_key=getattr(self, "machine_id", "") or "",
                                remote_key=source or "",
                            )

                            if winner == "local":
                                LOG.warning("⚠️  Conflict on %s id=%s: local wins, "
                                        "discarding remote change (quarantined)", table, row_id)
                                # Do NOT silently drop the losing remote change —
                                # persist it to sync_conflicts for later review.
                                self.record_conflict(
                                    table=table,
                                    row_id=row_id,
                                    operation=operation,
                                    winner="local",
                                    resolution="local_newer_remote_discarded",
                                    local_updated_at=local_updated_at,
                                    remote_updated_at=remote_updated_at,
                                    remote_data=data,
                                    source=source,
                                )
                                return True
                            # else: remote wins -> fall through and overwrite.

                    # ✅ CRITICAL FIX: Validate data before processing
                    is_valid, validation_error = self.validate_update_data(table, row_id, data, operation)

                    if not is_valid:
                        LOG.error("")
                        LOG.error("=" * 80)
                        LOG.error("💥 DATA VALIDATION FAILED")
                        LOG.error("=" * 80)
                        LOG.error(f"   Table: {table}")
                        LOG.error(f"   Row ID: {row_id}")
                        LOG.error(f"   Operation: {operation}")
                        LOG.error(f"   Error: {validation_error}")
                        LOG.error(f"   Data keys received: {list(data.keys()) if data else 'NONE'}")
                        LOG.error("=" * 80)
                        LOG.error("")
                        LOG.error("🔍 ROOT CAUSE:")
                        LOG.error("   The HQ server sent an incomplete update")
                        LOG.error("   This usually means:")
                        LOG.error("   1. audit_log.data field is empty/NULL")
                        LOG.error("   2. Server didn't fetch actual row data")
                        LOG.error("   3. Row was deleted before sync")
                        LOG.error("")
                        LOG.error("💡 SOLUTION:")
                        LOG.error(f"   1. Check HQ server logs for table: {table}")
                        LOG.error(f"   2. Verify row exists: SELECT * FROM {table} WHERE id = '{row_id}'")
                        LOG.error(f"   3. Check audit_log: SELECT * FROM audit_log WHERE row_id = '{row_id}'")
                        LOG.error("=" * 80)
                        LOG.error("")

                        return False

                    LOG.debug(f"✓ Data validation passed for {table} id={row_id}")

                    # Get schema info and set defaults for status columns
                    schema_info = self.get_table_schema_info(table)

                    if schema_info['has_pending_delete'] and 'pending_delete' not in data:
                        data['pending_delete'] = False
                        LOG.debug("Added default pending_delete=False for %s id=%s", table, row_id)

                    if schema_info['has_active_status'] and 'active_status' not in data:
                        data['active_status'] = True
                        LOG.debug("Added default active_status=True for %s id=%s", table, row_id)

                    # Get JSON columns and prepare values correctly
                    json_columns = self.get_json_columns(table)

                    def prepare_value(key, val):
                        if val is None:
                            return None
                        elif key in json_columns:
                            if isinstance(val, (dict, list)):
                                return Json(val)
                            elif isinstance(val, str):
                                try:
                                    parsed = json.loads(val)
                                    return Json(parsed)
                                except:
                                    return Json(val)
                            else:
                                return Json(val)
                        elif isinstance(val, (dict, list)):
                            return Json(val)
                        else:
                            return val

                    local_columns = schema_info.get('columns', set())
                    processed_data = {}
                    drifted_columns = []
                    for key, value in data.items():
                        if key == 'pending_delete' and not schema_info['has_pending_delete']:
                            continue
                        if key == 'active_status' and not schema_info['has_active_status']:
                            continue
                        # Column exists at HQ but not locally — a migration applied
                        # at HQ but not yet on this client. Dropping it prevents an
                        # UndefinedColumn crash, but is NOT silent: it is recorded
                        # durably (sync_schema_drift) and surfaced so the operator
                        # applies the pending migration instead of losing the field.
                        if local_columns and key not in local_columns:
                            drifted_columns.append(key)
                            continue
                        processed_data[key] = prepare_value(key, value)

                    if drifted_columns:
                        # Fail loud (deduplicated) rather than a scrolling warning.
                        self.record_schema_drift(table, drifted_columns, row_id=row_id)

                    cols = list(processed_data.keys())
                    vals = [processed_data[c] for c in cols] if cols else []

                    if cols:
                        col_list = ", ".join([f'"{c}"' for c in cols])
                        placeholders = ", ".join(["%s"] * len(cols))
                        set_clause = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in cols])

                        sql_upsert = f"""
                            INSERT INTO {full_table} ({col_list})
                            VALUES ({placeholders})
                            ON CONFLICT (id) DO UPDATE SET {set_clause};
                        """

                        cur.execute(sql_upsert, vals)
                        LOG.debug("✓ Applied remote update for %s id=%s", table, row_id)
                    else:
                        LOG.warning("⚠️  No data to apply for %s id=%s", table, row_id)

                    conn.commit()
                    return True

            except psycopg2.errors.ForeignKeyViolation as fk_error:
                if conn:
                    conn.rollback()

                LOG.warning("")
                LOG.warning("=" * 80)
                LOG.warning("⚠️  FOREIGN KEY VIOLATION DETECTED")
                LOG.warning("=" * 80)

                error_str = str(fk_error)
                LOG.warning(f"   Error: {error_str[:300]}")

                # 🔧 ENHANCED: Parse FK violation to extract parent info
                import re

                # Extract FK column and value from error message
                # Format: Key (equipment_id)=(uuid) is not present in table "Inventory_equipment"
                fk_match = re.search(r'Key \((\w+)\)=\(([^)]+)\)', error_str)

                if fk_match:
                    fk_column = fk_match.group(1)
                    parent_id = fk_match.group(2)

                    LOG.info(f"   📋 Parsed FK details:")
                    LOG.info(f"      FK Column: {fk_column}")
                    LOG.info(f"      Parent ID: {parent_id}")

                    # Determine parent table from FK relationships
                    try:
                        conn_temp = self.pool.getconn()

                        try:
                            if "." in table:
                                schema, tbl = table.split(".", 1)
                            else:
                                schema, tbl = "public", table

                            with conn_temp.cursor(cursor_factory=RealDictCursor) as cur:
                                cur.execute("""
                                    SELECT
                                        ccu.table_schema || '.' || ccu.table_name as parent_table,
                                        ccu.column_name as parent_column
                                    FROM information_schema.table_constraints AS tc
                                    JOIN information_schema.key_column_usage AS kcu
                                        ON tc.constraint_name = kcu.constraint_name
                                        AND tc.table_schema = kcu.table_schema
                                    JOIN information_schema.constraint_column_usage AS ccu
                                        ON ccu.constraint_name = tc.constraint_name
                                    WHERE tc.constraint_type = 'FOREIGN KEY'
                                        AND tc.table_schema = %s
                                        AND tc.table_name = %s
                                        AND kcu.column_name = %s
                                    LIMIT 1
                                """, (schema, tbl, fk_column))

                                fk_info = cur.fetchone()

                                if fk_info:
                                    parent_table = fk_info['parent_table']

                                    LOG.info(f"   🎯 Identified parent table: {parent_table}")
                                    LOG.info("")
                                    LOG.info("   🔧 ATTEMPTING AUTO-RECOVERY FROM HQ...")
                                    LOG.info("=" * 80)

                                    # 🆕 TRY TO AUTO-RECOVER THE MISSING PARENT
                                    recovery_success = self.auto_recover_missing_parents_from_hq(
                                        table=table,
                                        row_id=row_id,
                                        fk_column=fk_column,
                                        parent_table=parent_table,
                                        parent_id=parent_id
                                    )

                                    if recovery_success:
                                        LOG.info("")
                                        LOG.info("=" * 80)
                                        LOG.info("✅ AUTO-RECOVERY SUCCESSFUL!")
                                        LOG.info("=" * 80)
                                        LOG.info(f"   Parent {parent_table}[{parent_id}] recovered from HQ")
                                        LOG.info(f"   🔄 RETRYING child record insertion...")
                                        LOG.info("=" * 80)

                                        # RETRY the original update now that parent exists
                                        return self.apply_remote_update_locally(table, payload)
                                    else:
                                        LOG.error("")
                                        LOG.error("=" * 80)
                                        LOG.error("❌ AUTO-RECOVERY FAILED")
                                        LOG.error("=" * 80)
                                        LOG.error(f"   Could not recover {parent_table}[{parent_id}] from HQ")
                                        LOG.error(f"   This is an orphaned reference!")
                                        LOG.error("=" * 80)
                                        return False
                                else:
                                    LOG.error(f"   ❌ Could not determine parent table for FK: {fk_column}")
                                    return False
                        finally:
                            self.pool.putconn(conn_temp)

                    except Exception as lookup_error:
                        LOG.error(f"   ❌ Error during FK lookup: {lookup_error}")
                        return False
                else:
                    LOG.error("   ❌ Could not parse FK violation error")
                    return False

            except psycopg2.errors.UniqueViolation as uv_error:
                # ── Duplicate value on a non-PK unique constraint (e.g. certificate_number,
                # or (equipment_id, scheduled_month) on calibration schedules) ──
                # The upsert only conflicts on (id), so a second unique column that already
                # belongs to a *different* local row triggers this — typically two branches
                # independently creating "the same" record (e.g. a next-cycle schedule) before
                # they'd synced with each other.
                #
                # HQ is the source of truth: auto-resolve by re-pointing every local reference
                # from the local duplicate onto the incoming HQ row, deleting the local
                # duplicate, then retrying — instead of leaving both sides stuck indefinitely
                # (see the 2026-07-26 incident: a duplicate schedule row blocked its session
                # and certificate from ever syncing, with no automatic recovery).
                if conn:
                    conn.rollback()

                error_msg = str(uv_error)
                # Extract the constraint and duplicate value for a helpful log message
                try:
                    constraint = error_msg.split('constraint "')[1].split('"')[0]
                except Exception:
                    constraint = "unknown"
                try:
                    dup_columns = error_msg.split("Key (")[1].split(")=")[0]
                    dup_values = error_msg.split(")=(")[1].split(")")[0]
                    dup_value = dup_columns + "=" + dup_values
                except Exception:
                    dup_columns = dup_values = None
                    dup_value = error_msg[:120]

                if dup_columns and dup_values and row_id:
                    try:
                        resolved = self._resolve_unique_conflict_hq_wins(
                            schema=schema,
                            table=tbl,
                            dup_columns=dup_columns,
                            dup_values=dup_values,
                            remote_row_id=row_id,
                        )
                    except Exception as resolve_error:
                        resolved = False
                        LOG.error(f"   ❌ Auto-resolve raised an error for {table}: {resolve_error}")

                    if resolved:
                        LOG.info(
                            f"✅ Auto-resolved unique conflict on {table} ({constraint}) — "
                            f"HQ's row wins, local duplicate retired. Retrying."
                        )
                        return self.apply_remote_update_locally(table, payload)

                LOG.warning("")
                LOG.warning("=" * 80)
                LOG.warning("⚠️  UNIQUE CONSTRAINT VIOLATION — COULD NOT AUTO-RESOLVE, SKIPPING")
                LOG.warning("=" * 80)
                LOG.warning(f"   Table     : {table}")
                LOG.warning(f"   Row ID    : {row_id}")
                LOG.warning(f"   Constraint: {constraint}")
                LOG.warning(f"   Duplicate : {dup_value}")
                LOG.warning("")
                LOG.warning("   Auto-resolution (HQ wins) did not find a matching local row to")
                LOG.warning("   retire, or failed to re-point its references safely.")
                LOG.warning("   The remote record has been SKIPPED to protect local data integrity.")
                LOG.warning("   Review both records manually and merge if needed.")
                LOG.warning("=" * 80)
                LOG.warning("")
                # Return True so the download loop marks this record as processed and
                # advances the checkpoint — we don't want to retry it on every cycle.
                return True

            except psycopg2.errors.NotNullViolation as null_error:
                # ✅ NEW: Enhanced NULL violation error handler
                if conn:
                    conn.rollback()

                # Extract column name from error message
                error_msg = str(null_error)
                try:
                    column_match = error_msg.split('column "')[1].split('"')[0]
                except:
                    column_match = 'unknown'

                try:
                    failing_row = error_msg.split('Failing row contains (')[1].split(')')[0]
                except:
                    failing_row = 'not available'

                LOG.error("")
                LOG.error("=" * 80)
                LOG.error("💥 NULL CONSTRAINT VIOLATION")
                LOG.error("=" * 80)
                LOG.error(f"   Table: {table}")
                LOG.error(f"   Row ID: {row_id}")
                LOG.error(f"   Column: {column_match}")
                LOG.error(f"   Operation: {operation}")
                LOG.error(f"   Data keys: {list(data.keys()) if data else 'NONE'}")
                LOG.error("=" * 80)
                LOG.error("")
                LOG.error("🔍 DIAGNOSTIC INFORMATION:")
                LOG.error(f"   • The column '{column_match}' requires a value but received NULL")
                LOG.error(f"   • Failing row: ({failing_row})")
                LOG.error(f"   • This indicates incomplete data from HQ server")
                LOG.error("")
                LOG.error("📊 DATA ANALYSIS:")
                if data:
                    LOG.error(f"   Received {len(data)} fields:")
                    for key, value in list(data.items())[:10]:  # Show first 10
                        value_str = 'NULL' if value is None else f'{type(value).__name__}'
                        LOG.error(f"      • {key}: {value_str}")
                    if len(data) > 10:
                        LOG.error(f"      ... and {len(data) - 10} more fields")
                else:
                    LOG.error("   ⚠️  NO DATA RECEIVED FROM SERVER")
                LOG.error("")
                LOG.error("🛠️  TROUBLESHOOTING STEPS:")
                LOG.error(f"   1. SSH to HQ server")
                LOG.error(f"   2. Check audit_log:")
                LOG.error(f"      SELECT data FROM audit_log WHERE row_id = '{row_id}' ORDER BY received_at DESC LIMIT 1;")
                LOG.error(f"   3. Check source table:")
                LOG.error(f"      SELECT * FROM {table} WHERE id = '{row_id}';")
                LOG.error(f"   4. Verify server logs:")
                LOG.error(f"      tail -f logs/sync_server.log | grep '{row_id}'")
                LOG.error("")
                LOG.error("💡 COMMON CAUSES:")
                LOG.error("   • Server fetch_updates_since() not fetching row data")
                LOG.error("   • audit_log.data field is empty/NULL")
                LOG.error("   • Row deleted before sync completed")
                LOG.error("   • Database constraint mismatch between HQ and client")
                LOG.error("")
                LOG.error("🔧 TECHNICAL DETAILS:")
                LOG.error(f"   {error_msg}")
                LOG.error("=" * 80)
                LOG.error("")

                return False

            except Exception as e:
                # ✅ ENHANCED: Better generic error handler
                if conn:
                    conn.rollback()

                LOG.error("")
                LOG.error("=" * 80)
                LOG.error("💥 UNEXPECTED ERROR APPLYING UPDATE")
                LOG.error("=" * 80)
                LOG.error(f"   Table: {table}")
                LOG.error(f"   Row ID: {row_id}")
                LOG.error(f"   Operation: {operation}")
                LOG.error(f"   Error Type: {type(e).__name__}")
                LOG.error(f"   Error Message: {str(e)}")
                LOG.error("=" * 80)
                LOG.error("")
                LOG.error("📊 UPDATE DETAILS:")
                LOG.error(f"   • Has data: {bool(data)}")
                if data:
                    LOG.error(f"   • Data fields: {len(data)}")
                    LOG.error(f"   • Sample keys: {list(data.keys())[:5]}")
                LOG.error(f"   • Timestamp: {remote_updated_at}")
                LOG.error("")
                LOG.error("🔍 STACK TRACE:")
                LOG.exception(e)
                LOG.error("=" * 80)
                LOG.error("")

                return False

            finally:
                if conn:
                    self.pool.putconn(conn)

    def _resolve_unique_conflict_hq_wins(
        self,
        schema: str,
        table: str,
        dup_columns: str,
        dup_values: str,
        remote_row_id: str,
    ) -> bool:
        """
        HQ is the source of truth. Called when a downloaded HQ row fails to
        insert locally because a *different* local row already occupies the
        same non-PK unique slot (e.g. two branches each auto-created their own
        "next cycle" schedule for the same equipment+month before syncing).

        Finds the local row occupying that slot, re-points every local FK
        reference to it onto ``remote_row_id`` (HQ's row), deletes the local
        duplicate, and returns True so the caller can retry the insert.

        Returns False (touches nothing) if the unique columns/values can't be
        parsed, no matching local row is found, or anything looks unsafe —
        the caller then falls back to skip-and-log.
        """
        columns = [c.strip().strip('"') for c in dup_columns.split(",")]
        values = [v.strip() for v in dup_values.split(",")]
        if not columns or len(columns) != len(values):
            return False

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Resolve the table's real primary-key column instead of
                # assuming "id" — safer if a future table differs.
                cur.execute(
                    """
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema = %s AND tc.table_name = %s
                    """,
                    (schema, table),
                )
                pk_row = cur.fetchone()
                if not pk_row:
                    return False
                pk_column = pk_row["column_name"]

                where_clause = " AND ".join(f'"{c}" = %s' for c in columns)
                cur.execute(
                    f'SELECT "{pk_column}" AS pk FROM "{table}" '
                    f'WHERE {where_clause} AND "{pk_column}" != %s',
                    (*values, remote_row_id),
                )
                local_row = cur.fetchone()
                if not local_row:
                    # No conflicting local row found under this exact reading —
                    # not safe to guess further.
                    return False
                local_loser_id = local_row["pk"]

                # Every table with a FK pointing at this one — re-home their
                # rows onto HQ's id before the local duplicate is deleted.
                cur.execute(
                    """
                    SELECT tc.table_name, kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                        ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND ccu.table_schema = %s AND ccu.table_name = %s
                    """,
                    (schema, table),
                )
                referencing = cur.fetchall()

                for ref in referencing:
                    cur.execute(
                        f'UPDATE "{ref["table_name"]}" SET "{ref["column_name"]}" = %s '
                        f'WHERE "{ref["column_name"]}" = %s',
                        (remote_row_id, local_loser_id),
                    )
                    if cur.rowcount:
                        LOG.info(
                            f"   ↳ re-pointed {cur.rowcount} row(s) in "
                            f"{ref['table_name']}.{ref['column_name']} from "
                            f"{local_loser_id} to {remote_row_id}"
                        )

                cur.execute(f'DELETE FROM {identifier(table)} WHERE {identifier(pk_column)} = %s', (local_loser_id,))
                conn.commit()
                LOG.info(
                    f"   ↳ retired local duplicate {table}[{local_loser_id}] "
                    f"in favour of HQ's {remote_row_id}"
                )
                return True
        except Exception as resolve_error:
            if conn:
                conn.rollback()
            LOG.error(f"   ❌ Auto-resolve failed for {table} unique conflict: {resolve_error}")
            return False
        finally:
            if conn:
                self.pool.putconn(conn)
