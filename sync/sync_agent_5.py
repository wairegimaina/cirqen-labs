from .agent_prelude import LOG, now_kenyan, now_utc, now_iso, format_kenyan_time
from .agent_prelude import KENYAN_TZ, PerformanceMonitor, encrypt_token, setup_logging, load_agent_config
from .agent_prelude import load_config_from_unified_manager, load_config_from_env_fallback
from .agent_prelude import sleep_with_jitter, is_online, CERT_TABLES, DEFAULT_CONFIG
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

class SyncAgent(SmartDeleteMixin):
    def auto_recover_missing_parents_from_hq(self, table: str, row_id: str, fk_column: str, parent_table: str, parent_id: str) -> bool:
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
                # Query HQ database directly for the missing parent
                hq_config = {
                    "host": os.getenv("POSTGRES_HQ_HOST", "dpg-d7rk2sa8qa3s73diimb0-a.ohio-postgres.render.com"),
                    "port": int(os.getenv("POSTGRES_HQ_PORT", "5432")),
                    "dbname": os.getenv("POSTGRES_HQ_DB", "b12technologies"),
                    "user": os.getenv("POSTGRES_HQ_USER", "b12technologies"),
                    "password": os.getenv("POSTGRES_HQ_PASSWORD", "")
                }

                # Connect to HQ database
                import psycopg2
                from psycopg2.extras import RealDictCursor

                hq_conn = psycopg2.connect(**hq_config, cursor_factory=RealDictCursor)

                try:
                    if "." in parent_table:
                        schema, tbl = parent_table.split(".", 1)
                    else:
                        schema, tbl = "public", parent_table

                    quoted_table = f'"{tbl}"'
                    full_table = f"{schema}.{quoted_table}"

                    # Fetch parent record from HQ
                    with hq_conn.cursor() as cur:
                        cur.execute(f"""
                            SELECT to_jsonb(t.*) as data
                            FROM {full_table} t
                            WHERE id = %s
                        """, (parent_id,))

                        result = cur.fetchone()

                        if not result:
                            LOG.warning(f"   ❌ Parent record NOT FOUND in HQ: {parent_table}[{parent_id}]")
                            LOG.warning(f"      This is an orphaned reference!")
                            return False

                        parent_data = result['data']
                        LOG.info(f"   ✅ Found parent in HQ: {parent_table}[{parent_id}]")

                finally:
                    hq_conn.close()

                # Now insert the parent record locally
                conn = None
                try:
                    conn = self.pool.getconn()

                    with conn.cursor() as cur:
                        # Check if parent already exists locally (race condition check)
                        cur.execute(f"""
                            SELECT id FROM {full_table} WHERE id = %s
                        """, (parent_id,))

                        if cur.fetchone():
                            LOG.info(f"   ℹ️  Parent already exists locally (race condition)")
                            return True

                        # Prepare insert
                        cols = list(parent_data.keys())
                        vals = [parent_data[c] for c in cols]

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
                            LOG.info(f"      Parent record {parent_table}[{parent_id}] copied from HQ to local")
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
