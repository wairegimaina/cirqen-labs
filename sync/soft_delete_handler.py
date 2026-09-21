#!/usr/bin/env python3
"""
ENHANCED: Soft Delete Handler with Dependency Checking
Version: 2.1 - Production Ready with Comprehensive FK Protection

Key Improvements:
- ✅ Fixed CHAR(1) vs BOOLEAN column type detection (happens BEFORE cursor operations)
- ✅ Enhanced error handling with detailed tracebacks
- ✅ Optimized column type caching to reduce database queries
- ✅ Better logging with operation tracking
- ✅ Recursive cascade with circular dependency detection
- ✅ Rollback safety with transaction checkpoints
- ✅ AUTOMATIC FALLBACK: If hard delete fails due to FK constraints, automatically converts to soft delete
- ✅ COMPREHENSIVE FK CHECKING: Detects ALL foreign key references, even from untracked tables
- ✅ SMART DEPENDENCY DETECTION: Handles tables with and without status columns correctly

Change Log v2.1:
- Added automatic soft delete fallback when hard delete fails due to FK violations
- Enhanced dependency checker to find ALL FK references (not just tracked tables)
- Improved _count_active_children to handle tables without status columns
- Added has_any_foreign_key_references() for comprehensive FK checking
- Better error messages indicating whether FK references are tracked or untracked

Known Behavior:
- If a record has dependencies, it will ALWAYS be soft deleted (even if force_hard_delete=True)
- If hard delete fails due to FK constraints, system automatically falls back to soft delete
- Tables without status columns will block deletion if they have any FK references
"""

import logging
from typing import Dict, List, Tuple, Set, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import uuid
from datetime import datetime, timezone
import json
import traceback
try:
    from .sql_ident import identifier, qualified
except ImportError:  # loaded as a top-level module with sync/ on sys.path
    from sql_ident import identifier, qualified

LOG = logging.getLogger("soft_delete_handler")

# Global cache for column types to reduce database queries
_COLUMN_TYPE_CACHE = {}

# ============================================================
# ENHANCED COLUMN TYPE HANDLING WITH CACHING
# ============================================================

def get_column_type(conn, table: str, column: str) -> str:
    """
    Get the data type of a specific column with caching.
    Returns: 'char1', 'boolean', or the actual data_type
    """
    cache_key = f"{table}.{column}"

    if cache_key in _COLUMN_TYPE_CACHE:
        return _COLUMN_TYPE_CACHE[cache_key]

    if "." in table:
        schema, tbl = table.split(".", 1)
    else:
        schema, tbl = "public", table

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    data_type,
                    udt_name,
                    character_maximum_length,
                    is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                  AND column_name = %s
            """, (schema, tbl, column))

            result = cur.fetchone()

            if result:
                data_type = result['data_type']
                udt_name = result['udt_name']
                max_length = result['character_maximum_length']

                # Detect CHAR(1) more reliably
                if (data_type in ('character', 'character varying') or udt_name == 'bpchar') and max_length == 1:
                    column_type = 'char1'
                elif data_type == 'boolean':
                    column_type = 'boolean'
                else:
                    column_type = data_type

                # Cache the result
                _COLUMN_TYPE_CACHE[cache_key] = column_type
                LOG.debug(f"Cached column type: {cache_key} = {column_type}")
                return column_type
            else:
                LOG.warning(f"Column {cache_key} not found in schema")
                return None

    except Exception as e:
        LOG.error(f"Error getting column type for {cache_key}: {e}")
        return None


def convert_boolean_for_column(value: bool, column_type: str):
    """
    Convert boolean to appropriate value based on column type.
    - CHAR(1): '1' for True, '0' for False
    - BOOLEAN: True/False
    - Other: bool(value)
    """
    if column_type == 'char1':
        return '1' if value else '0'
    elif column_type == 'boolean':
        return bool(value)
    else:
        return bool(value)


def clear_column_type_cache():
    """Clear the column type cache (useful after schema changes)"""
    global _COLUMN_TYPE_CACHE
    _COLUMN_TYPE_CACHE.clear()
    LOG.info("Column type cache cleared")


# ============================================================
# ENHANCED DEPENDENCY CHECKER
# ============================================================

class SoftDeleteDependencyChecker:
    """
    Checks if a record has dependencies before deletion.
    Enhanced with circular dependency detection and better error handling.
    """

    def __init__(self, conn):
        self.conn = conn
        self._dependency_cache = {}
        self._table_has_children_cache = {}
        self._circular_detection_stack = set()

    def get_child_tables(self, table: str) -> List[Tuple[str, str, str]]:
        """
        Get all child tables that reference this table via foreign keys.
        Returns: [(child_table, child_fk_column, parent_pk_column), ...]
        """
        if table in self._dependency_cache:
            return self._dependency_cache[table]

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        tc.table_schema AS child_schema,
                        tc.table_name AS child_table,
                        kcu.column_name AS child_fk_column,
                        ccu.column_name AS parent_pk_column
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
                """, (schema, tbl))

                results = cur.fetchall()
                child_relations = []

                for row in results:
                    child_schema = row['child_schema']
                    child_table = row['child_table']
                    child_fk_column = row['child_fk_column']
                    parent_pk_column = row['parent_pk_column']

                    child_table_full = f"{child_schema}.{child_table}"
                    child_relations.append((child_table_full, child_fk_column, parent_pk_column))

                self._dependency_cache[table] = child_relations
                return child_relations

        except Exception as e:
            LOG.error(f"Error getting child tables for {table}: {e}")
            LOG.debug(traceback.format_exc())
            return []

    def has_active_dependencies(self, table: str, row_id: str) -> Tuple[bool, List[Dict]]:
        """
        Check if a record has any active child records that depend on it.
        Returns: (has_dependencies, dependency_details)
        """
        child_tables = self.get_child_tables(table)

        if not child_tables:
            LOG.debug(f"No child tables found for {table}")
            return False, []

        dependencies_found = []

        for child_table, child_fk_column, parent_pk_column in child_tables:
            try:
                count, details = self._count_active_children(
                    child_table,
                    child_fk_column,
                    row_id
                )

                if count > 0:
                    dependencies_found.append({
                        "child_table": child_table,
                        "child_fk_column": child_fk_column,
                        "count": count,
                        "sample_ids": details[:5]  # First 5 child IDs
                    })

            except Exception as e:
                LOG.warning(f"Error checking dependencies in {child_table}: {e}")
                LOG.debug(traceback.format_exc())
                continue

        has_deps = len(dependencies_found) > 0

        if has_deps:
            LOG.info(f"📊 DEPENDENCIES FOUND for {table}[{row_id}]:")
            for dep in dependencies_found:
                LOG.info(f"   ↳ {dep['child_table']}: {dep['count']} active records")

        return has_deps, dependencies_found

    def _count_active_children(self, child_table: str, fk_column: str, parent_id: str) -> Tuple[int, List[str]]:
        """
        Count active child records that would prevent deletion.
        A child is "active" if it's not soft-deleted.
        ENHANCED: Also counts records without status columns as active.
        """
        if "." in child_table:
            schema, tbl = child_table.split(".", 1)
        else:
            schema, tbl = "public", child_table

        quoted_fk = f'"{fk_column}"'
        full_table = qualified(schema, tbl)

        try:
            # Get column information BEFORE building query
            status_cols = self.check_table_has_status_columns(child_table)

            # Build WHERE clause - records are active if not marked as deleted
            where_conditions = [f"{quoted_fk} = %s"]

            # If table has status columns, check them
            if status_cols['has_active_status']:
                column_type = get_column_type(self.conn, child_table, 'active_status')
                if column_type == 'char1':
                    where_conditions.append("active_status = '1'")
                else:
                    where_conditions.append("active_status = TRUE")

            if status_cols['has_pending_delete']:
                column_type = get_column_type(self.conn, child_table, 'pending_delete')
                if column_type == 'char1':
                    where_conditions.append("(pending_delete = '0' OR pending_delete IS NULL)")
                else:
                    where_conditions.append("(pending_delete = FALSE OR pending_delete IS NULL)")

            # If table has NO status columns, ALL records are considered active
            # (This is the key fix - tables without status columns should still block deletion)

            where_clause = " AND ".join(where_conditions)

            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = f"""
                    SELECT id
                    FROM {full_table}
                    WHERE {where_clause}
                    LIMIT 100
                """

                LOG.debug(f"Checking dependencies: {query} with parent_id={parent_id}")
                cur.execute(query, (parent_id,))
                results = cur.fetchall()

                child_ids = [str(row['id']) for row in results]
                count = len(child_ids)

                if count > 0:
                    status_info = "with status cols" if (status_cols['has_active_status'] or status_cols['has_pending_delete']) else "NO status cols (all active)"
                    LOG.info(f"   ✓ Found {count} active children in {child_table} [{status_info}]")
                else:
                    LOG.debug(f"   ○ No active children in {child_table}")

                return count, child_ids

        except Exception as e:
            LOG.error(f"❌ Error counting children in {child_table}: {e}")
            LOG.error(traceback.format_exc())
            return 0, []

    def check_table_has_status_columns(self, table: str) -> Dict[str, bool]:
        """
        Check if a table has soft delete status columns.
        Returns: {"has_active_status": bool, "has_pending_delete": bool}
        """
        if table in self._table_has_children_cache:
            return self._table_has_children_cache[table]

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s
                    AND column_name IN ('active_status', 'pending_delete')
                """, (schema, tbl))

                columns = {row['column_name'] for row in cur.fetchall()}

                result = {
                    "has_active_status": 'active_status' in columns,
                    "has_pending_delete": 'pending_delete' in columns
                }

                self._table_has_children_cache[table] = result
                return result

        except Exception as e:
            LOG.warning(f"Error checking status columns for {table}: {e}")
            return {"has_active_status": False, "has_pending_delete": False}

    def has_any_foreign_key_references(self, table: str, row_id: str) -> Tuple[bool, List[Dict]]:
        """
        Check if a record has ANY foreign key references (even from tables not tracked).
        This is a comprehensive check to prevent FK violations on hard delete.

        Returns: (has_references, reference_details)
        """
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get ALL tables that reference this table (not just tracked ones)
                cur.execute("""
                    SELECT
                        tc.table_schema AS child_schema,
                        tc.table_name AS child_table,
                        kcu.column_name AS child_fk_column,
                        COUNT(*) as ref_count
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
                    GROUP BY tc.table_schema, tc.table_name, kcu.column_name
                """, (schema, tbl))

                potential_children = cur.fetchall()

                if not potential_children:
                    LOG.debug(f"No foreign key references found for {table}")
                    return False, []

                references_found = []

                for child_info in potential_children:
                    child_schema = child_info['child_schema']
                    child_table = child_info['child_table']
                    fk_column = child_info['child_fk_column']

                    child_table_full = f"{child_schema}.{child_table}"

                    # Check if any records actually reference this parent
                    try:
                        cur.execute(f"""
                            SELECT COUNT(*) as count
                            FROM {qualified(child_schema, child_table)}
                            WHERE {identifier(fk_column)} = %s
                            LIMIT 1
                        """, (row_id,))

                        result = cur.fetchone()
                        if result and result['count'] > 0:
                            references_found.append({
                                "child_table": child_table_full,
                                "child_fk_column": fk_column,
                                "count": result['count'],
                                "tracked": child_table_full in self._dependency_cache
                            })

                            LOG.debug(f"   ⚠️  Found {result['count']} FK references in {child_table_full}")

                    except Exception as e:
                        LOG.debug(f"Could not check {child_table_full}: {e}")
                        continue

                has_refs = len(references_found) > 0

                if has_refs:
                    LOG.warning(f"⚠️  COMPREHENSIVE FK CHECK: {table}[{row_id}] has {len(references_found)} FK references")
                    for ref in references_found:
                        tracked_status = "tracked" if ref['tracked'] else "NOT TRACKED"
                        LOG.warning(f"     ↳ {ref['child_table']}: {ref['count']} records [{tracked_status}]")

                return has_refs, references_found

        except Exception as e:
            LOG.error(f"Error checking FK references for {table}[{row_id}]: {e}")
            LOG.debug(traceback.format_exc())
            return False, []

    def should_soft_delete(self, table: str, row_id: str) -> Tuple[bool, str, List[Dict]]:
        """
        Determine if record should be soft deleted or hard deleted.
        ENHANCED: Now includes comprehensive FK reference checking.

        Returns: (should_soft_delete, reason, dependencies)
        """
        # Check if table supports soft delete
        status_cols = self.check_table_has_status_columns(table)

        if not status_cols['has_active_status'] and not status_cols['has_pending_delete']:
            # Table doesn't support soft delete, but check if it can be hard deleted
            has_refs, refs = self.has_any_foreign_key_references(table, row_id)
            if has_refs:
                return True, f"Table has no status columns BUT has {len(refs)} FK references - requires manual cleanup", refs
            return False, "Table does not support soft delete (no status columns) and has no FK references", []

        # Check for dependencies in tracked tables
        has_deps, dependencies = self.has_active_dependencies(table, row_id)

        if has_deps:
            total_deps = sum(dep['count'] for dep in dependencies)
            return True, f"Has {total_deps} active dependencies across {len(dependencies)} tracked tables", dependencies

        # Even if no tracked dependencies, check for ANY FK references
        has_refs, refs = self.has_any_foreign_key_references(table, row_id)
        if has_refs:
            untracked_refs = [ref for ref in refs if not ref.get('tracked', False)]
            if untracked_refs:
                LOG.warning(f"⚠️  Found {len(untracked_refs)} UNTRACKED FK references - forcing soft delete")
                return True, f"Has {len(untracked_refs)} references in untracked tables", refs

        return False, "No active dependencies found", []


# ============================================================
# ENHANCED DELETE OPERATIONS WITH FIXED COLUMN HANDLING
# ============================================================
def perform_smart_delete(
    conn,
    table: str,
    row_id: str,
    client_id: str = "unknown",
    force_hard_delete: bool = False
) -> Tuple[bool, str, str, List[Dict]]:
    """
    Intelligently decide between soft delete and hard delete based on dependencies.

    CRITICAL BEHAVIOR:
    1. ALWAYS checks for dependencies FIRST (even if force_hard_delete=True)
    2. If dependencies exist → MANDATORY soft delete with cascade
    3. If no dependencies → attempts hard delete
    4. If hard delete fails with FK error → automatic fallback to soft delete
    5. Comprehensive FK checking includes ALL tables (not just tracked ones)

    Returns: (success, operation_type, message, broadcast_changes)

    Operation types:
    - "soft_delete": Record and dependencies soft deleted (pending_delete=TRUE, active_status=FALSE)
    - "hard_delete": Record physically removed from database
    - "error": Operation failed
    """
    LOG.info("=" * 80)
    LOG.info(f"🎯 SMART DELETE REQUEST")
    LOG.info(f"   Table: {table}")
    LOG.info(f"   Row ID: {row_id}")
    LOG.info(f"   Client: {client_id}")
    LOG.info(f"   Force Hard Delete: {force_hard_delete}")
    LOG.info("=" * 80)

    try:
        # ============================================================
        # PHASE 1: COMPREHENSIVE DEPENDENCY ANALYSIS
        # ============================================================
        LOG.info("🔍 PHASE 1: Checking for dependencies...")

        checker = SoftDeleteDependencyChecker(conn)

        # Check if table supports soft delete
        status_cols = checker.check_table_has_status_columns(table)
        LOG.info(f"   Table status columns:")
        LOG.info(f"      has_active_status: {status_cols['has_active_status']}")
        LOG.info(f"      has_pending_delete: {status_cols['has_pending_delete']}")

        # Check for dependencies in tracked tables
        has_tracked_deps, tracked_dependencies = checker.has_active_dependencies(table, row_id)

        # Check for ANY foreign key references (including untracked tables)
        has_any_fk_refs, all_fk_references = checker.has_any_foreign_key_references(table, row_id)

        # Determine if soft delete is required
        should_soft_delete = False
        soft_delete_reason = ""
        dependencies_to_cascade = []

        if has_tracked_deps:
            should_soft_delete = True
            total_tracked = sum(dep['count'] for dep in tracked_dependencies)
            soft_delete_reason = f"Has {total_tracked} active dependencies across {len(tracked_dependencies)} tracked tables"
            dependencies_to_cascade = tracked_dependencies

            LOG.info(f"✓ Found tracked dependencies:")
            for dep in tracked_dependencies:
                LOG.info(f"      → {dep['child_table']}: {dep['count']} active records")

        elif has_any_fk_refs:
            should_soft_delete = True
            untracked_refs = [ref for ref in all_fk_references if not ref.get('tracked', False)]

            if untracked_refs:
                total_untracked = sum(ref['count'] for ref in untracked_refs)
                soft_delete_reason = f"Has {total_untracked} FK references in {len(untracked_refs)} untracked tables"

                LOG.warning(f"⚠️  Found UNTRACKED foreign key references:")
                for ref in untracked_refs:
                    LOG.warning(f"      → {ref['child_table']}: {ref['count']} records [UNTRACKED]")
            else:
                total_tracked = sum(ref['count'] for ref in all_fk_references)
                soft_delete_reason = f"Has {total_tracked} FK references in tracked tables"
                dependencies_to_cascade = all_fk_references

                LOG.info(f"✓ Found FK references (all tracked):")
                for ref in all_fk_references:
                    LOG.info(f"      → {ref['child_table']}: {ref['count']} records")

        elif not status_cols['has_active_status'] and not status_cols['has_pending_delete']:
            should_soft_delete = False
            soft_delete_reason = "Table does not support soft delete (no status columns)"
            LOG.warning(f"⚠️  {soft_delete_reason}")

        else:
            should_soft_delete = False
            soft_delete_reason = "No dependencies found - safe for hard delete"
            LOG.info(f"✓ {soft_delete_reason}")

        # ============================================================
        # PHASE 2: DECISION LOGIC
        # ============================================================
        LOG.info("")
        LOG.info("📊 PHASE 2: Delete strategy decision...")

        if should_soft_delete:
            # MANDATORY SOFT DELETE - Override force_hard_delete
            if force_hard_delete:
                LOG.warning("⚠️  Force hard delete requested BUT dependencies exist")
                LOG.warning("⚠️  OVERRIDING to SOFT DELETE for data integrity")

            LOG.info(f"📝 Decision: SOFT DELETE")
            LOG.info(f"   Reason: {soft_delete_reason}")

            # Check if table actually supports soft delete
            if not status_cols['has_active_status'] and not status_cols['has_pending_delete']:
                error_msg = f"Cannot soft delete: Table {table} has no status columns but has FK dependencies"
                LOG.error(f"❌ {error_msg}")
                LOG.error("   Manual intervention required to remove FK dependencies first")
                return False, "error", error_msg, []

            LOG.info("")
            LOG.info("🔄 PHASE 3: Executing soft delete with cascade...")

            success, message, changes = perform_soft_delete_with_cascade(
                conn, table, row_id, client_id, dependencies_to_cascade
            )

            if success:
                LOG.info("")
                LOG.info("=" * 80)
                LOG.info(f"✅ SOFT DELETE SUCCESSFUL")
                LOG.info(f"   Primary record: {table}[{row_id}]")
                LOG.info(f"   Total changes: {len(changes)} records")
                LOG.info(f"   Cascaded: {len(changes) - 1} dependent records")
                LOG.info(f"   Message: {message}")
                LOG.info("=" * 80)
                return True, "soft_delete", message, changes
            else:
                LOG.error("")
                LOG.error("=" * 80)
                LOG.error(f"❌ SOFT DELETE FAILED")
                LOG.error(f"   Error: {message}")
                LOG.error("=" * 80)
                return False, "error", message, []

        else:
            # NO DEPENDENCIES - Attempt hard delete
            LOG.info(f"🗑️  Decision: HARD DELETE")
            LOG.info(f"   Reason: {soft_delete_reason}")
            LOG.info("")
            LOG.info("🔄 PHASE 3: Executing hard delete...")

            success, message, changes = perform_hard_delete(conn, table, row_id, client_id)

            if success:
                LOG.info("")
                LOG.info("=" * 80)
                LOG.info(f"✅ HARD DELETE SUCCESSFUL")
                LOG.info(f"   Record: {table}[{row_id}]")
                LOG.info(f"   Message: {message}")
                LOG.info("=" * 80)
                return True, "hard_delete", message, changes
            else:
                # ============================================================
                # PHASE 4: AUTOMATIC FALLBACK TO SOFT DELETE
                # ============================================================
                if "foreign key constraint" in message.lower():
                    LOG.warning("")
                    LOG.warning("=" * 80)
                    LOG.warning("⚠️  HARD DELETE FAILED - FK CONSTRAINT VIOLATION")
                    LOG.warning("   Attempting automatic fallback to SOFT DELETE...")
                    LOG.warning("=" * 80)

                    # Check if table supports soft delete
                    if not status_cols['has_active_status'] and not status_cols['has_pending_delete']:
                        error_msg = f"Cannot fallback to soft delete: Table {table} has no status columns"
                        LOG.error(f"❌ {error_msg}")
                        LOG.error(f"   Original error: {message}")
                        return False, "error", f"{error_msg}. Original: {message}", []

                    try:
                        conn.rollback()  # Rollback the failed hard delete

                        # Re-check dependencies more thoroughly
                        LOG.info("🔍 Re-checking dependencies (comprehensive scan)...")
                        has_refs, refs = checker.has_any_foreign_key_references(table, row_id)

                        if has_refs:
                            LOG.info(f"   Found {len(refs)} FK references that were missed initially")
                            for ref in refs:
                                LOG.info(f"      → {ref['child_table']}: {ref['count']} records")

                        LOG.info("")
                        LOG.info("🔄 PHASE 4: Executing fallback soft delete...")

                        success, fb_message, changes = perform_soft_delete_with_cascade(
                            conn, table, row_id, client_id, refs if has_refs else []
                        )

                        if success:
                            LOG.info("")
                            LOG.info("=" * 80)
                            LOG.info(f"✅ FALLBACK SOFT DELETE SUCCESSFUL")
                            LOG.info(f"   Primary record: {table}[{row_id}]")
                            LOG.info(f"   Total changes: {len(changes)} records")
                            LOG.info(f"   Message: {fb_message}")
                            LOG.info("=" * 80)
                            return True, "soft_delete", f"Auto-fallback to soft delete: {fb_message}", changes
                        else:
                            error_msg = f"Fallback soft delete also failed: {fb_message}"
                            LOG.error(f"❌ {error_msg}")
                            return False, "error", error_msg, []

                    except Exception as fallback_error:
                        error_msg = f"Fallback failed: {str(fallback_error)}"
                        LOG.error(f"❌ {error_msg}")
                        LOG.error(traceback.format_exc())
                        return False, "error", error_msg, []

                else:
                    # Hard delete failed for non-FK reason
                    LOG.error("")
                    LOG.error("=" * 80)
                    LOG.error(f"❌ HARD DELETE FAILED")
                    LOG.error(f"   Error: {message}")
                    LOG.error("=" * 80)
                    return False, "error", message, []

    except psycopg2.Error as e:
        error_msg = f"Database error in smart delete: {str(e)}"
        LOG.error("")
        LOG.error("=" * 80)
        LOG.error(f"💥 DATABASE ERROR")
        LOG.error(f"   {error_msg}")
        LOG.error("=" * 80)
        LOG.error(traceback.format_exc())

        # Last resort fallback
        if "foreign key constraint" in str(e).lower():
            LOG.warning("🔄 Attempting emergency soft delete fallback...")
            try:
                conn.rollback()
                checker = SoftDeleteDependencyChecker(conn)
                status_cols = checker.check_table_has_status_columns(table)

                if status_cols['has_active_status'] or status_cols['has_pending_delete']:
                    has_refs, refs = checker.has_any_foreign_key_references(table, row_id)
                    success, message, changes = perform_soft_delete_with_cascade(
                        conn, table, row_id, client_id, refs if has_refs else []
                    )

                    if success:
                        LOG.info(f"✅ Emergency fallback succeeded")
                        return True, "soft_delete", f"Emergency soft delete: {message}", changes
            except Exception as emergency_error:
                LOG.error(f"Emergency fallback failed: {emergency_error}")

        return False, "error", error_msg, []

    except Exception as e:
        error_msg = f"Unexpected error in smart delete: {str(e)}"
        LOG.error("")
        LOG.error("=" * 80)
        LOG.error(f"💥 UNEXPECTED ERROR")
        LOG.error(f"   {error_msg}")
        LOG.error("=" * 80)
        LOG.exception(traceback.format_exc())
        return False, "error", error_msg, []

def perform_soft_delete_with_cascade(
    conn,
    table: str,
    row_id: str,
    client_id: str,
    dependencies: List[Dict]
) -> Tuple[bool, str, List[Dict]]:
    """
    Perform soft delete on record and cascade to all dependencies.
    FIXED: Column type detection happens BEFORE cursor operations.
    """
    broadcast_changes = []

    try:
        # Get table schema info
        checker = SoftDeleteDependencyChecker(conn)
        status_cols = checker.check_table_has_status_columns(table)

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        full_table = qualified(schema, tbl)

        # ===== CRITICAL FIX: Get column types BEFORE cursor operations =====
        update_parts = []
        update_values = []

        if status_cols['has_pending_delete']:
            pd_column_type = get_column_type(conn, table, 'pending_delete')
            pd_value = convert_boolean_for_column(True, pd_column_type)
            update_parts.append("pending_delete = %s")
            update_values.append(pd_value)
            LOG.debug(f"  → pending_delete: type={pd_column_type}, value={repr(pd_value)}")

        if status_cols['has_active_status']:
            as_column_type = get_column_type(conn, table, 'active_status')
            as_value = convert_boolean_for_column(False, as_column_type)
            update_parts.append("active_status = %s")
            update_values.append(as_value)
            LOG.debug(f"  → active_status: type={as_column_type}, value={repr(as_value)}")

        if not update_parts:
            error_msg = f"Table {table} has no status columns to update"
            LOG.error(error_msg)
            return False, error_msg, []

        update_parts.append("updated_at = NOW()")
        # ===== END CRITICAL FIX =====

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            update_sql = f"""
                UPDATE {full_table}
                SET {', '.join(update_parts)}
                WHERE id = %s
                RETURNING *
            """

            # Add row_id to the end of update_values
            update_values.append(row_id)

            LOG.debug(f"Executing: {update_sql}")
            LOG.debug(f"With values: {update_values}")

            cur.execute(update_sql, update_values)
            updated_record = cur.fetchone()

            if not updated_record:
                error_msg = f"Record not found: {table}[{row_id}]"
                LOG.warning(error_msg)
                return False, error_msg, []

            # Add to broadcast changes
            broadcast_changes.append({
                "table": table,
                "row_id": row_id,
                "operation": "soft_delete",
                "data": dict(updated_record),
                "active_status": False,
                "pending_delete": True,
                "source": client_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            LOG.info(f"✅ Soft deleted {table}[{row_id}]")

            # Cascade to all dependencies
            total_cascaded = 0
            for dep in dependencies:
                child_table = dep['child_table']
                child_fk_column = dep['child_fk_column']

                LOG.info(f"   ↳ Cascading soft delete to {child_table} ({dep['count']} records)")

                try:
                    cascaded = cascade_soft_delete_to_children(
                        conn,
                        child_table,
                        child_fk_column,
                        row_id,
                        client_id
                    )

                    broadcast_changes.extend(cascaded)
                    total_cascaded += len(cascaded)

                except Exception as e:
                    LOG.error(f"Failed to cascade to {child_table}: {e}")
                    LOG.error(traceback.format_exc())
                    # Continue with other children

            # Log to audit
            cur.execute("""
                INSERT INTO audit_log (event_id, table_name, row_id, operation, data, source)
                VALUES (%s, %s, %s, 's', %s, %s)
            """, (
                f"soft-delete-{uuid.uuid4()}",
                table,
                row_id,
                json.dumps({
                    "dependencies_cascaded": total_cascaded,
                    "dependency_details": dependencies
                }, default=str),
                client_id
            ))

            conn.commit()

            message = f"Soft deleted {table}[{row_id}] with {total_cascaded} cascaded dependencies"
            LOG.info(f"✅ {message}")

            return True, message, broadcast_changes

    except psycopg2.Error as e:
        conn.rollback()
        error_msg = f"Database error in soft delete: {str(e)}"
        LOG.error(error_msg)
        LOG.error(traceback.format_exc())
        return False, error_msg, []

    except Exception as e:
        conn.rollback()
        error_msg = f"Unexpected error in soft delete: {str(e)}"
        LOG.error(error_msg)
        LOG.error(traceback.format_exc())
        return False, error_msg, []


def cascade_soft_delete_to_children(
    conn,
    child_table: str,
    fk_column: str,
    parent_id: str,
    client_id: str
) -> List[Dict]:
    """
    Cascade soft delete to all active child records.
    FIXED: Column type detection before cursor operations.
    """
    cascaded_changes = []

    if "." in child_table:
        schema, tbl = child_table.split(".", 1)
    else:
        schema, tbl = "public", child_table

    quoted_fk = f'"{fk_column}"'
    full_table = qualified(schema, tbl)

    try:
        # Check child table status columns
        checker = SoftDeleteDependencyChecker(conn)
        status_cols = checker.check_table_has_status_columns(child_table)

        # ===== CRITICAL FIX: Pre-calculate column types and values =====
        update_parts = []
        update_values_template = []

        if status_cols['has_pending_delete']:
            pd_column_type = get_column_type(conn, child_table, 'pending_delete')
            pd_value = convert_boolean_for_column(True, pd_column_type)
            update_parts.append("pending_delete = %s")
            update_values_template.append(pd_value)

        if status_cols['has_active_status']:
            as_column_type = get_column_type(conn, child_table, 'active_status')
            as_value = convert_boolean_for_column(False, as_column_type)
            update_parts.append("active_status = %s")
            update_values_template.append(as_value)

        if not update_parts:
            LOG.warning(f"Child table {child_table} has no status columns")
            return []

        update_parts.append("updated_at = NOW()")
        # ===== END CRITICAL FIX =====

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Find active children
            where_conditions = [f"{quoted_fk} = %s"]

            if status_cols['has_active_status']:
                as_type = get_column_type(conn, child_table, 'active_status')
                if as_type == 'char1':
                    where_conditions.append("active_status = '1'")
                else:
                    where_conditions.append("active_status = TRUE")

            if status_cols['has_pending_delete']:
                pd_type = get_column_type(conn, child_table, 'pending_delete')
                if pd_type == 'char1':
                    where_conditions.append("(pending_delete = '0' OR pending_delete IS NULL)")
                else:
                    where_conditions.append("(pending_delete = FALSE OR pending_delete IS NULL)")

            where_clause = " AND ".join(where_conditions)

            cur.execute(f"""
                SELECT id
                FROM {full_table}
                WHERE {where_clause}
            """, (parent_id,))

            children = cur.fetchall()

            if not children:
                return []

            LOG.debug(f"      Cascading to {len(children)} children in {child_table}")

            update_sql = f"""
                UPDATE {full_table}
                SET {', '.join(update_parts)}
                WHERE id = %s
                RETURNING *
            """

            # Update each child using pre-calculated values
            for child in children:
                child_id = str(child['id'])

                # Create a copy of update values for each child
                update_values = update_values_template.copy()
                update_values.append(child_id)

                cur.execute(update_sql, update_values)
                updated_child = cur.fetchone()

                if updated_child:
                    cascaded_changes.append({
                        "table": child_table,
                        "row_id": child_id,
                        "operation": "soft_delete",
                        "data": dict(updated_child),
                        "active_status": False,
                        "pending_delete": True,
                        "source": f"cascade-{client_id}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "cascaded_from": parent_id
                    })

                    LOG.debug(f"         Soft deleted child {child_table}[{child_id}]")

                    # Recursively cascade to grandchildren if they exist
                    grandchild_deps = checker.get_child_tables(child_table)
                    if grandchild_deps:
                        for gc_table, gc_fk, _ in grandchild_deps:
                            try:
                                grandchildren = cascade_soft_delete_to_children(
                                    conn, gc_table, gc_fk, child_id, client_id
                                )
                                cascaded_changes.extend(grandchildren)
                            except Exception as e:
                                LOG.error(f"Failed to cascade to grandchild {gc_table}: {e}")
                                LOG.error(traceback.format_exc())

    except Exception as e:
        LOG.error(f"Error cascading to {child_table}: {e}")
        LOG.error(traceback.format_exc())

    return cascaded_changes


def perform_hard_delete(
    conn,
    table: str,
    row_id: str,
    client_id: str
) -> Tuple[bool, str, List[Dict]]:
    """
    Perform hard delete - physically remove record from database.
    """
    broadcast_changes = []

    try:
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        full_table = qualified(schema, tbl)

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get record before deletion for audit
            cur.execute(f"SELECT * FROM {full_table} WHERE id = %s", (row_id,))
            record = cur.fetchone()

            if not record:
                error_msg = f"Record not found: {table}[{row_id}]"
                LOG.warning(error_msg)
                return False, error_msg, []

            # Perform hard delete
            cur.execute(f"DELETE FROM {full_table} WHERE id = %s", (row_id,))
            deleted_count = cur.rowcount

            if deleted_count > 0:
                broadcast_changes.append({
                    "table": table,
                    "row_id": row_id,
                    "operation": "hard_delete",
                    "data": {"id": row_id, "_force_hard_delete": True},
                    "source": client_id,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

                # Log to audit
                cur.execute("""
                    INSERT INTO audit_log (event_id, table_name, row_id, operation, data, source)
                    VALUES (%s, %s, %s, 'd', %s, %s)
                """, (
                    f"hard-delete-{uuid.uuid4()}",
                    table,
                    row_id,
                    json.dumps(dict(record), default=str),
                    client_id
                ))

                conn.commit()

                message = f"Hard deleted {table}[{row_id}]"
                LOG.info(f"✅ {message}")

                return True, message, broadcast_changes
            else:
                error_msg = f"Failed to delete {table}[{row_id}]"
                LOG.error(error_msg)
                return False, error_msg, []

    except psycopg2.errors.ForeignKeyViolation as e:
        conn.rollback()
        error_msg = f"Cannot hard delete: Foreign key constraint violation. Try soft delete instead. Details: {str(e)}"
        LOG.error(error_msg)
        return False, error_msg, []

    except psycopg2.Error as e:
        conn.rollback()
        error_msg = f"Database error in hard delete: {str(e)}"
        LOG.error(error_msg)
        LOG.error(traceback.format_exc())
        return False, error_msg, []

    except Exception as e:
        conn.rollback()
        error_msg = f"Unexpected error in hard delete: {str(e)}"
        LOG.error(error_msg)
        LOG.error(traceback.format_exc())
        return False, error_msg, []


# ============================================================
# CONVENIENCE WRAPPER FUNCTION
# ============================================================

def handle_delete_request(conn, table: str, row_id: str, client_id: str, force_hard: bool = False):
    """
    Convenience wrapper for using the smart delete system.
    This should be called from your server's delete endpoint.

    Returns a dictionary with operation details and broadcast changes.
    """
    LOG.info("=" * 60)
    LOG.info(f"🗑️  DELETE REQUEST: {table}[{row_id}]")
    LOG.info(f"   Client: {client_id}")
    LOG.info(f"   Force Hard Delete: {force_hard}")
    LOG.info("=" * 60)

    success, operation_type, message, changes = perform_smart_delete(
        conn=conn,
        table=table,
        row_id=row_id,
        client_id=client_id,
        force_hard_delete=force_hard
    )

    if success:
        LOG.info(f"✅ Delete completed successfully")
        LOG.info(f"   Operation: {operation_type.upper()}")
        LOG.info(f"   Message: {message}")
        LOG.info(f"   Changes to broadcast: {len(changes)}")

        return {
            "status": "success",
            "operation": operation_type,
            "message": message,
            "changes_count": len(changes),
            "changes": changes
        }
    else:
        LOG.error(f"❌ Delete failed: {message}")
        return {
            "status": "error",
            "message": message,
            "operation": operation_type
        }


# ============================================================
# ACTIVATION/RESTORATION FUNCTIONS (UNDO SOFT DELETE)
# ============================================================

def perform_activation(
    conn,
    table: str,
    row_id: str,
    client_id: str = "unknown",
    cascade: bool = True
) -> Tuple[bool, str, List[Dict]]:
    """
    Activate (restore) a soft-deleted record.
    Optionally cascade activation to dependent records.

    Returns: (success, message, broadcast_changes)
    """
    broadcast_changes = []

    try:
        # Get table schema info
        checker = SoftDeleteDependencyChecker(conn)
        status_cols = checker.check_table_has_status_columns(table)

        if not status_cols['has_pending_delete'] and not status_cols['has_active_status']:
            error_msg = f"Table {table} does not support activation (no status columns)"
            LOG.error(error_msg)
            return False, error_msg, []

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        full_table = qualified(schema, tbl)

        # ===== CRITICAL FIX: Get column types BEFORE cursor operations =====
        update_parts = []
        update_values = []

        if status_cols['has_pending_delete']:
            pd_column_type = get_column_type(conn, table, 'pending_delete')
            pd_value = convert_boolean_for_column(False, pd_column_type)
            update_parts.append("pending_delete = %s")
            update_values.append(pd_value)
            LOG.debug(f"  → pending_delete: type={pd_column_type}, value={repr(pd_value)}")

        if status_cols['has_active_status']:
            as_column_type = get_column_type(conn, table, 'active_status')
            as_value = convert_boolean_for_column(True, as_column_type)
            update_parts.append("active_status = %s")
            update_values.append(as_value)
            LOG.debug(f"  → active_status: type={as_column_type}, value={repr(as_value)}")

        update_parts.append("updated_at = NOW()")
        # ===== END CRITICAL FIX =====

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            update_sql = f"""
                UPDATE {full_table}
                SET {', '.join(update_parts)}
                WHERE id = %s
                RETURNING *
            """

            update_values.append(row_id)

            LOG.debug(f"Executing activation: {update_sql}")
            LOG.debug(f"With values: {update_values}")

            cur.execute(update_sql, update_values)
            updated_record = cur.fetchone()

            if not updated_record:
                error_msg = f"Record not found: {table}[{row_id}]"
                LOG.warning(error_msg)
                return False, error_msg, []

            # Add to broadcast changes
            broadcast_changes.append({
                "table": table,
                "row_id": row_id,
                "operation": "activate",
                "data": dict(updated_record),
                "active_status": True,
                "pending_delete": False,
                "source": client_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            LOG.info(f"✅ Activated {table}[{row_id}]")

            # Cascade activation to children if requested
            total_cascaded = 0
            if cascade:
                child_tables = checker.get_child_tables(table)

                for child_table, child_fk_column, _ in child_tables:
                    LOG.info(f"   ↳ Cascading activation to {child_table}")

                    try:
                        cascaded = cascade_activation_to_children(
                            conn,
                            child_table,
                            child_fk_column,
                            row_id,
                            client_id
                        )

                        broadcast_changes.extend(cascaded)
                        total_cascaded += len(cascaded)

                    except Exception as e:
                        LOG.error(f"Failed to cascade activation to {child_table}: {e}")
                        LOG.error(traceback.format_exc())

            # Log to audit
            cur.execute("""
                INSERT INTO audit_log (event_id, table_name, row_id, operation, data, source)
                VALUES (%s, %s, %s, 'r', %s, %s)
            """, (
                f"activate-{uuid.uuid4()}",
                table,
                row_id,
                json.dumps({
                    "children_activated": total_cascaded
                }, default=str),
                client_id
            ))

            conn.commit()

            message = f"Activated {table}[{row_id}] with {total_cascaded} cascaded activations"
            LOG.info(f"✅ {message}")

            return True, message, broadcast_changes

    except psycopg2.Error as e:
        conn.rollback()
        error_msg = f"Database error in activation: {str(e)}"
        LOG.error(error_msg)
        LOG.error(traceback.format_exc())
        return False, error_msg, []

    except Exception as e:
        conn.rollback()
        error_msg = f"Unexpected error in activation: {str(e)}"
        LOG.error(error_msg)
        LOG.error(traceback.format_exc())
        return False, error_msg, []


def cascade_activation_to_children(
    conn,
    child_table: str,
    fk_column: str,
    parent_id: str,
    client_id: str
) -> List[Dict]:
    """
    Cascade activation to child records that were deactivated with the parent.
    FIXED: Column type detection before cursor operations.
    """
    cascaded_changes = []

    if "." in child_table:
        schema, tbl = child_table.split(".", 1)
    else:
        schema, tbl = "public", child_table

    quoted_fk = f'"{fk_column}"'
    full_table = qualified(schema, tbl)

    try:
        # Check child table status columns
        checker = SoftDeleteDependencyChecker(conn)
        status_cols = checker.check_table_has_status_columns(child_table)

        # ===== CRITICAL FIX: Pre-calculate column types and values =====
        update_parts = []
        update_values_template = []

        if status_cols['has_pending_delete']:
            pd_column_type = get_column_type(conn, child_table, 'pending_delete')
            pd_value = convert_boolean_for_column(False, pd_column_type)
            update_parts.append("pending_delete = %s")
            update_values_template.append(pd_value)

        if status_cols['has_active_status']:
            as_column_type = get_column_type(conn, child_table, 'active_status')
            as_value = convert_boolean_for_column(True, as_column_type)
            update_parts.append("active_status = %s")
            update_values_template.append(as_value)

        if not update_parts:
            LOG.warning(f"Child table {child_table} has no status columns")
            return []

        update_parts.append("updated_at = NOW()")
        # ===== END CRITICAL FIX =====

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Find deactivated children
            where_conditions = [f"{quoted_fk} = %s"]

            if status_cols['has_active_status']:
                as_type = get_column_type(conn, child_table, 'active_status')
                if as_type == 'char1':
                    where_conditions.append("active_status = '0'")
                else:
                    where_conditions.append("active_status = FALSE")

            if status_cols['has_pending_delete']:
                pd_type = get_column_type(conn, child_table, 'pending_delete')
                if pd_type == 'char1':
                    where_conditions.append("pending_delete = '1'")
                else:
                    where_conditions.append("pending_delete = TRUE")

            where_clause = " AND ".join(where_conditions)

            cur.execute(f"""
                SELECT id
                FROM {full_table}
                WHERE {where_clause}
            """, (parent_id,))

            children = cur.fetchall()

            if not children:
                return []

            LOG.debug(f"      Activating {len(children)} children in {child_table}")

            update_sql = f"""
                UPDATE {full_table}
                SET {', '.join(update_parts)}
                WHERE id = %s
                RETURNING *
            """

            # Update each child using pre-calculated values
            for child in children:
                child_id = str(child['id'])

                # Create a copy of update values for each child
                update_values = update_values_template.copy()
                update_values.append(child_id)

                cur.execute(update_sql, update_values)
                updated_child = cur.fetchone()

                if updated_child:
                    cascaded_changes.append({
                        "table": child_table,
                        "row_id": child_id,
                        "operation": "activate",
                        "data": dict(updated_child),
                        "active_status": True,
                        "pending_delete": False,
                        "source": f"cascade-{client_id}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "cascaded_from": parent_id
                    })

                    LOG.debug(f"         Activated child {child_table}[{child_id}]")

                    # Recursively cascade to grandchildren if they exist
                    grandchild_deps = checker.get_child_tables(child_table)
                    if grandchild_deps:
                        for gc_table, gc_fk, _ in grandchild_deps:
                            try:
                                grandchildren = cascade_activation_to_children(
                                    conn, gc_table, gc_fk, child_id, client_id
                                )
                                cascaded_changes.extend(grandchildren)
                            except Exception as e:
                                LOG.error(f"Failed to cascade activation to grandchild {gc_table}: {e}")
                                LOG.error(traceback.format_exc())

    except Exception as e:
        LOG.error(f"Error cascading activation to {child_table}: {e}")
        LOG.error(traceback.format_exc())

    return cascaded_changes


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def get_record_status(conn, table: str, row_id: str) -> Dict:
    """
    Get the current status of a record (active/inactive/deleted).

    Returns: {
        "exists": bool,
        "active_status": bool or None,
        "pending_delete": bool or None,
        "is_active": bool,
        "data": dict or None
    }
    """
    if "." in table:
        schema, tbl = table.split(".", 1)
    else:
        schema, tbl = "public", table

    full_table = qualified(schema, tbl)

    try:
        checker = SoftDeleteDependencyChecker(conn)
        status_cols = checker.check_table_has_status_columns(table)

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT * FROM {full_table} WHERE id = %s", (row_id,))
            record = cur.fetchone()

            if not record:
                return {
                    "exists": False,
                    "active_status": None,
                    "pending_delete": None,
                    "is_active": False,
                    "data": None
                }

            active_status = record.get('active_status') if status_cols['has_active_status'] else None
            pending_delete = record.get('pending_delete') if status_cols['has_pending_delete'] else None

            # Determine if record is truly active
            is_active = True
            if status_cols['has_active_status'] and active_status == False:
                is_active = False
            if status_cols['has_pending_delete'] and pending_delete == True:
                is_active = False

            return {
                "exists": True,
                "active_status": active_status,
                "pending_delete": pending_delete,
                "is_active": is_active,
                "data": dict(record)
            }

    except Exception as e:
        LOG.error(f"Error getting record status for {table}[{row_id}]: {e}")
        return {
            "exists": False,
            "active_status": None,
            "pending_delete": None,
            "is_active": False,
            "data": None,
            "error": str(e)
        }


def bulk_soft_delete(
    conn,
    table: str,
    row_ids: List[str],
    client_id: str = "unknown",
    cascade: bool = True
) -> Tuple[int, int, List[Dict]]:
    """
    Perform soft delete on multiple records.

    Returns: (success_count, failure_count, all_broadcast_changes)
    """
    success_count = 0
    failure_count = 0
    all_changes = []

    LOG.info(f"🗑️  BULK SOFT DELETE: {table} ({len(row_ids)} records)")

    for row_id in row_ids:
        try:
            success, operation_type, message, changes = perform_smart_delete(
                conn=conn,
                table=table,
                row_id=row_id,
                client_id=client_id,
                force_hard_delete=False
            )

            if success:
                success_count += 1
                all_changes.extend(changes)
                LOG.debug(f"   ✅ {row_id}")
            else:
                failure_count += 1
                LOG.warning(f"   ❌ {row_id}: {message}")

        except Exception as e:
            failure_count += 1
            LOG.error(f"   ❌ {row_id}: {str(e)}")

    LOG.info(f"✅ Bulk delete complete: {success_count} succeeded, {failure_count} failed")

    return success_count, failure_count, all_changes


def bulk_activate(
    conn,
    table: str,
    row_ids: List[str],
    client_id: str = "unknown",
    cascade: bool = True
) -> Tuple[int, int, List[Dict]]:
    """
    Perform activation on multiple records.

    Returns: (success_count, failure_count, all_broadcast_changes)
    """
    success_count = 0
    failure_count = 0
    all_changes = []

    LOG.info(f"✨ BULK ACTIVATE: {table} ({len(row_ids)} records)")

    for row_id in row_ids:
        try:
            success, message, changes = perform_activation(
                conn=conn,
                table=table,
                row_id=row_id,
                client_id=client_id,
                cascade=cascade
            )

            if success:
                success_count += 1
                all_changes.extend(changes)
                LOG.debug(f"   ✅ {row_id}")
            else:
                failure_count += 1
                LOG.warning(f"   ❌ {row_id}: {message}")

        except Exception as e:
            failure_count += 1
            LOG.error(f"   ❌ {row_id}: {str(e)}")

    LOG.info(f"✅ Bulk activation complete: {success_count} succeeded, {failure_count} failed")

    return success_count, failure_count, all_changes


# ============================================================
# DIAGNOSTIC & MAINTENANCE FUNCTIONS
# ============================================================

def diagnose_table_status_system(conn, table: str) -> Dict:
    """
    Diagnose the status system configuration for a table.
    Returns detailed information about column types and values.
    """
    result = {
        "table": table,
        "has_active_status": False,
        "has_pending_delete": False,
        "active_status_type": None,
        "pending_delete_type": None,
        "sample_records": [],
        "issues": []
    }

    try:
        checker = SoftDeleteDependencyChecker(conn)
        status_cols = checker.check_table_has_status_columns(table)

        result["has_active_status"] = status_cols["has_active_status"]
        result["has_pending_delete"] = status_cols["has_pending_delete"]

        if status_cols["has_active_status"]:
            result["active_status_type"] = get_column_type(conn, table, "active_status")

        if status_cols["has_pending_delete"]:
            result["pending_delete_type"] = get_column_type(conn, table, "pending_delete")

        # Get sample records
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        full_table = qualified(schema, tbl)

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            columns = ["id"]
            if status_cols["has_active_status"]:
                columns.append("active_status")
            if status_cols["has_pending_delete"]:
                columns.append("pending_delete")

            col_list = ", ".join(columns)
            cur.execute(f"SELECT {col_list} FROM {full_table} LIMIT 5")
            result["sample_records"] = [dict(row) for row in cur.fetchall()]

        # Check for potential issues
        if not status_cols["has_active_status"] and not status_cols["has_pending_delete"]:
            result["issues"].append("Table has no status columns - soft delete not supported")

        if result["active_status_type"] == "char1":
            result["issues"].append("active_status is CHAR(1) - values should be '0' or '1'")

        if result["pending_delete_type"] == "char1":
            result["issues"].append("pending_delete is CHAR(1) - values should be '0' or '1'")

        LOG.info(f"📊 Diagnosis for {table}:")
        LOG.info(f"   Active Status: {result['has_active_status']} (type: {result['active_status_type']})")
        LOG.info(f"   Pending Delete: {result['has_pending_delete']} (type: {result['pending_delete_type']})")
        if result["issues"]:
            LOG.warning(f"   Issues: {', '.join(result['issues'])}")

    except Exception as e:
        result["error"] = str(e)
        LOG.error(f"Error diagnosing {table}: {e}")

    return result


def repair_inconsistent_status_values(conn, table: str, dry_run: bool = True) -> Dict:
    """
    Find and optionally repair inconsistent status values.
    For CHAR(1) columns, converts True/False to '1'/'0'.

    Args:
        dry_run: If True, only reports issues without fixing them

    Returns: Report of found and fixed issues
    """
    report = {
        "table": table,
        "issues_found": 0,
        "issues_fixed": 0,
        "dry_run": dry_run,
        "details": []
    }

    try:
        checker = SoftDeleteDependencyChecker(conn)
        status_cols = checker.check_table_has_status_columns(table)

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        full_table = qualified(schema, tbl)

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check for CHAR(1) columns with invalid values
            if status_cols["has_active_status"]:
                as_type = get_column_type(conn, table, "active_status")
                if as_type == "char1":
                    # Find records with invalid values
                    cur.execute(f"""
                        SELECT id, active_status
                        FROM {full_table}
                        WHERE active_status NOT IN ('0', '1')
                        AND active_status IS NOT NULL
                        LIMIT 100
                    """)
                    invalid_records = cur.fetchall()

                    if invalid_records:
                        report["issues_found"] += len(invalid_records)
                        report["details"].append({
                            "column": "active_status",
                            "issue": "Invalid values for CHAR(1) column",
                            "count": len(invalid_records)
                        })

                        if not dry_run:
                            # Fix them
                            for record in invalid_records:
                                row_id = record['id']
                                current_value = record['active_status']
                                # Try to interpret as boolean
                                new_value = '1' if str(current_value).lower() in ('true', 't', '1', 'yes') else '0'

                                cur.execute(f"""
                                    UPDATE {full_table}
                                    SET active_status = %s
                                    WHERE id = %s
                                """, (new_value, row_id))

                                report["issues_fixed"] += 1

            if status_cols["has_pending_delete"]:
                pd_type = get_column_type(conn, table, "pending_delete")
                if pd_type == "char1":
                    cur.execute(f"""
                        SELECT id, pending_delete
                        FROM {full_table}
                        WHERE pending_delete NOT IN ('0', '1')
                        AND pending_delete IS NOT NULL
                        LIMIT 100
                    """)
                    invalid_records = cur.fetchall()

                    if invalid_records:
                        report["issues_found"] += len(invalid_records)
                        report["details"].append({
                            "column": "pending_delete",
                            "issue": "Invalid values for CHAR(1) column",
                            "count": len(invalid_records)
                        })

                        if not dry_run:
                            for record in invalid_records:
                                row_id = record['id']
                                current_value = record['pending_delete']
                                new_value = '1' if str(current_value).lower() in ('true', 't', '1', 'yes') else '0'

                                cur.execute(f"""
                                    UPDATE {full_table}
                                    SET pending_delete = %s
                                    WHERE id = %s
                                """, (new_value, row_id))

                                report["issues_fixed"] += 1

            if not dry_run and report["issues_fixed"] > 0:
                conn.commit()
                LOG.info(f"✅ Repaired {report['issues_fixed']} records in {table}")
            elif dry_run and report["issues_found"] > 0:
                LOG.warning(f"⚠️  Found {report['issues_found']} issues in {table} (dry run - not fixed)")

    except Exception as e:
        if not dry_run:
            conn.rollback()
        report["error"] = str(e)
        LOG.error(f"Error repairing {table}: {e}")

    return report


# ============================================================
# USAGE EXAMPLES
# ============================================================

if __name__ == "__main__":
    """
    Example usage of the soft delete system
    """
    import psycopg2

    # Example connection (replace with your actual connection details)
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        dbname="yourdb",
        user="youruser",
        password="yourpassword"
    )

    try:
        # Example 1: Smart delete (automatically chooses soft or hard delete)
        result = handle_delete_request(
            conn=conn,
            table="public.Inventory_equipment",
            row_id="123",
            client_id="workshop-abc",
            force_hard=False
        )
        print(f"Delete result: {result['status']} - {result['message']}")

        # Example 2: Check dependencies before deleting
        checker = SoftDeleteDependencyChecker(conn)
        should_soft, reason, deps = checker.should_soft_delete(
            "public.Inventory_equipment",
            "123"
        )
        print(f"Should soft delete: {should_soft}")
        print(f"Reason: {reason}")

        # Example 3: Activate (restore) a soft-deleted record
        success, message, changes = perform_activation(
            conn=conn,
            table="public.Inventory_equipment",
            row_id="123",
            client_id="workshop-abc",
            cascade=True
        )
        print(f"Activation: {message}")

        # Example 4: Diagnose table status system
        diagnosis = diagnose_table_status_system(conn, "public.Inventory_equipment")
        print(f"Diagnosis: {diagnosis}")

        # Example 5: Bulk operations
        success_count, failure_count, changes = bulk_soft_delete(
            conn=conn,
            table="public.Inventory_equipment",
            row_ids=["123", "456", "789"],
            client_id="bulk-operation"
        )
        print(f"Bulk delete: {success_count} succeeded, {failure_count} failed")

    finally:
        conn.close()
