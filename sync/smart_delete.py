from .agent_prelude import LOG
from typing import List, Dict
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
try:
    from .sql_ident import qualified
except ImportError:  # loaded as a top-level module with sync/ on sys.path
    from sql_ident import qualified


class SmartDeleteMixin:

    def detect_local_deletes_with_dependencies(self, table: str, since_ts: str) -> List[Dict]:
        """
        Detect deleted records and determine if they have dependencies.
        Returns list of delete events with smart delete metadata.
        """
        conn = None
        try:
            conn = self.pool.getconn()

            # Get records marked for deletion (pending_delete = TRUE)
            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            full_table = qualified(schema, tbl)

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

            if "pending_delete" not in status_columns:
                return []  # Table doesn't support soft delete

            # Find records marked for deletion
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT
                        id,
                        to_jsonb(t.*) || jsonb_build_object('source_updated_at', t.updated_at) as row_data,
                        updated_at,
                        pending_delete,
                        active_status
                    FROM {full_table} t
                    WHERE pending_delete = TRUE
                    AND updated_at > %s
                    ORDER BY updated_at ASC
                    LIMIT 100
                """,
                    (since_ts,),
                )

                deleted_records = cur.fetchall()

                delete_events = []

                for record in deleted_records:
                    row_id = str(record["id"])

                    # Check if this record has dependencies
                    has_deps = self.check_local_dependencies(conn, table, row_id)

                    delete_event = {
                        "event_id": f"delete-{table}-{row_id}-{uuid.uuid4().hex[:8]}",
                        "table": table,
                        "row_id": row_id,
                        "operation": "d",
                        "data": {
                            "id": row_id,
                            "_has_dependencies": has_deps,
                            "_local_soft_delete": True,
                            # Don't include full record data for deletes
                        },
                        "created_at": record["updated_at"].isoformat(),
                        "source": "local",
                        "machine_id": self.machine_id,
                        "metadata": {"has_dependencies": has_deps, "local_soft_delete": True},
                    }

                    delete_events.append(delete_event)

                    LOG.info(f"Detected local delete: {table}[{row_id}] (has_deps={has_deps})")

                return delete_events

        except Exception as e:
            LOG.error(f"Error detecting deletes for {table}: {e}")
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def check_local_dependencies(self, conn, table: str, row_id: str) -> bool:
        """
        ENHANCED: Comprehensive check if a record has ANY foreign key dependencies.
        Returns True if dependencies exist (checks ALL tables, not just tracked ones).

        This prevents hard delete of records that have dependent data,
        avoiding orphaned records and data integrity issues.
        """
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get ALL tables that have foreign keys pointing to this table
                # Removed LIMIT to check all dependencies
                cur.execute(
                    """
                    SELECT
                        tc.table_schema AS child_schema,
                        tc.table_name AS child_table,
                        kcu.column_name AS child_fk_column
                    FROM
                        information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage AS ccu
                        ON ccu.constraint_name = tc.constraint_name
                    WHERE
                        tc.constraint_type = 'FOREIGN KEY'
                        AND ccu.table_schema = %s
                        AND ccu.table_name = %s
                """,
                    (schema, tbl),
                )

                child_tables = cur.fetchall()

                if not child_tables:
                    LOG.debug(f"   No FK relationships found for {table}")
                    return False

                LOG.debug(
                    f"   Checking {len(child_tables)} potential child tables for dependencies..."
                )

                # Check each child table for actual records
                for child in child_tables:
                    child_schema = child["child_schema"]
                    child_table = child["child_table"]
                    fk_column = child["child_fk_column"]

                    child_table_full = f"{child_schema}.{child_table}"

                    try:
                        # Count ANY records that reference this parent
                        # This checks ALL records, not just active ones
                        cur.execute(
                            f"""
                            SELECT COUNT(*) as count
                            FROM "{child_schema}"."{child_table}"
                            WHERE "{fk_column}" = %s
                        """,
                            (row_id,),
                        )

                        result = cur.fetchone()

                        if result and result["count"] > 0:
                            LOG.info(
                                f"   ✓ Found {result['count']} dependencies in {child_table_full}"
                            )
                            # Return immediately on first dependency found
                            return True
                        else:
                            LOG.debug(f"   ○ No dependencies in {child_table_full}")

                    except psycopg2.Error as e:
                        # If we can't check a table, log it but continue
                        LOG.debug(f"   ⚠ Could not check {child_table_full}: {str(e)[:100]}")
                        continue
                    except Exception as e:
                        LOG.debug(
                            f"   ⚠ Unexpected error checking {child_table_full}: {str(e)[:100]}"
                        )

                # If we checked all tables and found no dependencies
                LOG.debug(f"   ✓ No dependencies found for {table}[{row_id}]")
                return False

        except psycopg2.Error as e:
            LOG.warning(f"Database error checking dependencies for {table}[{row_id}]: {e}")
            # On database error, assume dependencies exist (safer approach)
            return True
        except Exception as e:
            LOG.warning(f"Unexpected error checking dependencies for {table}[{row_id}]: {e}")
            # On any error, assume dependencies exist (safer approach)
            return True

    def apply_smart_delete_from_hq(self, table: str, payload: Dict) -> bool:
        """
        Apply smart delete from HQ to local database.
        Handles both soft_delete and hard_delete operations.
        """
        operation_type = payload.get("operation")
        row_id = payload.get("row_id")

        LOG.info(f"Applying {operation_type} from HQ: {table}[{row_id}]")

        conn = None
        try:
            conn = self.pool.getconn()

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            full_table = qualified(schema, tbl)

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if operation_type == "soft_delete":
                    # Apply soft delete locally
                    cur.execute(
                        f"""
                        UPDATE {full_table}
                        SET pending_delete = TRUE,
                            active_status = FALSE,
                            updated_at = NOW()
                        WHERE id = %s
                    """,
                        (row_id,),
                    )

                    LOG.info(f"   ✓ Applied soft delete locally")

                elif operation_type == "hard_delete":
                    # Apply hard delete locally
                    cur.execute(
                        f"""
                        DELETE FROM {full_table}
                        WHERE id = %s
                    """,
                        (row_id,),
                    )

                    LOG.info(f"   🗑️ Applied hard delete locally")

                conn.commit()
                return True

        except psycopg2.errors.ForeignKeyViolation as e:
            if conn:
                conn.rollback()
            LOG.warning(f"FK violation applying delete: {e}")
            LOG.info(f"   Attempting to apply as soft delete instead...")

            # Try soft delete as fallback
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE {full_table}
                        SET pending_delete = TRUE,
                            active_status = FALSE,
                            updated_at = NOW()
                        WHERE id = %s
                    """,
                        (row_id,),
                    )
                    conn.commit()
                    LOG.info(f"   ✓ Applied as soft delete instead")
                    return True
            except Exception as e2:
                LOG.error(f"   ✗ Soft delete fallback also failed: {e2}")
                if conn:
                    conn.rollback()
                return False

        except Exception as e:
            LOG.error(f"Error applying delete from HQ: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

    def detect_local_restores(self, table: str, since_ts: str) -> List[Dict]:
        """
        ✅ FIXED: Detect records that were restored (activated) locally.
        Now properly clears soft delete tracking to allow re-deletion.
        """
        conn = None
        try:
            conn = self.pool.getconn()

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            full_table = qualified(schema, tbl)

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
                LOG.debug(f"   Table {table} doesn't support restore operations")
                return []

            # Find restore operations from audit log
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        event_id,
                        row_id,
                        data,
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
                    return []

                LOG.info(f"📋 Found {len(audit_restores)} restore operations for {table}")

                restore_events = []
                restored_ids = []

                for audit_record in audit_restores:
                    row_id = str(audit_record["row_id"])
                    event_id = audit_record["event_id"]

                    # Verify the record exists and is active
                    cur.execute(
                        f"""
                        SELECT
                            id,
                            to_jsonb(t.*) || jsonb_build_object('source_updated_at', t.updated_at) as row_data,
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
                        LOG.warning(f"   ⚠️ Record {row_id} not found (may have been deleted)")
                        continue

                    # Verify it's actually active
                    is_active = (
                        current_record.get("active_status") == True
                        or current_record.get("pending_delete") == False
                    )

                    if not is_active:
                        LOG.warning(f"   ⚠️ Record {row_id} is not active (skipping)")
                        continue

                    # Create restore event
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
                        "metadata": {"restore_operation": True, "restored_from_audit": True},
                    }

                    restore_events.append(restore_event)
                    restored_ids.append(row_id)
                    LOG.info(f"✅ Detected local restore: {table}[{row_id}]")

                # 🔥 CRITICAL FIX: Clear soft delete tracking for restored records
                if restored_ids:
                    LOG.info(
                        f"🧹 Clearing soft delete tracking for {len(restored_ids)} restored records..."
                    )
                    self._clear_soft_delete_tracking_for_records(table, restored_ids)

                if restore_events:
                    LOG.info(f"🎉 Prepared {len(restore_events)} restore events for sync")

                return restore_events

        except Exception as e:
            LOG.error(f"Error detecting restores for {table}: {e}")
            LOG.exception(e)
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def _clear_soft_delete_tracking_for_records(self, table: str, row_ids: List[str]):
        """
        🔥 NEW: Clear soft delete tracking for specific records.
        This allows them to be detected as deleted again after restore.
        """
        state_key = f"synced_soft_deletes_{table}"
        synced_soft_deletes_str = self.state.get(state_key, "")
        synced_soft_deletes = (
            set(synced_soft_deletes_str.split(",")) if synced_soft_deletes_str else set()
        )

        # Entries are "id@version" (bare ids from older builds still match).
        wanted = {str(r) for r in row_ids}
        cleared = {k for k in synced_soft_deletes if k.split("@", 1)[0] in wanted}
        synced_soft_deletes -= cleared
        cleared_count = len(cleared)

        # Save updated tracking
        self.state.set(state_key, ",".join(synced_soft_deletes))

        if cleared_count > 0:
            LOG.info(f"   ✅ Cleared {cleared_count} records from soft delete tracking")
            LOG.info(f"   📝 These records can now be deleted again")

    def clear_soft_delete_tracking(self, table: str, row_id: str = None):
        """
        🔧 ENHANCED: Clear soft delete tracking for emergency cleanup.
        """
        state_key = f"synced_soft_deletes_{table}"

        if row_id:
            # Clear specific record
            synced_soft_deletes_str = self.state.get(state_key, "")
            synced_soft_deletes = (
                set(synced_soft_deletes_str.split(",")) if synced_soft_deletes_str else set()
            )

            cleared = {k for k in synced_soft_deletes if k.split("@", 1)[0] == str(row_id)}
            if cleared:
                synced_soft_deletes -= cleared
                self.state.set(state_key, ",".join(synced_soft_deletes))
                LOG.info(f"🧹 Cleared soft delete tracking for {table}[{row_id}]")
            else:
                LOG.info(f"ℹ️  {table}[{row_id}] was not in tracking")
        else:
            # Clear entire table tracking
            self.state.set(state_key, "")
            LOG.info(f"🧹 Cleared ALL soft delete tracking for {table}")

    def mark_record_for_deletion(self, table: str, row_id: str) -> bool:
        """
        Mark a record for soft deletion locally before syncing to HQ.
        This ensures proper soft delete flow: local soft delete → HQ evaluation → proper action
        """
        conn = None
        try:
            conn = self.pool.getconn()

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            full_table = qualified(schema, tbl)

            # Get schema info
            schema_info = self.get_table_schema_info(table)

            with conn.cursor() as cur:
                # Mark as pending_delete instead of actually deleting
                if schema_info["has_pending_delete"]:
                    cur.execute(
                        f"""
                        UPDATE {full_table}
                        SET pending_delete = TRUE,
                            active_status = FALSE,
                            updated_at = NOW()
                        WHERE id = %s
                    """,
                        (row_id,),
                    )

                    LOG.info(f"✅ Marked {table}[{row_id}] for deletion (soft delete)")

                    conn.commit()
                    return True
                else:
                    LOG.warning(f"⚠️ Table {table} doesn't support soft delete")

        except Exception as e:
            LOG.error(f"Failed to mark {table}[{row_id}] for deletion: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

    def discover_recent_changes_with_smart_delete(self) -> List[Dict]:
        """
        Enhanced version of discover_recent_changes that detects smart deletes.
        Replace your existing discover_recent_changes with this.
        """
        last_upload_time = self.get_last_upload_time()
        events = []

        LOG.debug("Looking for changes since: %s", last_upload_time)

        for table in self.tables:
            # Get regular updates
            table_events = self.fetch_recent_changes_for_table(table, last_upload_time)
            events.extend(table_events)

            # Get smart delete events
            delete_events = self.detect_local_deletes_with_dependencies(table, last_upload_time)
            events.extend(delete_events)

        if events:
            delete_count = sum(1 for e in events if e.get("operation") == "d")
            update_count = len(events) - delete_count

            LOG.info(
                f"Discovered {len(events)} changes: {update_count} updates, {delete_count} deletes"
            )

        return events

    def apply_remote_update_with_smart_delete(self, table: str, payload: Dict) -> bool:
        """
        Enhanced version that handles smart delete operations from HQ.
        Replace apply_remote_update_locally call with this.
        """
        operation = payload.get("operation", "u")

        # Handle smart delete operations
        if operation in ["soft_delete", "hard_delete"]:
            return self.apply_smart_delete_from_hq(table, payload)

        # Handle regular operations
        return self.apply_remote_update_locally(table, payload)


class ClientSmartDelete:
    """
    Helper class for sync agent to request smart deletes from HQ
    """

    def __init__(self, agent):
        self.agent = agent
        self.api_url = agent.api_url
        self.headers = agent._http_headers()

    def request_smart_delete(self, table: str, row_id: str, force_hard: bool = False) -> Dict:
        """
        Request HQ to perform smart delete on a record.
        HQ will decide between soft and hard delete based on dependencies.
        """
        try:
            payload = {
                "table": table,
                "row_id": row_id,
                "client_id": self.agent.client_id,
                "force_hard_delete": force_hard,
            }

            LOG.info(f"Requesting smart delete from HQ: {table}[{row_id}]")

            response = requests.post(
                f"{self.api_url}/smart_delete", json=payload, headers=self.headers, timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                LOG.info(f"✓ HQ processed delete: {result.get('operation')}")
                LOG.info(f"   Message: {result.get('message')}")
                return result
            else:
                LOG.error(f"✗ Smart delete request failed: {response.status_code}")
                return {"status": "error", "message": response.text}

        except Exception as e:
            LOG.error(f"Error requesting smart delete: {e}")
            return {"status": "error", "message": str(e)}

    def check_dependencies_before_delete(self, table: str, row_id: str) -> Dict:
        """
        Check dependencies before attempting delete.
        Helps UI show warning to users.
        """
        try:
            payload = {"table": table, "row_id": row_id}

            response = requests.post(
                f"{self.api_url}/check_dependencies", json=payload, headers=self.headers, timeout=10
            )

            if response.status_code == 200:
                result = response.json()

                if result.get("has_dependencies"):
                    LOG.info(
                        f"⚠️  {table}[{row_id}] has {result.get('total_dependencies')} dependencies"
                    )

                    for dep in result.get("dependencies", []):
                        LOG.info(f"      ↳ {dep['child_table']}: {dep['count']} records")

                return result
            else:
                return {"has_dependencies": False, "error": response.text}

        except Exception as e:
            LOG.error(f"Error checking dependencies: {e}")
            return {"has_dependencies": False, "error": str(e)}
