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
import os
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
    from .sql_ident import qualified
except ImportError:  # loaded as a top-level module with sync/ on sys.path
    from sql_ident import qualified


class ParentRecoveryMixin(SmartDeleteMixin):
    """Auto-recovery of missing FK parent records from HQ."""
    def auto_recover_missing_parents_from_hq(
        self, table: str, row_id: str, fk_column: str, parent_table: str, parent_id: str
    ) -> bool:
        """
        🆕 AUTO-RECOVERY: Fetch missing parent record from HQ and insert locally

        This solves the core issue where equipment/other parent records are missing locally,
        blocking dependent records like calibration sessions from syncing.

        Args:
            table: Child table that has the FK violation
            row_id: Child record ID
            fk_column: Foreign key column name
            parent_table: Parent table name
            parent_id: Missing parent record ID

        Returns:
            True if parent was recovered, False otherwise
        """
        LOG.info("=" * 80)
        LOG.info("🔧 AUTO-RECOVERY: Fetching missing parent from HQ")
        LOG.info(f"   Child: {table}[{row_id}]")
        LOG.info(f"   Missing Parent: {parent_table}[{parent_id}]")
        LOG.info(f"   FK Column: {fk_column}")
        LOG.info("=" * 80)

        try:
            # Ask HQ for the parent through the API (the same tables and row
            # encoding as the bootstrap download). The agent no longer holds
            # HQ database credentials.
            if "." in parent_table:
                schema, tbl = parent_table.split(".", 1)
            else:
                schema, tbl = "public", parent_table
            full_table = qualified(schema, tbl)

            response = requests.post(
                f"{self.api_url}/data_checker/fetch_rows",
                json={"client_id": self.client_id, "table": f"{schema}.{tbl}", "ids": [str(parent_id)]},
                headers=self._http_headers(),
                timeout=30,
            )
            if response.status_code != 200:
                LOG.error(f"   ❌ HQ could not return the parent ({response.status_code}): {response.text[:200]}")
                return False
            rows = (response.json() or {}).get("rows") or []
            if not rows:
                LOG.warning(
                    f"   ❌ Parent record NOT FOUND in HQ: {parent_table}[{parent_id}]"
                )
                LOG.warning(f"      This is an orphaned reference!")
                return False

            try:
                from .data_checker_client import _decode_row
            except ImportError:  # loaded as a top-level module with sync/ on sys.path
                from data_checker_client import _decode_row

            parent_data = _decode_row(rows[0])
            LOG.info(f"   ✅ Found parent in HQ: {parent_table}[{parent_id}]")

            # Now insert the parent record locally
            conn = None
            try:
                conn = self.pool.getconn()

                with conn.cursor() as cur:
                    # Check if parent already exists locally (race condition check)
                    cur.execute(
                        f"""
                            SELECT id FROM {full_table} WHERE id = %s
                        """,
                        (parent_id,),
                    )

                    if cur.fetchone():
                        LOG.info(f"   ℹ️  Parent already exists locally (race condition)")
                        return True

                    # HQ can carry columns this client has no migration for
                    # (e.g. source_updated_at). Drop them as the main apply
                    # path does; otherwise every recovery fails with
                    # UndefinedColumn and the child rows never land.
                    local_columns = self.get_table_schema_info(f"{schema}.{tbl}").get("columns", set())
                    if local_columns:
                        drifted = [c for c in parent_data if c not in local_columns]
                        if drifted:
                            self.record_schema_drift(f"{schema}.{tbl}", drifted, row_id=parent_id)
                            parent_data = {k: v for k, v in parent_data.items() if k in local_columns}

                    # Prepare insert
                    cols = list(parent_data.keys())
                    vals = [
                        Json(parent_data[c]) if isinstance(parent_data[c], (dict, list)) else parent_data[c]
                        for c in cols
                    ]

                    col_list = ", ".join([f'"{c}"' for c in cols])
                    placeholders = ", ".join(["%s"] * len(cols))

                    insert_sql = f"""
                            INSERT INTO {full_table} ({col_list})
                            VALUES ({placeholders})
                            ON CONFLICT (id) DO NOTHING
                        """

                    LOG.info(f"   📥 Inserting parent into local database...")
                    cur.execute(insert_sql, vals)

                    if cur.rowcount > 0:
                        conn.commit()
                        LOG.info(f"   ✅ AUTO-RECOVERY SUCCESS!")
                        LOG.info(
                            f"      Parent record {parent_table}[{parent_id}] copied from HQ to local"
                        )
                        LOG.info("=" * 80)
                        return True
                    else:
                        LOG.info(f"   ℹ️  Parent already inserted (conflict)")
                        return True

            except Exception as local_error:
                LOG.error(f"   ❌ Failed to insert parent locally: {local_error}")
                if conn:
                    conn.rollback()
                return False
            finally:
                if conn:
                    self.pool.putconn(conn)

        except Exception as e:
            LOG.error(f"❌ AUTO-RECOVERY FAILED: {e}")
            LOG.exception(e)
            return False
