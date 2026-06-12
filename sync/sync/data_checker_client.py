#!/usr/bin/env python3
"""
data_checker_client.py  –  Client-side Bootstrap & Data-Integrity Module
=========================================================================

Purpose
-------
When a desktop is reinstalled, the local PostgreSQL database starts
completely empty.  The normal audit-log download only returns *changes*
since a timestamp — it cannot reconstruct records that predate the audit
log or were never in the log at all.

This module provides:

  DataCheckerClient
  -----------------
  A class that:

  1. **check_and_sync()** (call on startup):
     • Counts local rows per table.
     • Sends counts to HQ's ``POST /api/sync/data_checker``.
     • For every out-of-sync table, fetches all pages from HQ's
       ``POST /api/sync/data_checker/fetch_table``.
     • Upserts each row into the local database.
     • Returns a summary dict.

  2. **compute_local_counts()** – standalone helper.

  3. **compute_local_checksums()** – optional; sends checksums too so HQ
     can detect row-level differences even when counts match.

Usage (in sync_agent.py startup)
---------------------------------

    from data_checker_client import DataCheckerClient

    checker = DataCheckerClient(
        hq_url=self.server_url,          # e.g. "https://hq-server.onrender.com"
        api_key=self.api_key,
        client_id=self.client_id,
        local_pool=self.pool,            # psycopg2 connection pool
        allowed_tables=self.tables,
        logger=LOG,
    )

    result = checker.check_and_sync()
    if result["synced_tables"]:
        LOG.info("Bootstrap complete: %d tables synced", len(result["synced_tables"]))

Notes
-----
* Uses only ``requests`` (synchronous) – no asyncio needed.
* Each fetched row is upserted with ``ON CONFLICT (id) DO UPDATE SET …``
  to be idempotent.
* Binary values encoded as ``__b64__:<base64>`` by the server are decoded
  back to ``bytes`` before inserting.
* All operations are wrapped in try/except; a failure on one table does
  not abort the rest.
"""

import base64
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from psycopg2.extras import RealDictCursor, Json

LOG = logging.getLogger("data_checker_client")

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_BATCH_SIZE = 500
REQUEST_TIMEOUT = 60          # seconds per HTTP call
MAX_RETRIES = 3
RETRY_DELAY = 2.0             # seconds between retries


# ──────────────────────────────────────────────────────────────────────────────
# Dependency-aware table ordering
# ──────────────────────────────────────────────────────────────────────────────
#
# The server now auto-discovers FK dependencies from the DB and sends them in
# two places:
#
#   POST /api/sync/data_checker  → response["sync_order"]        (preferred)
#   GET  /api/sync/data_checker/status → response["table_dependencies"]
#
# The client always prefers the server-provided ``sync_order`` list.
# ``_sort_tables_by_dependency`` is only called when the server is an older
# version that does not include ``sync_order``, using whatever dep_map the
# caller passes.  ``CLIENT_TABLE_DEPENDENCIES`` below is kept as a last-resort
# static fallback for that case – it does NOT need to be kept up to date once
# all servers are on the new version.
#
CLIENT_TABLE_DEPENDENCIES: Dict[str, List[str]] = {
    "public.users_userprofile":           ["public.users_customuser"],
    "public.users_usersignature":         ["public.users_customuser"],
    "public.Inventory_equipment":         ["public.workshop_workshop"],
    "public.Inventory_equipmentdocument": ["public.Inventory_equipment"],
    "public.Inventory_equipmentimage":    ["public.Inventory_equipment"],
    "public.Inventory_maintenancelog":    [
        "public.Inventory_equipment",
        "public.users_customuser",
    ],
    "public.CalSoft_calibrationreport":   [
        "public.Inventory_equipment",
        "public.users_customuser",
    ],
    "public.CalSoft_calibrationschedule": [
        "public.Inventory_equipment",
        "public.users_customuser",
    ],
    "public.CalSoft_driftdatapoint":      ["public.CalSoft_calibrationreport"],
    "public.workshop_workshopassignment": [
        "public.workshop_workshop",
        "public.users_customuser",
    ],
}


def _sort_tables_by_dependency(
    tables: List[str],
    dep_map: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """
    Topological sort so parent tables are always synced before children.

    Parameters
    ----------
    tables:
        The list of tables to sort (only tables in this list are considered).
    dep_map:
        Dependency map to use.  Defaults to CLIENT_TABLE_DEPENDENCIES.

    Raises ValueError on a dependency cycle (should not happen in practice).
    """
    if dep_map is None:
        dep_map = CLIENT_TABLE_DEPENDENCIES

    table_set = set(tables)
    graph: Dict[str, List[str]] = {t: [] for t in tables}
    in_degree: Dict[str, int] = {t: 0 for t in tables}

    for table in tables:
        for parent in dep_map.get(table, []):
            if parent in table_set:
                graph[parent].append(table)
                in_degree[table] += 1

    queue = sorted(t for t in tables if in_degree[t] == 0)
    result: List[str] = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for child in sorted(graph[node]):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if len(result) != len(tables):
        cycled = set(tables) - set(result)
        raise ValueError(f"Dependency cycle detected: {cycled}")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _split_table(table: str) -> Tuple[str, str]:
    if "." in table:
        schema, tbl = table.split(".", 1)
        return schema, tbl
    return "public", table


def _quoted(schema: str, tbl: str) -> str:
    return f'"{schema}"."{tbl}"'


def _decode_value(value: Any) -> Any:
    """
    Reverse the server-side ``_safe_serialize`` encoding:
    * ``"__b64__:<data>"``  →  bytes
    * everything else       →  unchanged
    """
    if isinstance(value, str) and value.startswith("__b64__:"):
        return base64.b64decode(value[len("__b64__:"):])
    return value


def _decode_row(row: Dict) -> Dict:
    return {k: _decode_value(v) for k, v in row.items()}


# ──────────────────────────────────────────────────────────────────────────────
# DataCheckerClient
# ──────────────────────────────────────────────────────────────────────────────

class DataCheckerClient:
    """
    Client-side counterpart to the HQ data_checker module.

    Parameters
    ----------
    hq_url:
        Base URL of the HQ server, e.g. ``"https://hq-server.onrender.com"``.
    api_key:
        API key used for all requests (sent as ``X-API-Key`` header).
    client_id:
        Machine / client identifier string.
    local_pool:
        A ``psycopg2`` ThreadedConnectionPool connected to the *local*
        database.
    allowed_tables:
        The list of tables that should be checked/synced.
    logger:
        Optional logger instance; falls back to the module logger.
    batch_size:
        Rows to fetch per page from HQ (default 500).
    """

    def __init__(
        self,
        hq_url: str,
        api_key: str,
        client_id: str,
        local_pool,
        allowed_tables: List[str],
        logger: Optional[logging.Logger] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        self.hq_url = hq_url.rstrip("/")
        self.api_key = api_key
        self.client_id = client_id
        self.pool = local_pool
        self.allowed_tables = allowed_tables
        self.logger = logger or LOG
        self.batch_size = batch_size

        self._session = requests.Session()
        self._session.headers.update({
            "X-API-Key": self.api_key,
            "X-Client-ID": self.client_id,
            "Content-Type": "application/json",
        })

    # ─────────────────────────────────────────────────────────────────
    # Local DB helpers
    # ─────────────────────────────────────────────────────────────────

    def compute_local_counts(self) -> Dict[str, int]:
        """Return {table: local_row_count} for all allowed tables."""
        counts: Dict[str, int] = {}
        conn = self.pool.getconn()
        try:
            for table in self.allowed_tables:
                schema, tbl = _split_table(table)
                full = _quoted(schema, tbl)
                try:
                    with conn.cursor() as cur:
                        cur.execute(f"SELECT COUNT(*) FROM {full}")
                        counts[table] = cur.fetchone()[0]
                except Exception as exc:
                    self.logger.warning("Count failed for %s: %s", table, exc)
                    counts[table] = -1
        finally:
            self.pool.putconn(conn)
        return counts

    def compute_local_checksums(self) -> Dict[str, str]:
        """
        Compute MD5 over (id, updated_at) for each table.
        Only included in the request when ``send_checksums=True`` is
        passed to ``check_and_sync()``.
        """
        checksums: Dict[str, str] = {}
        conn = self.pool.getconn()
        try:
            for table in self.allowed_tables:
                schema, tbl = _split_table(table)
                full = _quoted(schema, tbl)
                try:
                    with conn.cursor() as cur:
                        # Check columns
                        cur.execute(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = %s AND table_name = %s
                              AND column_name IN ('id', 'updated_at')
                            """,
                            (schema, tbl),
                        )
                        cols = {r[0] for r in cur.fetchall()}
                        if "id" not in cols:
                            continue

                        if "updated_at" in cols:
                            cur.execute(
                                f"SELECT id, updated_at FROM {full} ORDER BY id"
                            )
                        else:
                            cur.execute(f"SELECT id FROM {full} ORDER BY id")

                        rows = cur.fetchall()
                        checksums[table] = hashlib.md5(
                            json.dumps(rows, default=str).encode()
                        ).hexdigest()
                except Exception as exc:
                    self.logger.debug("Checksum skipped for %s: %s", table, exc)
        finally:
            self.pool.putconn(conn)
        return checksums

    def _upsert_rows(self, table: str, rows: List[Dict]) -> Tuple[int, int]:
        """
        Upsert *rows* into the local *table*.

        Returns (inserted_or_updated, failed) counts.
        """
        if not rows:
            return 0, 0

        schema, tbl = _split_table(table)
        full = _quoted(schema, tbl)

        # Decode any binary values
        decoded_rows = [_decode_row(r) for r in rows]

        conn = self.pool.getconn()
        ok = failed = 0
        try:
            with conn.cursor() as cur:
                # Discover JSON/JSONB columns once
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                      AND data_type IN ('json', 'jsonb')
                    """,
                    (schema, tbl),
                )
                json_cols = {r[0] for r in cur.fetchall()}

            for row in decoded_rows:
                try:
                    processed: Dict[str, Any] = {}
                    for k, v in row.items():
                        if k.startswith("_"):
                            continue
                        if v is None:
                            processed[k] = None
                        elif k in json_cols:
                            try:
                                processed[k] = Json(
                                    v if isinstance(v, (dict, list))
                                    else json.loads(v)
                                )
                            except Exception:
                                processed[k] = Json(v)
                        elif isinstance(v, (dict, list)):
                            processed[k] = Json(v)
                        else:
                            processed[k] = v

                    cols = list(processed.keys())
                    if not cols:
                        continue

                    col_list = ", ".join(f'"{c}"' for c in cols)
                    placeholders = ", ".join(["%s"] * len(cols))
                    set_clause = ", ".join(
                        f'"{c}" = EXCLUDED."{c}"'
                        for c in cols
                        if c != "id"
                    )
                    vals = [processed[c] for c in cols]

                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            INSERT INTO {full} ({col_list})
                            VALUES ({placeholders})
                            ON CONFLICT (id) DO UPDATE SET {set_clause}
                            """,
                            vals,
                        )
                    ok += 1
                except Exception as exc:
                    failed += 1
                    self.logger.debug("Upsert failed for row in %s: %s", table, exc)
                    conn.rollback()

            conn.commit()
        except Exception as exc:
            conn.rollback()
            self.logger.error("Batch upsert error for %s: %s", table, exc)
        finally:
            self.pool.putconn(conn)

        return ok, failed

    # ─────────────────────────────────────────────────────────────────
    # HQ API calls
    # ─────────────────────────────────────────────────────────────────

    def _post(self, path: str, body: Dict) -> Optional[Dict]:
        """POST to HQ with retry logic. Returns parsed JSON or None."""
        url = f"{self.hq_url}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._session.post(
                    url, json=body, timeout=REQUEST_TIMEOUT
                )
                if resp.status_code == 200:
                    return resp.json()
                self.logger.warning(
                    "POST %s → %d (attempt %d/%d)",
                    path, resp.status_code, attempt, MAX_RETRIES,
                )
            except requests.RequestException as exc:
                self.logger.warning(
                    "POST %s error (attempt %d/%d): %s",
                    path, attempt, MAX_RETRIES, exc,
                )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
        return None

    def compare_with_hq(
        self,
        local_counts: Dict[str, int],
        local_checksums: Optional[Dict[str, str]] = None,
        is_reinstall: bool = False,
    ) -> Optional[Dict]:
        """
        Call HQ's ``POST /api/sync/data_checker`` and return the response.
        """
        body: Dict[str, Any] = {
            "client_id": self.client_id,
            "table_counts": local_counts,
            "is_reinstall": is_reinstall,
        }
        if local_checksums:
            body["table_checksums"] = local_checksums

        return self._post("/api/sync/data_checker", body)

    def _fetch_server_dep_map(self) -> Dict[str, List[str]]:
        """
        Fetch the live FK dependency map from HQ's GET /api/sync/data_checker/status.

        Returns the ``table_dependencies`` dict from that response, or falls back
        to ``CLIENT_TABLE_DEPENDENCIES`` if the endpoint is unreachable or returns
        an older response that doesn't include the field.
        """
        try:
            url = f"{self.hq_url}/api/sync/data_checker/status"
            resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                dep_map = data.get("table_dependencies")
                if isinstance(dep_map, dict) and dep_map:
                    self.logger.debug(
                        "Fetched server dep map: %d entries", len(dep_map)
                    )
                    return dep_map
        except Exception as exc:
            self.logger.warning(
                "Could not fetch server dep map from /status: %s – using static fallback", exc
            )
        return CLIENT_TABLE_DEPENDENCIES

    def fetch_table_page(
        self,
        table: str,
        page: int = 0,
    ) -> Optional[Dict]:
        """
        Call HQ's ``POST /api/sync/data_checker/fetch_table`` for one page.
        """
        body = {
            "client_id": self.client_id,
            "table": table,
            "page": page,
            "batch_size": self.batch_size,
        }
        return self._post("/api/sync/data_checker/fetch_table", body)

    def sync_table_from_hq(self, table: str) -> Dict[str, Any]:
        """
        Fetch ALL pages for *table* from HQ and upsert them locally.

        Returns
        -------
        {
            "table":     str,
            "total_rows": int,
            "pages_fetched": int,
            "upserted": int,
            "failed":   int,
            "success":  bool,
        }
        """
        self.logger.info("⬇️  Syncing table from HQ: %s", table)

        total_upserted = total_failed = pages_fetched = 0
        total_rows = 0
        page = 0

        while True:
            result = self.fetch_table_page(table, page=page)

            if result is None:
                self.logger.error("  ❌ Failed to fetch page %d of %s", page, table)
                break

            rows = result.get("rows", [])
            total_rows = result.get("total_rows", 0)
            has_more = result.get("has_more", False)

            if rows:
                ok, fail = self._upsert_rows(table, rows)
                total_upserted += ok
                total_failed += fail

            pages_fetched += 1
            self.logger.debug(
                "  Page %d/%d – rows: %d, upserted: %d, failed: %d",
                page + 1,
                result.get("total_pages", 1),
                len(rows),
                ok if rows else 0,
                fail if rows else 0,
            )

            if not has_more:
                break

            page += 1

        success = total_failed == 0 and pages_fetched > 0
        self.logger.info(
            "  %s %s: %d/%d rows upserted (%d pages)",
            "✅" if success else "⚠️ ",
            table,
            total_upserted,
            total_rows,
            pages_fetched,
        )

        return {
            "table": table,
            "total_rows": total_rows,
            "pages_fetched": pages_fetched,
            "upserted": total_upserted,
            "failed": total_failed,
            "success": success,
        }

    # ─────────────────────────────────────────────────────────────────
    # Reverse-push: local → HQ
    # ─────────────────────────────────────────────────────────────────

    def _read_all_rows(self, table: str) -> List[Dict]:
        """
        Read every row from the local *table* and return them as plain
        dicts, serialising non-JSON-safe types (datetime, Decimal, bytes,
        UUID …) to strings so the event payload can be sent over HTTP.
        """
        schema, tbl = _split_table(table)
        full = _quoted(schema, tbl)
        conn = self.pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(f"SELECT * FROM {full} ORDER BY id")
                raw_rows = cur.fetchall()
        except Exception as exc:
            self.logger.error("Could not read rows from %s: %s", table, exc)
            return []
        finally:
            self.pool.putconn(conn)

        # Serialise to plain dicts (mirrors what the audit-log upload sends)
        import decimal, uuid as _uuid_mod
        serialised: List[Dict] = []
        for row in raw_rows:
            d: Dict[str, Any] = {}
            for k, v in row.items():
                if v is None:
                    d[k] = None
                elif isinstance(v, bytes):
                    d[k] = "__b64__:" + base64.b64encode(v).decode()
                elif isinstance(v, (datetime,)):
                    d[k] = v.isoformat()
                elif isinstance(v, decimal.Decimal):
                    d[k] = float(v)
                elif isinstance(v, _uuid_mod.UUID):
                    d[k] = str(v)
                elif isinstance(v, (dict, list)):
                    d[k] = v
                else:
                    d[k] = v
            serialised.append(d)
        return serialised

    def push_table_to_hq(
        self,
        table: str,
        client_id: str,
        machine_id: str,
        upload_url: str,
        http_headers: Dict[str, str],
    ) -> Dict[str, Any]:
        """
        Push every local row in *table* to HQ's ``/api/sync/upload``
        endpoint, using the same upsert-event format as the normal upload
        loop.

        Parameters
        ----------
        table:
            Fully-qualified table name, e.g. ``"public.users_customuser"``.
        client_id / machine_id:
            Passed through in the upload payload.
        upload_url:
            Full URL of the upload endpoint, e.g.
            ``"https://hq.example.com/api/sync/upload"``.
        http_headers:
            Headers dict (must include ``Authorization`` / ``X-API-Key``).

        Returns
        -------
        {
            "table": str,
            "total_rows": int,
            "uploaded": int,
            "batches": int,
            "failed_batches": int,
            "success": bool,
        }
        """
        self.logger.info("⬆️  Pushing table to HQ: %s", table)
        rows = self._read_all_rows(table)
        total_rows = len(rows)

        if total_rows == 0:
            self.logger.info("   %s is empty – nothing to push", table)
            return {
                "table": table,
                "total_rows": 0,
                "uploaded": 0,
                "batches": 0,
                "failed_batches": 0,
                "success": True,
            }

        uploaded = batches = failed_batches = 0

        for offset in range(0, total_rows, self.batch_size):
            chunk = rows[offset : offset + self.batch_size]

            events = [
                {
                    "event_id": f"push-{table}-{row['id']}-bootstrap",
                    "table": table,
                    "operation": "u",           # upsert / update
                    "row_id": row["id"],
                    "data": row,
                    "created_at": row.get("updated_at") or row.get("created_at") or "1970-01-01T00:00:00+00:00",
                    "updated_at": row.get("updated_at") or "1970-01-01T00:00:00+00:00",
                }
                for row in chunk
            ]

            payload = {
                "events": events,
                "client_id": client_id,
                "machine_id": machine_id,
            }

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    resp = self._session.post(
                        upload_url,
                        json=payload,
                        headers=http_headers,
                        timeout=REQUEST_TIMEOUT,
                    )
                    if resp.status_code == 200:
                        uploaded += len(chunk)
                        batches += 1
                        self.logger.debug(
                            "   Batch %d/%d uploaded (%d rows)",
                            batches,
                            -(-total_rows // self.batch_size),
                            len(chunk),
                        )
                        break
                    else:
                        self.logger.warning(
                            "   Batch upload → %d (attempt %d/%d): %s",
                            resp.status_code, attempt, MAX_RETRIES, resp.text[:200],
                        )
                except requests.RequestException as exc:
                    self.logger.warning(
                        "   Batch upload error (attempt %d/%d): %s",
                        attempt, MAX_RETRIES, exc,
                    )
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
            else:
                failed_batches += 1
                self.logger.error("   ❌ Batch at offset %d failed after %d attempts", offset, MAX_RETRIES)

        success = failed_batches == 0
        self.logger.info(
            "  %s %s: %d/%d rows pushed (%d batches, %d failed)",
            "✅" if success else "⚠️ ",
            table,
            uploaded,
            total_rows,
            batches,
            failed_batches,
        )
        return {
            "table": table,
            "total_rows": total_rows,
            "uploaded": uploaded,
            "batches": batches,
            "failed_batches": failed_batches,
            "success": success,
        }

    def push_all_to_hq(
        self,
        client_id: str,
        machine_id: str,
        upload_url: str,
        http_headers: Dict[str, str],
        dep_map: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """
        Push **all** allowed tables to HQ in dependency order.

        This is the symmetric counterpart of ``check_and_sync()``'s
        download path: where that calls ``sync_table_from_hq`` for each
        out-of-sync table, this calls ``push_table_to_hq`` for every
        table — right now, without waiting for the normal upload-loop
        polling cycle.

        Call this whenever ``check_and_sync()`` returns
        ``{"client_leads": True}`` (i.e. HQ was wiped and the client
        holds the authoritative data).

        Parameters
        ----------
        dep_map:
            Optional FK dependency map for ordering.  When omitted the
            static ``CLIENT_TABLE_DEPENDENCIES`` fallback is used.

        Returns
        -------
        {
            "pushed_tables":  [{table, total_rows, uploaded, …}, …],
            "failed_tables":  [table, …],
            "total_rows":     int,
            "total_uploaded": int,
            "total_duration_s": float,
            "success":        bool,
        }
        """
        start = time.monotonic()

        self.logger.info("=" * 70)
        self.logger.info("⬆️  PUSH ALL TO HQ – client leads, pushing local data up")
        self.logger.info("   Tables: %d", len(self.allowed_tables))
        self.logger.info("=" * 70)

        # Sort parents before children so FK constraints are satisfied on HQ
        try:
            ordered = _sort_tables_by_dependency(self.allowed_tables, dep_map=dep_map)
        except ValueError as exc:
            self.logger.warning("Dependency sort warning: %s – using unsorted list", exc)
            ordered = list(self.allowed_tables)

        pushed: List[Dict] = []
        failed: List[str] = []

        for i, table in enumerate(ordered, start=1):
            self.logger.info("[%d/%d] %s", i, len(ordered), table)
            try:
                result = self.push_table_to_hq(
                    table, client_id, machine_id, upload_url, http_headers
                )
                if result["success"]:
                    pushed.append(result)
                else:
                    failed.append(table)
            except Exception as exc:
                self.logger.error("  ❌ Exception pushing %s: %s", table, exc)
                failed.append(table)

        duration = time.monotonic() - start
        total_rows = sum(r["total_rows"] for r in pushed)
        total_uploaded = sum(r["uploaded"] for r in pushed)
        overall_success = len(failed) == 0

        self.logger.info("=" * 70)
        self.logger.info(
            "%s Push-all complete in %.1fs | tables: %d ok / %d failed | rows: %d/%d",
            "✅" if overall_success else "⚠️ ",
            duration,
            len(pushed),
            len(failed),
            total_uploaded,
            total_rows,
        )
        self.logger.info("=" * 70)

        return {
            "pushed_tables": pushed,
            "failed_tables": failed,
            "total_rows": total_rows,
            "total_uploaded": total_uploaded,
            "total_duration_s": duration,
            "success": overall_success,
        }

    # ─────────────────────────────────────────────────────────────────
    # Main entry point
    # ─────────────────────────────────────────────────────────────────

    def check_and_sync(
        self,
        send_checksums: bool = False,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Full bootstrap workflow:

        1. Count local rows.
        2. Compare with HQ.
        3. For each out-of-sync table, fetch all pages from HQ and upsert.

        Parameters
        ----------
        send_checksums:
            If True, also compute and send MD5 checksums per table.
            Slower but catches row-level differences when counts match.
        force:
            If True, sync all allowed tables regardless of comparison
            result (full wipe and replace).

        Returns
        -------
        {
            "already_in_sync":  [<table>, ...],
            "synced_tables":    [{table, upserted, failed, ...}, ...],
            "failed_tables":    [<table>, ...],
            "skipped_tables":   [<table>, ...],
            "total_duration_s": float,
            "success":          bool,
        }
        """
        start = time.monotonic()

        self.logger.info("=" * 70)
        self.logger.info("🔍 DATA CHECKER – Starting integrity check")
        self.logger.info("   Client: %s", self.client_id)
        self.logger.info("   Tables to check: %d", len(self.allowed_tables))
        if force:
            self.logger.info("   Mode: FORCE (syncing all tables)")
        self.logger.info("=" * 70)

        # ── Step 1: Count local rows ──────────────────────────────────
        local_counts = self.compute_local_counts()

        total_local = sum(c for c in local_counts.values() if c >= 0)
        self.logger.info("   Local total rows: %d", total_local)

        is_reinstall = total_local == 0

        # ── Step 2: Compare with HQ ───────────────────────────────────
        local_checksums = self.compute_local_checksums() if send_checksums else None

        if force:
            # Pretend everything is out of sync; still respect parent→child order.
            # Ask the server for its live dependency map so we don't rely on the
            # static CLIENT_TABLE_DEPENDENCIES fallback.
            #
            # ── Safety guard ──────────────────────────────────────────────────
            # Before force-pulling, check whether HQ actually has data.
            # If HQ is empty (e.g. it was wiped) we must NOT pull — doing so
            # would leave the client with zero rows.  Instead signal the caller
            # to push the local DB up to HQ.
            server_dep_map = self._fetch_server_dep_map()
            try:
                url = f"{self.hq_url}/api/sync/data_checker/status"
                _status_resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
                if _status_resp.status_code == 200:
                    _status = _status_resp.json()
                    _hq_counts = _status.get("table_counts", {})
                    _hq_grand_total = sum(v for v in _hq_counts.values() if v >= 0)
                    _local_grand_total = sum(c for c in local_counts.values() if c >= 0)
                    if _hq_grand_total == 0 and _local_grand_total > 0:
                        self.logger.warning("=" * 70)
                        self.logger.warning(
                            "⚠️  FORCE mode aborted: HQ reports 0 rows total "
                            "but local DB has %d rows.",
                            _local_grand_total,
                        )
                        self.logger.warning(
                            "   HQ appears to have been wiped. "
                            "Returning client_leads so upload loop pushes data up."
                        )
                        self.logger.warning("=" * 70)
                        return {
                            "already_in_sync": [],
                            "synced_tables": [],
                            "failed_tables": [],
                            "skipped_tables": self.allowed_tables,
                            "total_duration_s": time.monotonic() - start,
                            "success": False,
                            "client_leads": True,
                            "client_total_rows": _local_grand_total,
                            "hq_total_rows": 0,
                        }
            except Exception as _status_err:
                self.logger.warning(
                    "Could not verify HQ row totals before force sync: %s — proceeding",
                    _status_err,
                )

            tables_to_sync = _sort_tables_by_dependency(
                self.allowed_tables, dep_map=server_dep_map
            )
            self.logger.info(
                "   Force mode: dependency-sorted %d tables (%s dep map)",
                len(tables_to_sync),
                "server" if server_dep_map else "static fallback",
            )
            already_in_sync: List[str] = []
        else:
            compare_result = self.compare_with_hq(
                local_counts,
                local_checksums,
                is_reinstall=is_reinstall,
            )

            if compare_result is None:
                self.logger.error("❌ Could not reach HQ for data check – aborting")
                return {
                    "already_in_sync": [],
                    "synced_tables": [],
                    "failed_tables": [],
                    "skipped_tables": self.allowed_tables,
                    "total_duration_s": time.monotonic() - start,
                    "success": False,
                    "error": "HQ unreachable",
                }

            already_in_sync = compare_result.get("in_sync", [])
            out_of_sync = compare_result.get("out_of_sync", [])
            missing = compare_result.get("missing_on_client", [])

            # ── Direction guard ───────────────────────────────────────
            # If HQ has FEWER total rows than the client across all
            # out-of-sync tables it almost certainly means HQ was wiped
            # (or is a fresh blank install).  Pulling from HQ in that
            # state would destroy local data.  Instead we return a
            # special "client_leads" result so the caller (sync_agent)
            # can trigger an upload instead of a download.
            hq_total_oos   = compare_result.get("hq_total_rows", None)
            cli_total_oos  = compare_result.get("client_total_rows", None)

            # Fall back to counting from the entries if the server is older
            # and doesn't include the summary fields.
            if hq_total_oos is None:
                hq_total_oos  = sum(e.get("hq_count", 0)  for e in out_of_sync if e.get("hq_count", 0)  >= 0)
                cli_total_oos = sum(e.get("client_count", 0) for e in out_of_sync if e.get("client_count", 0) >= 0)

            # Only engage the guard when there IS a count discrepancy and
            # the client clearly leads (HQ has strictly fewer rows overall).
            if out_of_sync and hq_total_oos < cli_total_oos:
                self.logger.warning("=" * 70)
                self.logger.warning(
                    "⚠️  DATA CHECKER – CLIENT LEADS HQ: "
                    "client has %d rows vs HQ %d in out-of-sync tables.",
                    cli_total_oos, hq_total_oos,
                )
                self.logger.warning(
                    "   HQ may have been wiped or is a blank install."
                )
                self.logger.warning(
                    "   Refusing to pull from HQ — returning client_leads "
                    "so the upload loop will push local data up instead."
                )
                self.logger.warning("=" * 70)
                return {
                    "already_in_sync": already_in_sync,
                    "synced_tables": [],
                    "failed_tables": [],
                    "skipped_tables": [e["table"] for e in out_of_sync],
                    "total_duration_s": time.monotonic() - start,
                    "success": False,
                    "client_leads": True,          # caller must push, not pull
                    "client_total_rows": cli_total_oos,
                    "hq_total_rows": hq_total_oos,
                }

            # Prefer the server-provided sync_order (already dependency-sorted).
            # Fall back to a local topological sort for older server versions.
            server_sync_order: Optional[List[str]] = compare_result.get("sync_order")

            if server_sync_order:
                tables_to_sync = server_sync_order
                self.logger.info("   Using server-provided sync_order (%d tables)", len(tables_to_sync))
            else:
                raw_tables = [e["table"] for e in out_of_sync] + missing
                try:
                    tables_to_sync = _sort_tables_by_dependency(raw_tables)
                    self.logger.info(
                        "   Dependency-sorted %d tables locally (server sync_order absent)",
                        len(tables_to_sync),
                    )
                except ValueError as exc:
                    self.logger.warning("   Dependency sort warning: %s – using unsorted list", exc)
                    tables_to_sync = raw_tables

            self.logger.info(
                "   HQ comparison: %d in-sync, %d out-of-sync, %d missing",
                len(already_in_sync),
                len(out_of_sync),
                len(missing),
            )

        if not tables_to_sync:
            self.logger.info("✅ All tables in sync – no bootstrap needed")
            return {
                "already_in_sync": already_in_sync,
                "synced_tables": [],
                "failed_tables": [],
                "skipped_tables": [],
                "total_duration_s": time.monotonic() - start,
                "success": True,
            }

        self.logger.info("=" * 70)
        self.logger.info("⬇️  Bootstrapping %d table(s) from HQ …", len(tables_to_sync))
        self.logger.info("=" * 70)

        # ── Step 3: Sync each out-of-sync table ───────────────────────
        synced: List[Dict] = []
        failed: List[str] = []

        for i, table in enumerate(tables_to_sync, start=1):
            self.logger.info(
                "[%d/%d] %s", i, len(tables_to_sync), table
            )
            try:
                result = self.sync_table_from_hq(table)
                if result["success"] or result["upserted"] > 0:
                    synced.append(result)
                else:
                    failed.append(table)
            except Exception as exc:
                self.logger.error("  ❌ Exception syncing %s: %s", table, exc)
                failed.append(table)

        duration = time.monotonic() - start
        overall_success = len(failed) == 0

        self.logger.info("=" * 70)
        self.logger.info(
            "🎉 Data check complete in %.1fs | synced: %d | failed: %d",
            duration,
            len(synced),
            len(failed),
        )
        self.logger.info("=" * 70)

        return {
            "already_in_sync": already_in_sync,
            "synced_tables": synced,
            "failed_tables": failed,
            "skipped_tables": [],
            "total_duration_s": duration,
            "success": overall_success,
        }

    def is_local_db_empty(self) -> bool:
        """
        Quick check: returns True if the very first allowed table has 0 rows.
        Used by sync_agent to decide whether to call ``check_and_sync()``.
        """
        if not self.allowed_tables:
            return False
        table = self.allowed_tables[0]
        schema, tbl = _split_table(table)
        full = _quoted(schema, tbl)
        conn = self.pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {full} LIMIT 1")
                count = cur.fetchone()[0]
            return count == 0
        except Exception as exc:
            self.logger.warning("Could not check if DB is empty: %s", exc)
            return False
        finally:
            self.pool.putconn(conn)
