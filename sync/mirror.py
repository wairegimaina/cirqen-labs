#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mirror.py - Complete Database Mirroring & Consistency System
============================================================

✅ FIXED: Cursor type consistency issues resolved
- Regular cursor for scalar/boolean queries (EXISTS, COUNT)
- RealDictCursor only for queries needing column name access

Features:
- Full table content comparison (HQ ↔ Local)
- Missing record detection and automatic creation
- Bidirectional sync with conflict resolution
- Initial sync for new machines
- Periodic consistency checks
- Broadcast to all connected agents
- Progress tracking and detailed logging

Author: B12 Technologies
Version: 1.0.2 - Fixed cursor type handling throughout
"""

import os
import sys
import json
import time
import logging
import uuid
import hashlib
from typing import Dict, List, Set, Tuple, Optional, Any
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import psycopg2
from psycopg2.extras import RealDictCursor, Json
import requests
from dataclasses import dataclass, asdict
from enum import Enum
from psycopg2.extras import RealDictCursor, Json

# Setup logging
LOG = logging.getLogger("mirror_sync")
LOG.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
LOG.addHandler(handler)


class SyncDirection(Enum):
    """Direction of synchronization"""
    HQ_TO_LOCAL = "hq_to_local"
    LOCAL_TO_HQ = "local_to_hq"
    BIDIRECTIONAL = "bidirectional"


@dataclass
class MirrorStats:
    """Statistics for a mirror operation"""
    table: str
    hq_count: int
    local_count: int
    missing_in_hq: int
    missing_in_local: int
    conflicts: int
    synced_to_hq: int
    synced_to_local: int
    errors: int
    duration_seconds: float

    def to_dict(self):
        return asdict(self)


@dataclass
class RecordDifference:
    """Represents a difference between HQ and local"""
    table: str
    row_id: str
    exists_in_hq: bool
    exists_in_local: bool
    hq_data: Optional[Dict] = None
    local_data: Optional[Dict] = None
    hq_updated_at: Optional[datetime] = None
    local_updated_at: Optional[datetime] = None

    @property
    def is_conflict(self) -> bool:
        """Check if this is a conflict (exists in both but different)"""
        return (self.exists_in_hq and self.exists_in_local and
                self.hq_updated_at and self.local_updated_at)

    @property
    def newer_location(self) -> Optional[str]:
        """Determine which version is newer"""
        if not self.is_conflict:
            return None
        if self.hq_updated_at > self.local_updated_at:
            return "hq"
        elif self.local_updated_at > self.hq_updated_at:
            return "local"
        return "same"


# ============================================================
# ENHANCED FK DEPENDENCY RESOLUTION FOR MIRROR SYNC
# ============================================================

def get_fk_relationships_for_mirror(conn) -> Dict[str, List[Dict]]:
    """
    Get all FK relationships from database schema.
    Returns: {child_table: [{parent_table, child_column, parent_column}]}
    """
    fk_relationships = defaultdict(list)

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    tc.table_schema || '.' || tc.table_name AS child_table,
                    kcu.column_name AS child_column,
                    ccu.table_schema || '.' || ccu.table_name AS parent_table,
                    ccu.column_name AS parent_column
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_schema = 'public'
            """)

            rows = cur.fetchall()

            for row in rows:
                child_table = row['child_table']
                fk_relationships[child_table].append({
                    'parent_table': row['parent_table'],
                    'child_column': row['child_column'],
                    'parent_column': row['parent_column']
                })

            LOG.info(f"📊 Loaded FK relationships for {len(fk_relationships)} tables")

            return dict(fk_relationships)

    except Exception as e:
        LOG.error(f"❌ Failed to load FK relationships: {e}")
        return {}

def fetch_parent_record_from_local(local_conn, table: str, parent_id: str,
                                   include_soft_deleted: bool = False) -> Optional[Dict]:
    """
    Fetch parent record - optionally include soft-deleted records

    Args:
        local_conn: Database connection
        table: Table name
        parent_id: Parent record ID
        include_soft_deleted: If True, fetch even if pending_delete=TRUE
    """
    if "." in table:
        schema, tbl = table.split(".", 1)
    else:
        schema, tbl = "public", table

    quoted_table = f'"{tbl}"'
    full_table = f"{schema}.{quoted_table}"

    try:
        with local_conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check for pending_delete column
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s
                    AND column_name = 'pending_delete'
                )
            """, (schema, tbl))

            has_pending_delete = cur.fetchone()['exists']

            # 🆕 MODIFIED: Optionally include soft-deleted records
            if has_pending_delete and not include_soft_deleted:
                where_sql = "AND (pending_delete = FALSE OR pending_delete IS NULL)"
            else:
                where_sql = ""  # Get ALL records including soft-deleted

            query = f"""
                SELECT to_jsonb(t.*) as data
                FROM {full_table} t
                WHERE id = %s {where_sql}
            """

            cur.execute(query, (parent_id,))
            result = cur.fetchone()

            if result:
                data = result['data']
                is_soft_deleted = data.get('pending_delete', False)
                status = "SOFT-DELETED" if is_soft_deleted else "ACTIVE"
                LOG.debug(f"   ✅ Found parent ({status}): {table}[{parent_id}]")
                return result['data']
            else:
                LOG.debug(f"   ❌ Parent not found: {table}[{parent_id}]")
                return None

    except Exception as e:
        LOG.error(f"   ❌ Error fetching parent {table}[{parent_id}]: {e}")
        try:
            local_conn.rollback()
        except:
            pass
        return None
# ============================================================
# 🆕 ENHANCED: Helper function needs to handle BOTH directions
# ============================================================
def check_and_fetch_missing_parents_for_mirror(
    source_conn,
    dest_conn,
    records_to_sync: List[Tuple[str, str]],
    fk_relationships: Dict,
    present_in_dest: Set[Tuple[str, str]]
) -> Tuple[List[Tuple[str, str, Dict]], Set[Tuple[str, str]]]:
    """
    Check for missing parent records and fetch from SOURCE database.

    🆕 MODIFIED: Now fetches soft-deleted parents too for FK resolution
    """
    auto_fetched_parents = []
    records_with_missing_parents = set()
    checked_parents = set()

    LOG.info(f"🔍 Checking {len(records_to_sync)} records for FK dependencies...")

    for table, row_id in records_to_sync:
        if table not in fk_relationships:
            continue

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        quoted_table = f'"{tbl}"'
        full_table = f"{schema}.{quoted_table}"

        try:
            with source_conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT to_jsonb(t.*) as data
                    FROM {full_table} t
                    WHERE id = %s
                """, (row_id,))

                result = cur.fetchone()

                if not result:
                    continue

                record_data = result['data']

                for fk in fk_relationships[table]:
                    parent_table = fk['parent_table']
                    child_column = fk['child_column']
                    parent_id = record_data.get(child_column)

                    if not parent_id:
                        continue

                    parent_key = (parent_table, str(parent_id))

                    if parent_key in checked_parents:
                        continue

                    checked_parents.add(parent_key)

                    if parent_key in present_in_dest:
                        LOG.debug(f"   ✓ {parent_table}[{parent_id}] present in destination")
                        continue

                    LOG.info(f"   ⚠️ Missing parent: {parent_table}[{parent_id}] for {table}[{row_id}]")

                    # 🆕 MODIFIED: Fetch with include_soft_deleted=True for FK resolution
                    parent_data = fetch_parent_record_from_local(
                        source_conn,
                        parent_table,
                        parent_id,
                        include_soft_deleted=True  # 🔥 KEY CHANGE
                    )

                    if parent_data:
                        is_soft_deleted = parent_data.get('pending_delete', False)
                        status = " [SOFT-DELETED]" if is_soft_deleted else ""
                        LOG.info(f"      ✅ AUTO-FETCHED from source{status}: {parent_table}[{parent_id}]")
                        auto_fetched_parents.append((parent_table, parent_id, parent_data))
                        present_in_dest.add(parent_key)
                    else:
                        LOG.warning(f"      ❌ NOT FOUND in source: {parent_table}[{parent_id}]")
                        records_with_missing_parents.add((table, row_id))

        except Exception as e:
            LOG.error(f"   ❌ Error checking record {table}[{row_id}]: {e}")
            continue

    return auto_fetched_parents, records_with_missing_parents
# ============================================================
# 🆕 ENHANCED: Recursive resolver needs to handle BOTH directions
# ============================================================

def resolve_parent_dependencies_recursive(
    source_conn,  # 🔄 Can be either Local or HQ
    dest_conn,    # 🔄 Can be either HQ or Local
    parents_to_fetch: List[Tuple[str, str, Dict]],
    fk_relationships: Dict,
    present_in_dest: Set[Tuple[str, str]],
    max_depth: int = 5
) -> List[Tuple[str, str, Dict]]:
    """
    ✅ ENHANCED: Recursively resolve parent dependencies (works BIDIRECTIONALLY).

    Args:
        source_conn: Database to fetch FROM (Local or HQ)
        dest_conn: Database to check IN (HQ or Local)
        parents_to_fetch: Initial parents found
        fk_relationships: FK relationships
        present_in_dest: Records already in destination
        max_depth: Maximum recursion depth

    Returns:
        Complete list of all parent records in dependency order
    """
    all_parents = []
    depth = 0
    current_batch = parents_to_fetch

    LOG.info(f"🔄 Starting recursive dependency resolution (max depth: {max_depth})...")

    while current_batch and depth < max_depth:
        depth += 1
        LOG.info(f"   📊 Depth {depth}: Checking {len(current_batch)} parents for their dependencies...")

        records_to_check = [(table, row_id) for table, row_id, _ in current_batch]

        grandparents, _ = check_and_fetch_missing_parents_for_mirror(
            source_conn,
            dest_conn,
            records_to_check,
            fk_relationships,
            present_in_dest
        )

        if grandparents:
            LOG.info(f"      ✅ Found {len(grandparents)} grandparent records at depth {depth}")
            all_parents.extend(grandparents)
            current_batch = grandparents
        else:
            LOG.info(f"      ✓ No more dependencies at depth {depth}")
            break

    if depth >= max_depth:
        LOG.warning(f"   ⚠️ Reached max dependency depth ({max_depth})")

    all_parents.extend(parents_to_fetch)

    LOG.info(f"✅ Dependency resolution complete: {len(all_parents)} total parents to sync")

    return all_parents

class DatabaseMirror:
    """
    Complete database mirroring system with bidirectional sync
    """

    def __init__(self,
                 hq_config: Dict,
                 local_config: Dict,
                 api_url: str,
                 auth_token: str = None,
                 client_id: str = None,
                 machine_id: str = None):
        """
        Initialize mirror system

        Args:
            hq_config: HQ database configuration
            local_config: Local database configuration
            api_url: HQ API URL for broadcasting
            auth_token: Authentication token
            client_id: Client ID for this machine
            machine_id: Unique machine identifier
        """
        self.hq_config = hq_config
        self.local_config = local_config
        self.api_url = api_url.rstrip("/")
        self.auth_token = auth_token
        self.client_id = client_id or f"mirror-{uuid.uuid4().hex[:8]}"
        self.machine_id = machine_id or self._get_machine_id()

        # Connection pools
        self.hq_conn = None
        self.local_conn = None

        # Statistics
        self.total_stats = []

        LOG.info("=" * 80)
        LOG.info("🔄 DATABASE MIRROR SYSTEM INITIALIZED")
        LOG.info("=" * 80)
        LOG.info(f"   HQ Database: {hq_config['host']}:{hq_config['port']}/{hq_config['dbname']}")
        LOG.info(f"   Local Database: {local_config['host']}:{local_config['port']}/{local_config['dbname']}")
        LOG.info(f"   API URL: {api_url}")
        LOG.info(f"   Client ID: {self.client_id}")
        LOG.info(f"   Machine ID: {self.machine_id}")
        LOG.info("=" * 80)

    def _get_machine_id(self) -> str:
        """Generate unique machine identifier"""
        try:
            mac_int = uuid.getnode()
            mac_hex = ":".join(f"{(mac_int >> ele) & 0xff:02x}" for ele in range(40, -1, -8))
            mac_hash = hashlib.sha1(mac_hex.encode()).hexdigest()[:12]
            return f"mac-{mac_hash}"
        except Exception:
            return f"machine-{uuid.uuid4().hex[:8]}"

    def connect(self):
        """Establish database connections with retry logic"""
        max_retries = 3
        retry_delay = 2

        try:
            LOG.info("🔌 Connecting to databases...")

            # Connect to HQ with retry
            for attempt in range(max_retries):
                try:
                    self.hq_conn = psycopg2.connect(
                        **self.hq_config,
                        connect_timeout=10
                    )
                    # Test connection
                    with self.hq_conn.cursor() as cur:
                        cur.execute("SELECT 1")
                    LOG.info("   ✅ Connected to HQ database")
                    break
                except psycopg2.OperationalError as e:
                    if attempt < max_retries - 1:
                        LOG.warning(f"   ⚠️ HQ connection attempt {attempt + 1} failed, retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                    else:
                        LOG.error(f"   ❌ Failed to connect to HQ after {max_retries} attempts")
                        raise

            # Connect to Local with retry
            for attempt in range(max_retries):
                try:
                    self.local_conn = psycopg2.connect(
                        **self.local_config,
                        connect_timeout=10
                    )
                    # Test connection
                    with self.local_conn.cursor() as cur:
                        cur.execute("SELECT 1")
                    LOG.info("   ✅ Connected to local database")
                    break
                except psycopg2.OperationalError as e:
                    if attempt < max_retries - 1:
                        LOG.warning(f"   ⚠️ Local connection attempt {attempt + 1} failed, retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                    else:
                        LOG.error(f"   ❌ Failed to connect to local after {max_retries} attempts")
                        raise

            LOG.info("   ✅ Both database connections verified")
            return True

        except Exception as e:
            LOG.error(f"❌ Connection failed: {e}")
            import traceback
            LOG.error(traceback.format_exc())
            return False

    def _sort_tables_by_dependencies(self, tables: List[str]) -> List[str]:
        """
        ✅ NEW: Sort tables by foreign key dependencies (parent tables first)

        This prevents FK violations during sync by ensuring parent records
        exist before child records are synced.

        Args:
            tables: Unsorted list of table names

        Returns:
            Sorted list with parent tables before child tables
        """
        try:
            # Get FK relationships from database
            with self.local_conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        tc.table_schema || '.' || tc.table_name AS child_table,
                        ccu.table_schema || '.' || ccu.table_name AS parent_table
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage AS ccu
                        ON ccu.constraint_name = tc.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                """)

                fk_relationships = cur.fetchall()

            # Build dependency graph
            dependencies = {}
            for table in tables:
                dependencies[table] = set()

            for child, parent in fk_relationships:
                if child in tables and parent in tables:
                    dependencies[child].add(parent)

            # Topological sort (Kahn's algorithm)
            sorted_tables = []
            no_deps = [t for t in tables if not dependencies[t]]

            while no_deps:
                table = no_deps.pop(0)
                sorted_tables.append(table)

                # Remove this table from all dependencies
                for child in list(dependencies.keys()):
                    if table in dependencies[child]:
                        dependencies[child].remove(table)
                        if not dependencies[child]:
                            no_deps.append(child)

            # Add any remaining tables (circular dependencies or isolated)
            remaining = set(tables) - set(sorted_tables)
            sorted_tables.extend(sorted(remaining))

            return sorted_tables

        except Exception as e:
            LOG.warning(f"⚠️ Could not sort tables by dependencies: {e}")
            LOG.warning("   Using original order")
            return tables


    def mirror_table(self,
                    table: str,
                    direction: SyncDirection = SyncDirection.BIDIRECTIONAL,
                    conflict_strategy: str = "last_write_wins") -> MirrorStats:
        """
        ✅ FULLY BIDIRECTIONAL: Mirror table with intelligent FK dependency resolution

        Now properly handles BOTH directions:
        1. Local → HQ (with FK resolution)
        2. HQ → Local (with FK resolution)

        Phase 1: Sync Local → HQ (records missing in HQ)
        Phase 2: Sync HQ → Local (records missing in Local) ✨ NEW
        Phase 3: Resolve conflicts (records in both but different)
        """
        start_time = time.time()

        LOG.info("")
        LOG.info("=" * 80)
        LOG.info(f"🔄 MIRRORING TABLE: {table}")
        LOG.info(f"   Direction: {direction.value}")
        LOG.info(f"   Conflict Strategy: {conflict_strategy}")
        LOG.info("=" * 80)

        # Verify table exists in both databases
        hq_exists = self.verify_table_exists(self.hq_conn, table)
        local_exists = self.verify_table_exists(self.local_conn, table)

        if not hq_exists or not local_exists:
            LOG.error(f"❌ Table validation failed for {table}")
            return MirrorStats(
                table=table, hq_count=0, local_count=0,
                missing_in_hq=0, missing_in_local=0, conflicts=0,
                synced_to_hq=0, synced_to_local=0, errors=0,
                duration_seconds=time.time() - start_time
            )

        # Compare contents
        try:
            missing_in_hq, missing_in_local, common_ids = self.compare_table_contents(table)
        except Exception as e:
            LOG.error(f"❌ Failed to compare table contents: {e}")
            return MirrorStats(
                table=table, hq_count=0, local_count=0,
                missing_in_hq=0, missing_in_local=0, conflicts=0,
                synced_to_hq=0, synced_to_local=0, errors=0,
                duration_seconds=time.time() - start_time
            )

        # Detect conflicts
        conflicts = []
        if common_ids:
            try:
                conflicts = self.detect_conflicts(table, common_ids)
            except Exception as e:
                LOG.error(f"❌ Failed to detect conflicts: {e}")

        # Initialize counters
        synced_to_hq = 0
        synced_to_local = 0
        errors = 0

        # ============================================================
        # 🎯 PHASE 1: Sync LOCAL → HQ (with FK dependency resolution)
        # ============================================================
        if direction in [SyncDirection.LOCAL_TO_HQ, SyncDirection.BIDIRECTIONAL]:
            if missing_in_hq:
                LOG.info(f"")
                LOG.info(f"📤 PHASE 1: Syncing {len(missing_in_hq)} records from Local → HQ")
                LOG.info(f"   (with intelligent FK dependency resolution)")
                LOG.info("")

                # Get FK relationships
                fk_relationships = get_fk_relationships_for_mirror(self.local_conn)

                # Build set of records already in HQ
                present_in_hq = set()
                hq_ids = self.get_table_record_ids(self.hq_conn, table)
                for hq_id in hq_ids:
                    present_in_hq.add((table, str(hq_id)))

                # Check for missing parents
                records_to_sync = [(table, str(row_id)) for row_id in missing_in_hq]

                auto_fetched_parents, records_with_missing_parents = check_and_fetch_missing_parents_for_mirror(
                    self.local_conn,
                    self.hq_conn,
                    records_to_sync,
                    fk_relationships,
                    present_in_hq
                )

                # Recursively resolve parent dependencies
                if auto_fetched_parents:
                    LOG.info(f"🔄 Resolving parent dependencies recursively...")
                    all_parents_ordered = resolve_parent_dependencies_recursive(
                        self.local_conn,
                        self.hq_conn,
                        auto_fetched_parents,
                        fk_relationships,
                        present_in_hq,
                        max_depth=5
                    )

                    LOG.info(f"📦 Total parents to sync first: {len(all_parents_ordered)}")

                    # Sync all parents first (in dependency order)
                    for parent_table, parent_id, parent_data in all_parents_ordered:
                        try:
                            if self.sync_record_to_hq(parent_table, parent_id, parent_data):
                                synced_to_hq += 1
                                LOG.debug(f"   ✅ Synced parent: {parent_table}[{parent_id}]")
                            else:
                                errors += 1
                                LOG.warning(f"   ❌ Failed parent: {parent_table}[{parent_id}]")
                        except Exception as e:
                            errors += 1
                            LOG.error(f"   ❌ Error syncing parent {parent_table}[{parent_id}]: {e}")

                # Now sync the original missing records
                LOG.info(f"")
                LOG.info(f"📤 Syncing {len(missing_in_hq)} original records...")

                for i, row_id in enumerate(missing_in_hq, 1):
                    if i % 50 == 0:
                        LOG.info(f"   Progress: {i}/{len(missing_in_hq)}")

                    # Skip if this record had missing parents that weren't resolved
                    if (table, str(row_id)) in records_with_missing_parents:
                        LOG.warning(f"   ⏸️ Deferring {row_id} (unresolved parent dependencies)")
                        errors += 1
                        continue

                    try:
                        record = self.get_record_data(self.local_conn, table, row_id)
                        if record and self.sync_record_to_hq(table, row_id, record['data']):
                            synced_to_hq += 1
                        else:
                            errors += 1
                    except Exception as e:
                        LOG.error(f"   ❌ Error syncing record {row_id}: {e}")
                        errors += 1

                deferred_count = len(records_with_missing_parents)
                if deferred_count > 0:
                    LOG.warning(f"")
                    LOG.warning(f"⏸️ {deferred_count} records deferred (unresolved dependencies)")

                LOG.info(f"   ✅ Synced {synced_to_hq}/{len(missing_in_hq)} records to HQ")

        # ============================================================
        # 🎯 PHASE 2: Sync HQ → LOCAL (with FK dependency resolution) ✨ NEW
        # ============================================================
        if direction in [SyncDirection.HQ_TO_LOCAL, SyncDirection.BIDIRECTIONAL]:
            if missing_in_local:
                LOG.info(f"")
                LOG.info(f"📥 PHASE 2: Syncing {len(missing_in_local)} records from HQ → Local")
                LOG.info(f"   (with intelligent FK dependency resolution) ✨")
                LOG.info("")

                # 🆕 Get FK relationships for LOCAL database
                fk_relationships_local = get_fk_relationships_for_mirror(self.local_conn)

                # 🆕 Build set of records already in Local
                present_in_local = set()
                local_ids = self.get_table_record_ids(self.local_conn, table)
                for local_id in local_ids:
                    present_in_local.add((table, str(local_id)))

                # 🆕 Check for missing parents (fetch from HQ if needed)
                records_to_sync_local = [(table, str(row_id)) for row_id in missing_in_local]

                auto_fetched_parents_hq, records_with_missing_parents_hq = check_and_fetch_missing_parents_for_mirror(
                    self.hq_conn,  # 🔄 Source is now HQ
                    self.local_conn,  # 🔄 Destination is Local
                    records_to_sync_local,
                    fk_relationships_local,
                    present_in_local
                )

                # 🆕 Recursively resolve parent dependencies from HQ
                if auto_fetched_parents_hq:
                    LOG.info(f"🔄 Resolving HQ parent dependencies recursively...")
                    all_parents_ordered_hq = resolve_parent_dependencies_recursive(
                        self.hq_conn,  # 🔄 Source
                        self.local_conn,  # 🔄 Destination
                        auto_fetched_parents_hq,
                        fk_relationships_local,
                        present_in_local,
                        max_depth=5
                    )

                    LOG.info(f"📦 Total HQ parents to sync first: {len(all_parents_ordered_hq)}")

                    # 🆕 Sync all HQ parents to Local first (in dependency order)
                    for parent_table, parent_id, parent_data in all_parents_ordered_hq:
                        try:
                            if self.sync_record_to_local(parent_table, parent_id, parent_data):
                                synced_to_local += 1
                                LOG.debug(f"   ✅ Synced HQ parent: {parent_table}[{parent_id}]")
                            else:
                                errors += 1
                                LOG.warning(f"   ❌ Failed HQ parent: {parent_table}[{parent_id}]")
                        except Exception as e:
                            errors += 1
                            LOG.error(f"   ❌ Error syncing HQ parent {parent_table}[{parent_id}]: {e}")

                # 🆕 Now sync the original HQ records to Local
                LOG.info(f"")
                LOG.info(f"📥 Syncing {len(missing_in_local)} HQ records to Local...")

                for i, row_id in enumerate(missing_in_local, 1):
                    if i % 50 == 0:
                        LOG.info(f"   Progress: {i}/{len(missing_in_local)}")

                    # Skip if this record had missing parents that weren't resolved
                    if (table, str(row_id)) in records_with_missing_parents_hq:
                        LOG.warning(f"   ⏸️ Deferring {row_id} (unresolved HQ parent dependencies)")
                        errors += 1
                        continue

                    try:
                        record = self.get_record_data(self.hq_conn, table, row_id)
                        if record and self.sync_record_to_local(table, row_id, record['data']):
                            synced_to_local += 1
                        else:
                            errors += 1
                    except Exception as e:
                        LOG.error(f"   ❌ Error syncing HQ record {row_id}: {e}")
                        errors += 1

                deferred_count_hq = len(records_with_missing_parents_hq)
                if deferred_count_hq > 0:
                    LOG.warning(f"")
                    LOG.warning(f"⏸️ {deferred_count_hq} HQ records deferred (unresolved dependencies)")

                LOG.info(f"   ✅ Synced {synced_to_local}/{len(missing_in_local)} records to Local")

        # ============================================================
        # PHASE 3: Resolve conflicts (unchanged)
        # ============================================================
        if conflicts and direction == SyncDirection.BIDIRECTIONAL:
            LOG.info(f"")
            LOG.info(f"⚔️ Resolving {len(conflicts)} conflicts...")

            resolved = 0
            for conflict in conflicts:
                try:
                    if self.resolve_conflict(conflict, conflict_strategy):
                        resolved += 1
                    else:
                        errors += 1
                except Exception as e:
                    LOG.error(f"   ❌ Error resolving conflict: {e}")
                    errors += 1

            LOG.info(f"   ✅ Resolved {resolved}/{len(conflicts)} conflicts")

        duration = time.time() - start_time

        # Create statistics
        try:
            final_hq_count = len(self.get_table_record_ids(self.hq_conn, table))
            final_local_count = len(self.get_table_record_ids(self.local_conn, table))
        except Exception as e:
            LOG.warning(f"   ⚠️ Could not get final counts: {e}")
            final_hq_count = 0
            final_local_count = 0

        stats = MirrorStats(
            table=table,
            hq_count=final_hq_count,
            local_count=final_local_count,
            missing_in_hq=len(missing_in_hq),
            missing_in_local=len(missing_in_local),
            conflicts=len(conflicts),
            synced_to_hq=synced_to_hq,
            synced_to_local=synced_to_local,
            errors=errors,
            duration_seconds=duration
        )

        LOG.info("")
        LOG.info("=" * 80)
        LOG.info(f"✅ MIRROR COMPLETE: {table}")
        LOG.info(f"   Duration: {duration:.2f}s")
        LOG.info(f"   Synced to HQ: {synced_to_hq}")
        LOG.info(f"   Synced to Local: {synced_to_local}")
        LOG.info(f"   Conflicts resolved: {len(conflicts)}")
        LOG.info(f"   Errors: {errors}")
        LOG.info(f"   Final HQ count: {final_hq_count}")
        LOG.info(f"   Final Local count: {final_local_count}")
        LOG.info("=" * 80)

        return stats

    def disconnect(self):
        """Close database connections"""
        if self.hq_conn:
            try:
                self.hq_conn.close()
                LOG.info("🔌 Disconnected from HQ database")
            except Exception as e:
                LOG.warning(f"Error disconnecting from HQ: {e}")

        if self.local_conn:
            try:
                self.local_conn.close()
                LOG.info("🔌 Disconnected from local database")
            except Exception as e:
                LOG.warning(f"Error disconnecting from local: {e}")

    def verify_table_exists(self, conn, table: str) -> bool:
        """
        ✅ FIXED: Verify that a table exists in the database
        Uses REGULAR cursor (not RealDictCursor) for EXISTS query
        """
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        # Remove any existing quotes
        tbl = tbl.strip('"')

        try:
            # ✅ FIX: Use regular cursor for EXISTS query (returns tuple)
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = %s
                        AND table_name = %s
                    )
                """, (schema, tbl))

                # ✅ FIX: Now [0] works because it's a tuple, not a dict
                exists = cur.fetchone()[0]

                if not exists:
                    LOG.warning(f"   ⚠️ Table does not exist: {schema}.{tbl}")

                return exists
        except Exception as e:
            LOG.error(f"   ❌ Error checking if table {table} exists: {e}")
            return False

    def get_table_record_ids(self, conn, table: str) -> Set[str]:
        """
        ✅ SIMPLIFIED: Get record IDs - checks ONLY pending_delete
        - No more active_status checks
        - Simpler, cleaner code
        - Proper error handling with rollback
        """
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        tbl = tbl.strip('"')
        quoted_table = f'"{tbl}"'
        full_table = f"{schema}.{quoted_table}"

        try:
            with conn.cursor() as cur:
                # Check ONLY for pending_delete column
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns
                        WHERE table_schema = %s
                        AND table_name = %s
                        AND column_name = 'pending_delete'
                    )
                """, (schema, tbl))

                has_pending_delete = cur.fetchone()[0]

                # Simple WHERE clause
                if has_pending_delete:
                    where_sql = "WHERE (pending_delete = FALSE OR pending_delete IS NULL)"
                else:
                    where_sql = ""  # Get ALL records if no pending_delete column

                query = f"""
                    SELECT id::text
                    FROM {full_table}
                    {where_sql}
                """

                cur.execute(query)
                ids = {row[0] for row in cur.fetchall()}

                LOG.debug(f"   ✅ Found {len(ids)} active record IDs in {table}")
                return ids

        except psycopg2.Error as e:
            LOG.error(f"❌ Database error getting record IDs for {table}:")
            LOG.error(f"   Error code: {e.pgcode}")
            LOG.error(f"   Error message: {str(e).split('CONTEXT:')[0].strip()}")
            try:
                conn.rollback()
            except:
                pass
            return set()
        except Exception as e:
            LOG.error(f"❌ Error getting record IDs for {table}: {str(e)}")
            try:
                conn.rollback()
            except:
                pass
            return set()

    def diagnose_table_issue(self, conn, table: str, conn_name: str):
        """
        ✅ FIXED: Diagnose why a table might be failing
        Uses appropriate cursor types for different queries
        """
        LOG.info(f"🔍 Diagnosing {conn_name} table: {table}")

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        tbl = tbl.strip('"')

        try:
            # ✅ FIX: Use regular cursor for EXISTS check
            with conn.cursor() as cur:
                # Check if table exists
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = %s AND table_name = %s
                    )
                """, (schema, tbl))

                # ✅ FIX: This now works because regular cursor returns tuple
                exists = cur.fetchone()[0]

                if not exists:
                    LOG.error(f"   ❌ Table does not exist in {conn_name}")
                    return

                LOG.info(f"   ✅ Table exists in {conn_name}")

            # ✅ Use RealDictCursor for detailed info (with proper dict access)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get column info
                cur.execute("""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    LIMIT 10
                """, (schema, tbl))

                cols = cur.fetchall()
                LOG.info(f"   📋 Columns ({len(cols)} shown):")
                for col in cols:
                    # ✅ FIX: Access dict keys by name, not index
                    LOG.info(f"      - {col['column_name']}: {col['data_type']} "
                            f"{'NULL' if col['is_nullable'] == 'YES' else 'NOT NULL'}")

            # ✅ Use regular cursor for EXISTS check
            with conn.cursor() as cur:
                # Check for 'id' column
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = %s AND column_name = 'id'
                    )
                """, (schema, tbl))

                has_id = cur.fetchone()[0]
                if not has_id:
                    LOG.error(f"   ❌ Table missing 'id' column!")
                else:
                    LOG.info(f"   ✅ Has 'id' column")

                    # Try to count records
                    try:
                        cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{tbl}"')
                        count = cur.fetchone()[0]
                        LOG.info(f"   📊 Total records: {count}")
                    except Exception as e:
                        LOG.error(f"   ❌ Could not count records: {e}")

        except Exception as e:
            LOG.error(f"   ❌ Diagnostic failed for {conn_name}: {e}")
            import traceback
            LOG.error(traceback.format_exc())

    def get_record_data(self, conn, table: str, row_id: str) -> Optional[Dict]:
        """
        Get complete record data

        Args:
            conn: Database connection
            table: Table name
            row_id: Record ID

        Returns:
            Record data as dictionary
        """
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        tbl = tbl.strip('"')
        quoted_table = f'"{tbl}"'
        full_table = f"{schema}.{quoted_table}"

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT
                        to_jsonb(t.*) as data,
                        updated_at,
                        created_at
                    FROM {full_table} t
                    WHERE id = %s
                """, (row_id,))

                result = cur.fetchone()
                if result:
                    return {
                        'data': result['data'],
                        'updated_at': result['updated_at'],
                        'created_at': result['created_at']
                    }
                return None

        except Exception as e:
            LOG.error(f"❌ Error getting record data for {table}[{row_id}]: {e}")
            return None

    def compare_table_contents(self, table: str) -> Tuple[Set[str], Set[str], Set[str]]:
        """
        Compare table contents between HQ and local

        Args:
            table: Table name

        Returns:
            Tuple of (missing_in_hq, missing_in_local, common_ids)
        """
        LOG.info(f"🔍 Comparing table: {table}")

        # Get all IDs from both databases
        hq_ids = self.get_table_record_ids(self.hq_conn, table)
        local_ids = self.get_table_record_ids(self.local_conn, table)

        # Calculate differences
        missing_in_hq = local_ids - hq_ids
        missing_in_local = hq_ids - local_ids
        common_ids = hq_ids & local_ids

        LOG.info(f"   📊 HQ records: {len(hq_ids)}")
        LOG.info(f"   📊 Local records: {len(local_ids)}")
        LOG.info(f"   ➕ Missing in HQ: {len(missing_in_hq)}")
        LOG.info(f"   ➖ Missing in Local: {len(missing_in_local)}")
        LOG.info(f"   ✅ Common records: {len(common_ids)}")

        return missing_in_hq, missing_in_local, common_ids

    def detect_conflicts(self, table: str, common_ids: Set[str]) -> List[RecordDifference]:
        """
        Detect conflicts in common records (same ID but different data)

        Args:
            table: Table name
            common_ids: Set of IDs present in both databases

        Returns:
            List of conflicts
        """
        conflicts = []

        if not common_ids:
            return conflicts

        LOG.info(f"🔍 Checking {len(common_ids)} common records for conflicts...")

        checked = 0
        for row_id in common_ids:
            checked += 1
            if checked % 100 == 0:
                LOG.info(f"   Progress: {checked}/{len(common_ids)}")

            hq_record = self.get_record_data(self.hq_conn, table, row_id)
            local_record = self.get_record_data(self.local_conn, table, row_id)

            if not hq_record or not local_record:
                continue

            hq_updated = hq_record['updated_at']
            local_updated = local_record['updated_at']

            # Check if timestamps differ significantly (more than 1 second)
            if hq_updated and local_updated:
                time_diff = abs((hq_updated - local_updated).total_seconds())

                if time_diff > 1.0:
                    conflicts.append(RecordDifference(
                        table=table,
                        row_id=row_id,
                        exists_in_hq=True,
                        exists_in_local=True,
                        hq_data=hq_record['data'],
                        local_data=local_record['data'],
                        hq_updated_at=hq_updated,
                        local_updated_at=local_updated
                    ))

        if conflicts:
            LOG.warning(f"   ⚠️ Found {len(conflicts)} conflicts")
        else:
            LOG.info(f"   ✅ No conflicts detected")

        return conflicts

    def sync_record_to_hq(self, table: str, row_id: str, data: Dict) -> bool:
        """
        ✅ FIXED: Sync record from local to HQ
        - Wraps dicts in Json() adapter
        - Proper error handling with rollback
        """
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        tbl = tbl.strip('"')
        quoted_table = f'"{tbl}"'
        full_table = f"{schema}.{quoted_table}"

        try:
            with self.hq_conn.cursor() as cur:
                # Get JSON/JSONB columns
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s
                    AND data_type IN ('json', 'jsonb')
                """, (schema, tbl))

                json_columns = {row[0] for row in cur.fetchall()}

                # Prepare data with JSON wrapping
                prepared_data = {}
                for key, value in data.items():
                    if value is None:
                        prepared_data[key] = None
                    elif key in json_columns or isinstance(value, (dict, list)):
                        if isinstance(value, (dict, list)):
                            prepared_data[key] = Json(value)
                        elif isinstance(value, str):
                            try:
                                parsed = json.loads(value)
                                prepared_data[key] = Json(parsed)
                            except:
                                prepared_data[key] = Json(value)
                        else:
                            prepared_data[key] = Json(value)
                    else:
                        prepared_data[key] = value

                cols = list(prepared_data.keys())
                vals = [prepared_data[c] for c in cols]

                col_list = ", ".join([f'"{c}"' for c in cols])
                placeholders = ", ".join(["%s"] * len(cols))
                set_clause = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in cols])

                sql = f"""
                    INSERT INTO {full_table} ({col_list})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO UPDATE SET {set_clause}
                """

                cur.execute(sql, vals)
                self.hq_conn.commit()

                LOG.debug(f"   ✅ Synced to HQ: {table}[{row_id}]")
                return True

        except psycopg2.errors.UniqueViolation as uv_error:
            # HQ is the source of truth. This local row collides with a
            # *different* row HQ already has under the same non-PK unique
            # value (e.g. two branches each auto-created their own "next
            # cycle" schedule for the same equipment+month). HQ's existing
            # row wins: re-point local references onto it and retire the
            # local duplicate instead of leaving it stuck retrying forever.
            try:
                self.hq_conn.rollback()
            except Exception:
                pass

            error_msg = str(uv_error)
            try:
                dup_columns = error_msg.split("Key (")[1].split(")=")[0]
                dup_values = error_msg.split(")=(")[1].split(")")[0]
            except Exception:
                dup_columns = dup_values = None

            resolved = False
            if dup_columns and dup_values:
                try:
                    resolved = self._resolve_unique_conflict_hq_wins_upload(
                        schema=schema, table=tbl,
                        dup_columns=dup_columns, dup_values=dup_values,
                        local_loser_id=row_id,
                    )
                except Exception as resolve_error:
                    LOG.error(f"   ❌ Auto-resolve raised an error for {table}: {resolve_error}")

            if resolved:
                LOG.info(
                    f"✅ Auto-resolved unique conflict on {table}[{row_id}] — "
                    f"HQ's existing row wins, local duplicate retired."
                )
            else:
                LOG.warning(
                    f"   ⚠️  Unique conflict syncing {table}[{row_id}] to HQ could not be "
                    f"auto-resolved ({error_msg[:200]}) — leaving for manual review."
                )
            return False

        except Exception as e:
            LOG.error(f"   ❌ Failed to sync to HQ {table}[{row_id}]: {e}")
            try:
                self.hq_conn.rollback()
            except:
                pass
            return False

    def _resolve_unique_conflict_hq_wins_upload(
        self, schema: str, table: str, dup_columns: str, dup_values: str, local_loser_id: str,
    ) -> bool:
        """
        Upload-direction counterpart to the download-apply auto-resolver in
        sync_agent_6.py. HQ already has a row occupying this unique slot
        under a different id; find it, re-point every *local* FK reference
        from ``local_loser_id`` onto HQ's id, and delete the local duplicate.
        """
        columns = [c.strip().strip('"') for c in dup_columns.split(",")]
        values = [v.strip() for v in dup_values.split(",")]
        if not columns or len(columns) != len(values):
            return False

        with self.hq_conn.cursor(cursor_factory=RealDictCursor) as hq_cur:
            hq_cur.execute(
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
            pk_row = hq_cur.fetchone()
            if not pk_row:
                return False
            pk_column = pk_row["column_name"]

            where_clause = " AND ".join(f'"{c}" = %s' for c in columns)
            hq_cur.execute(
                f'SELECT "{pk_column}" AS pk FROM "{table}" WHERE {where_clause}',
                tuple(values),
            )
            hq_row = hq_cur.fetchone()
            if not hq_row:
                return False
            hq_winner_id = hq_row["pk"]

        if str(hq_winner_id) == str(local_loser_id):
            return False  # not actually a different row — nothing to resolve

        try:
            with self.local_conn.cursor(cursor_factory=RealDictCursor) as local_cur:
                local_cur.execute(
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
                referencing = local_cur.fetchall()

                for ref in referencing:
                    local_cur.execute(
                        f'UPDATE "{ref["table_name"]}" SET "{ref["column_name"]}" = %s '
                        f'WHERE "{ref["column_name"]}" = %s',
                        (hq_winner_id, local_loser_id),
                    )
                    if local_cur.rowcount:
                        LOG.info(
                            f"   ↳ re-pointed {local_cur.rowcount} row(s) in "
                            f"{ref['table_name']}.{ref['column_name']} from "
                            f"{local_loser_id} to {hq_winner_id}"
                        )

                local_cur.execute(
                    f'DELETE FROM "{table}" WHERE "{pk_column}" = %s', (local_loser_id,)
                )
                self.local_conn.commit()
                LOG.info(
                    f"   ↳ retired local duplicate {table}[{local_loser_id}] "
                    f"in favour of HQ's {hq_winner_id}"
                )
                return True
        except Exception as e:
            try:
                self.local_conn.rollback()
            except Exception:
                pass
            LOG.error(f"   ❌ Failed retiring local duplicate {table}[{local_loser_id}]: {e}")
            return False


    def sync_record_to_local(self, table: str, row_id: str, data: Dict) -> bool:
        """
        ✅ FIXED: Sync record from HQ to local
        - Wraps dicts in Json() adapter
        - Checks only pending_delete column
        - Proper error handling with rollback
        """
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        tbl = tbl.strip('"')
        quoted_table = f'"{tbl}"'
        full_table = f"{schema}.{quoted_table}"

        try:
            with self.local_conn.cursor() as cur:
                # Get JSON/JSONB columns
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s
                    AND data_type IN ('json', 'jsonb')
                """, (schema, tbl))

                json_columns = {row[0] for row in cur.fetchall()}

                # Prepare data with JSON wrapping
                prepared_data = {}
                for key, value in data.items():
                    if value is None:
                        prepared_data[key] = None
                    elif key in json_columns or isinstance(value, (dict, list)):
                        # Wrap dict/list in Json() adapter
                        if isinstance(value, (dict, list)):
                            prepared_data[key] = Json(value)
                        elif isinstance(value, str):
                            try:
                                parsed = json.loads(value)
                                prepared_data[key] = Json(parsed)
                            except:
                                prepared_data[key] = Json(value)
                        else:
                            prepared_data[key] = Json(value)
                    else:
                        prepared_data[key] = value

                cols = list(prepared_data.keys())
                vals = [prepared_data[c] for c in cols]

                col_list = ", ".join([f'"{c}"' for c in cols])
                placeholders = ", ".join(["%s"] * len(cols))
                set_clause = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in cols])

                sql = f"""
                    INSERT INTO {full_table} ({col_list})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO UPDATE SET {set_clause}
                """

                cur.execute(sql, vals)
                self.local_conn.commit()

                LOG.debug(f"   ✅ Synced to Local: {table}[{row_id}]")
                return True

        except Exception as e:
            LOG.error(f"   ❌ Failed to sync to local {table}[{row_id}]: {e}")
            try:
                self.local_conn.rollback()
            except:
                pass
            return False
    def resolve_conflict(self, conflict: RecordDifference,
                        strategy: str = "last_write_wins") -> bool:
        """
        Resolve a conflict between HQ and local

        Args:
            conflict: Conflict information
            strategy: Resolution strategy ('last_write_wins', 'hq_wins', 'local_wins')

        Returns:
            Success status
        """
        if strategy == "last_write_wins":
            newer = conflict.newer_location

            if newer == "hq":
                LOG.info(f"   🔄 Resolving conflict: HQ newer → syncing to local")
                return self.sync_record_to_local(
                    conflict.table,
                    conflict.row_id,
                    conflict.hq_data
                )
            elif newer == "local":
                LOG.info(f"   🔄 Resolving conflict: Local newer → syncing to HQ")
                return self.sync_record_to_hq(
                    conflict.table,
                    conflict.row_id,
                    conflict.local_data
                )
            else:
                LOG.info(f"   ℹ️ Same timestamp, keeping as is")
                return True

        elif strategy == "hq_wins":
            LOG.info(f"   🔄 Resolving conflict: HQ wins → syncing to local")
            return self.sync_record_to_local(
                conflict.table,
                conflict.row_id,
                conflict.hq_data
            )

        elif strategy == "local_wins":
            LOG.info(f"   🔄 Resolving conflict: Local wins → syncing to HQ")
            return self.sync_record_to_hq(
                conflict.table,
                conflict.row_id,
                conflict.local_data
            )

        return False

    def mirror_all_tables(self,
                         tables: List[str],
                         direction: SyncDirection = SyncDirection.BIDIRECTIONAL,
                         conflict_strategy: str = "last_write_wins") -> List[MirrorStats]:
        """
        ✅ ENHANCED: Mirror all specified tables in dependency order

        Args:
            tables: List of table names
            direction: Sync direction
            conflict_strategy: How to resolve conflicts

        Returns:
            List of statistics for each table
        """
        LOG.info("")
        LOG.info("=" * 80)
        LOG.info("🚀 STARTING FULL DATABASE MIRROR")
        LOG.info("=" * 80)
        LOG.info(f"   Tables to mirror: {len(tables)}")
        LOG.info(f"   Direction: {direction.value}")
        LOG.info(f"   Conflict Strategy: {conflict_strategy}")
        LOG.info("=" * 80)

        # ✅ NEW: Sort tables by dependency order to avoid FK violations
        sorted_tables = self._sort_tables_by_dependencies(tables)

        if sorted_tables != tables:
            LOG.info("")
            LOG.info("📊 Tables reordered by dependencies:")
            LOG.info(f"   Original order: {len(tables)} tables")
            LOG.info(f"   Dependency order: {len(sorted_tables)} tables (parents first)")

        all_stats = []
        start_time = time.time()

        for i, table in enumerate(sorted_tables, 1):
            LOG.info(f"")
            LOG.info(f"📋 Progress: {i}/{len(sorted_tables)}")

            try:
                stats = self.mirror_table(table, direction, conflict_strategy)
                all_stats.append(stats)

                # Log summary after each table
                if stats.errors > 0:
                    LOG.warning(f"   ⚠️ Completed with {stats.errors} errors")
                else:
                    LOG.info(f"   ✅ Completed successfully")

            except Exception as e:
                LOG.error(f"❌ Failed to mirror {table}: {e}")
                import traceback
                LOG.error(traceback.format_exc())

                # Create error stats
                error_stats = MirrorStats(
                    table=table,
                    hq_count=0,
                    local_count=0,
                    missing_in_hq=0,
                    missing_in_local=0,
                    conflicts=0,
                    synced_to_hq=0,
                    synced_to_local=0,
                    errors=1,
                    duration_seconds=0
                )
                all_stats.append(error_stats)

        total_duration = time.time() - start_time

        # Calculate totals
        total_synced_to_hq = sum(s.synced_to_hq for s in all_stats)
        total_synced_to_local = sum(s.synced_to_local for s in all_stats)
        total_conflicts = sum(s.conflicts for s in all_stats)
        total_errors = sum(s.errors for s in all_stats)

        LOG.info("")
        LOG.info("=" * 80)
        LOG.info("🎉 FULL MIRROR COMPLETE!")
        LOG.info("=" * 80)
        LOG.info(f"   Total Duration: {total_duration:.2f}s")
        LOG.info(f"   Tables Processed: {len(all_stats)}/{len(tables)}")
        LOG.info(f"   Total Synced to HQ: {total_synced_to_hq}")
        LOG.info(f"   Total Synced to Local: {total_synced_to_local}")
        LOG.info(f"   Total Conflicts Resolved: {total_conflicts}")
        LOG.info(f"   Total Errors: {total_errors}")
        LOG.info("=" * 80)

        # Save detailed report
        self.save_mirror_report(all_stats, total_duration)

        return all_stats

    def broadcast_changes_to_agents(self, stats: List[MirrorStats]) -> bool:
        """
        Broadcast mirror changes to all connected agents via HQ API

        Args:
            stats: List of mirror statistics

        Returns:
            Success status
        """
        if not stats:
            return True

        LOG.info("")
        LOG.info("=" * 80)
        LOG.info("📡 BROADCASTING CHANGES TO ALL AGENTS")
        LOG.info("=" * 80)

        try:
            # Prepare broadcast payload
            payload = {
                "event_type": "mirror_sync_complete",
                "source": "mirror_system",
                "client_id": self.client_id,
                "machine_id": self.machine_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "statistics": [s.to_dict() for s in stats],
                "summary": {
                    "total_synced_to_hq": sum(s.synced_to_hq for s in stats),
                    "total_synced_to_local": sum(s.synced_to_local for s in stats),
                    "total_conflicts": sum(s.conflicts for s in stats),
                    "tables_affected": [s.table for s in stats if s.synced_to_hq > 0 or s.synced_to_local > 0]
                }
            }

            headers = {"Content-Type": "application/json"}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"

            # Trigger download on all agents
            LOG.info("   📤 Sending broadcast to HQ API...")
            response = requests.post(
                f"{self.api_url}/mirror/broadcast",
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                LOG.info("   ✅ Broadcast successful!")
                LOG.info("   🔄 All agents will download updates on next sync cycle")
                return True
            else:
                LOG.warning(f"   ⚠️ Broadcast failed: {response.status_code}")
                LOG.warning(f"   Response: {response.text}")
                return False

        except requests.exceptions.RequestException as e:
            LOG.error(f"   ❌ Broadcast request error: {e}")
            return False
        except Exception as e:
            LOG.error(f"   ❌ Broadcast error: {e}")
            import traceback
            LOG.error(traceback.format_exc())
            return False

    def save_mirror_report(self, stats: List[MirrorStats], total_duration: float):
        """
        Save detailed mirror report to file

        Args:
            stats: List of statistics
            total_duration: Total operation duration
        """
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "client_id": self.client_id,
            "machine_id": self.machine_id,
            "total_duration_seconds": total_duration,
            "summary": {
                "tables_processed": len(stats),
                "total_synced_to_hq": sum(s.synced_to_hq for s in stats),
                "total_synced_to_local": sum(s.synced_to_local for s in stats),
                "total_conflicts": sum(s.conflicts for s in stats),
                "total_errors": sum(s.errors for s in stats)
            },
            "table_details": [s.to_dict() for s in stats]
        }

        # Save to file
        report_file = f"mirror_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        try:
            with open(report_file, 'w') as f:
                json.dump(report, f, indent=2, default=str)

            LOG.info(f"")
            LOG.info(f"📄 Detailed report saved: {report_file}")
        except Exception as e:
            LOG.error(f"❌ Failed to save report: {e}")

    def initial_sync_new_machine(self, tables: List[str]) -> bool:
        """
        Perform initial sync for a new machine (HQ → Local only)

        Args:
            tables: List of tables to sync

        Returns:
            Success status
        """
        LOG.info("")
        LOG.info("=" * 80)
        LOG.info("🆕 INITIAL SYNC FOR NEW MACHINE")
        LOG.info("=" * 80)
        LOG.info("   This will copy all data from HQ to Local")
        LOG.info("=" * 80)

        stats = self.mirror_all_tables(
            tables=tables,
            direction=SyncDirection.HQ_TO_LOCAL,
            conflict_strategy="hq_wins"
        )

        success = all(s.errors == 0 for s in stats)

        if success:
            LOG.info("")
            LOG.info("✅ Initial sync completed successfully!")
            LOG.info("   This machine is now fully synchronized with HQ")
        else:
            LOG.warning("")
            LOG.warning("⚠️ Initial sync completed with some errors")
            LOG.warning("   Check the report for details")

            # Show which tables had errors
            failed_tables = [s.table for s in stats if s.errors > 0]
            if failed_tables:
                LOG.warning(f"   Tables with errors: {', '.join(failed_tables)}")

        return success

    def periodic_consistency_check(self,
                                   tables: List[str],
                                   interval_seconds: int = 3600) -> bool:
        """
        Run periodic consistency checks

        Args:
            tables: List of tables to check
            interval_seconds: Check interval (default: 1 hour)

        Returns:
            Success status
        """
        LOG.info("")
        LOG.info("=" * 80)
        LOG.info("⏰ STARTING PERIODIC CONSISTENCY CHECK")
        LOG.info("=" * 80)
        LOG.info(f"   Interval: {interval_seconds}s ({interval_seconds/3600:.1f}h)")
        LOG.info(f"   Tables: {len(tables)}")
        LOG.info("=" * 80)

        check_count = 0

        try:
            while True:
                check_count += 1

                LOG.info("")
                LOG.info(f"🔍 Consistency Check #{check_count}")
                LOG.info(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                # Perform mirror sync
                stats = self.mirror_all_tables(
                    tables=tables,
                    direction=SyncDirection.BIDIRECTIONAL,
                    conflict_strategy="last_write_wins"
                )

                # Check if any changes were made
                total_changes = sum(s.synced_to_hq + s.synced_to_local for s in stats)

                if total_changes > 0:
                    LOG.info(f"")
                    LOG.info(f"🔄 Detected {total_changes} inconsistencies - fixed!")

                    # Broadcast changes to all agents
                    self.broadcast_changes_to_agents(stats)
                else:
                    LOG.info(f"")
                    LOG.info(f"✅ All databases are consistent!")

                # Wait for next check
                LOG.info(f"")
                LOG.info(f"💤 Next check in {interval_seconds}s...")
                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            LOG.info("")
            LOG.info("ℹ️ Periodic checks stopped by user")
            return True
        except Exception as e:
            LOG.error(f"❌ Periodic check failed: {e}")
            import traceback
            LOG.error(traceback.format_exc())
            return False


class MirrorScheduler:
    """
    Scheduler for automated mirror operations
    """

    def __init__(self, mirror: DatabaseMirror):
        self.mirror = mirror
        self.running = False

    def start_scheduled_sync(self,
                           tables: List[str],
                           interval_hours: float = 1.0,
                           direction: SyncDirection = SyncDirection.BIDIRECTIONAL):
        """
        Start scheduled mirror sync

        Args:
            tables: Tables to sync
            interval_hours: Sync interval in hours
            direction: Sync direction
        """
        self.running = True
        interval_seconds = int(interval_hours * 3600)

        LOG.info("=" * 80)
        LOG.info("📅 SCHEDULED MIRROR SYNC STARTED")
        LOG.info("=" * 80)
        LOG.info(f"   Interval: {interval_hours}h ({interval_seconds}s)")
        LOG.info(f"   Direction: {direction.value}")
        LOG.info(f"   Tables: {len(tables)}")
        LOG.info("=" * 80)

        sync_count = 0

        try:
            while self.running:
                sync_count += 1

                LOG.info("")
                LOG.info(f"🔄 Scheduled Sync #{sync_count}")
                LOG.info(f"   Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                # Connect to databases
                if not self.mirror.connect():
                    LOG.error("❌ Failed to connect to databases")
                    time.sleep(60)
                    continue

                try:
                    # Perform mirror sync
                    stats = self.mirror.mirror_all_tables(
                        tables=tables,
                        direction=direction,
                        conflict_strategy="last_write_wins"
                    )

                    # Broadcast if changes were made
                    total_changes = sum(s.synced_to_hq + s.synced_to_local for s in stats)

                    if total_changes > 0:
                        LOG.info(f"📡 Broadcasting {total_changes} changes to all agents...")
                        self.mirror.broadcast_changes_to_agents(stats)

                finally:
                    self.mirror.disconnect()

                # Wait for next sync
                next_sync = datetime.now() + timedelta(seconds=interval_seconds)
                LOG.info("")
                LOG.info(f"⏳ Next sync at: {next_sync.strftime('%Y-%m-%d %H:%M:%S')}")

                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            LOG.info("")
            LOG.info("ℹ️ Scheduled sync stopped by user")
        except Exception as e:
            LOG.error(f"❌ Scheduler error: {e}")
            import traceback
            LOG.error(traceback.format_exc())
        finally:
            self.running = False

    def stop(self):
        """Stop scheduled sync"""
        self.running = False
        LOG.info("🛑 Stopping scheduled sync...")


# ============================================================
# COMMAND LINE INTERFACE
# ============================================================

def main():
    """Main entry point for mirror system"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Database Mirror & Consistency System"
    )

    parser.add_argument(
        "--mode",
        choices=["once", "scheduled", "initial", "check"],
        required=True,
        help="Operation mode"
    )

    parser.add_argument(
        "--hq-host",
        default=os.getenv("POSTGRES_HQ_HOST", "127.0.0.1"),
        help="HQ database host"
    )

    parser.add_argument(
        "--hq-port",
        type=int,
        default=int(os.getenv("POSTGRES_HQ_PORT", 5432)),
        help="HQ database port"
    )

    parser.add_argument(
        "--hq-db",
        default=os.getenv("POSTGRES_HQ_DB", "b12technologies"),
        help="HQ database name"
    )

    parser.add_argument(
        "--hq-user",
        default=os.getenv("POSTGRES_HQ_USER", "b12technologies"),
        help="HQ database user"
    )

    parser.add_argument(
        "--hq-password",
        default=os.getenv("POSTGRES_HQ_PASSWORD", ""),
        help="HQ database password"
    )

    parser.add_argument(
        "--hq-sslmode",
        default=os.getenv("POSTGRES_SSLMODE", "require"),
        help="HQ database sslmode (e.g. 'require' for Supabase pooler)"
    )

    parser.add_argument(
        "--local-host",
        default=os.getenv("POSTGRES_LOCAL_HOST", "127.0.0.1"),
        help="Local database host"
    )

    parser.add_argument(
        "--local-port",
        type=int,
        default=int(os.getenv("POSTGRES_LOCAL_PORT", 5455)),
        help="Local database port"
    )

    parser.add_argument(
        "--local-db",
        default=os.getenv("POSTGRES_LOCAL_DB", "localdb"),
        help="Local database name"
    )

    parser.add_argument(
        "--local-user",
        default=os.getenv("POSTGRES_LOCAL_USER", "localdb"),
        help="Local database user"
    )

    parser.add_argument(
        "--local-password",
        default=os.getenv("POSTGRES_LOCAL_PASSWORD", ""),
        help="Local database password"
    )

    parser.add_argument(
        "--api-url",
        default=os.getenv("SYNC_API_URL", "https://hq-server-dgs6.onrender.com/api/sync"),
        help="HQ API URL"
    )

    parser.add_argument(
        "--tables",
        default=os.getenv("TABLES", ""),
        help="Comma-separated list of tables"
    )

    parser.add_argument(
        "--direction",
        choices=["hq_to_local", "local_to_hq", "bidirectional"],
        default="bidirectional",
        help="Sync direction"
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Interval in hours for scheduled mode"
    )

    args = parser.parse_args()

    # Parse tables
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]

    if not tables:
        LOG.error("❌ No tables specified!")
        sys.exit(1)

    # Create configurations
    hq_config = {
        "host": args.hq_host,
        "port": args.hq_port,
        "dbname": args.hq_db,
        "user": args.hq_user,
        "password": args.hq_password,
        "sslmode": args.hq_sslmode
    }

    local_config = {
        "host": args.local_host,
        "port": args.local_port,
        "dbname": args.local_db,
        "user": args.local_user,
        "password": args.local_password
    }

    # Map direction
    direction_map = {
        "hq_to_local": SyncDirection.HQ_TO_LOCAL,
        "local_to_hq": SyncDirection.LOCAL_TO_HQ,
        "bidirectional": SyncDirection.BIDIRECTIONAL
    }
    direction = direction_map[args.direction]

    # Create mirror system
    mirror = DatabaseMirror(
        hq_config=hq_config,
        local_config=local_config,
        api_url=args.api_url,
        auth_token=os.getenv("SYNC_AUTH_TOKEN", "")
    )

    # Execute based on mode
    try:
        # Connect
        if not mirror.connect():
            LOG.error("❌ Failed to connect to databases")
            sys.exit(1)

        try:
            if args.mode == "once":
                # Single mirror operation
                stats = mirror.mirror_all_tables(tables, direction)

                # Broadcast changes
                total_changes = sum(s.synced_to_hq + s.synced_to_local for s in stats)
                if total_changes > 0:
                    mirror.broadcast_changes_to_agents(stats)

            elif args.mode == "scheduled":
                # Scheduled mirror operations
                scheduler = MirrorScheduler(mirror)
                scheduler.start_scheduled_sync(tables, args.interval, direction)

            elif args.mode == "initial":
                # Initial sync for new machine
                success = mirror.initial_sync_new_machine(tables)
                sys.exit(0 if success else 1)

            elif args.mode == "check":
                # Periodic consistency check
                mirror.periodic_consistency_check(tables, int(args.interval * 3600))

        finally:
            mirror.disconnect()

    except KeyboardInterrupt:
        LOG.info("\nℹ️ Stopped by user")
        sys.exit(0)
    except Exception as e:
        LOG.error(f"❌ Fatal error: {e}")
        import traceback
        LOG.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
