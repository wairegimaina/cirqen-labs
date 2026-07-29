#!/usr/bin/env python3
"""
cert_checker.py
───────────────
Compares CalSoft_calibrationsession and certificate data between
HQ (Render PostgreSQL) and the local database.

Checks:
  1. Row counts on both sides
  2. Sessions present in HQ but missing locally
  3. Sessions present locally but missing from HQ
  4. Duplicate certificate_number values within each DB
  5. Certificate_number conflicts — same number, different id (the BNH-0093 problem)
  6. Cert status mismatches (certificate_number, certificate_status, etc.)
"""

import sys
import psycopg2
from psycopg2.extras import RealDictCursor
from collections import defaultdict

# ── Connection configs (loaded from .env / config.json — no secrets here) ─────
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _creds import hq_dsn, LOCAL_DB, require

require("local", "hq")
HQ_DSN = hq_dsn()
LOCAL_CFG = dict(
    host=LOCAL_DB["host"],
    port=LOCAL_DB["port"],
    dbname=LOCAL_DB["database"],
    user=LOCAL_DB["user"],
    password=LOCAL_DB["password"],
)

SESSION_TABLE  = 'public."CalSoft_calibrationsession"'
REPORT_TABLE   = 'public."CalSoft_calibrationreport"'

# Columns we care about for the session table
SESSION_COLS = [
    "id",
    "certificate_number",
    "certificate_status",
    "session_status",
    "active_status",
    "pending_delete",
    "updated_at",
    "created_at",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

SEP  = "=" * 80
SEP2 = "-" * 80

def connect_hq():
    print("🔌 Connecting to HQ …")
    conn = psycopg2.connect(HQ_DSN, connect_timeout=15)
    conn.autocommit = True
    print("   ✅ HQ connected")
    return conn

def connect_local():
    print("🔌 Connecting to local DB …")
    conn = psycopg2.connect(**LOCAL_CFG, connect_timeout=10)
    conn.autocommit = True
    print("   ✅ Local connected")
    return conn

def fetch_all(conn, sql, params=None):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()

def col_exists(conn, schema, table, column):
    rows = fetch_all(conn, """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s AND column_name=%s
    """, (schema, table, column))
    return bool(rows)

def safe_cols(conn, schema, table, wanted):
    """Return only columns that actually exist in this DB."""
    rows = fetch_all(conn, """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s
    """, (schema, table))
    existing = {r["column_name"] for r in rows}
    return [c for c in wanted if c in existing]

def print_section(title):
    print()
    print(SEP)
    print(f"  {title}")
    print(SEP)

def print_rows(rows, limit=30):
    if not rows:
        print("   (none)")
        return
    for i, r in enumerate(rows):
        if i >= limit:
            print(f"   … and {len(rows) - limit} more")
            break
        parts = []
        for k, v in r.items():
            parts.append(f"{k}={repr(v)}")
        print("   • " + " | ".join(parts))

# ── Main checks ───────────────────────────────────────────────────────────────

def check_row_counts(hq, local):
    print_section("1. ROW COUNTS")

    for label, conn in [("HQ", hq), ("LOCAL", local)]:
        rows = fetch_all(conn, f"SELECT COUNT(*) AS n FROM {SESSION_TABLE}")
        print(f"   {label} CalSoft_calibrationsession : {rows[0]['n']:,} rows")

    # Also report table
    for label, conn in [("HQ", hq), ("LOCAL", local)]:
        try:
            rows = fetch_all(conn, f"SELECT COUNT(*) AS n FROM {REPORT_TABLE}")
            print(f"   {label} CalSoft_calibrationreport   : {rows[0]['n']:,} rows")
        except Exception as e:
            print(f"   {label} CalSoft_calibrationreport   : ⚠️  {e}")


def check_duplicate_cert_numbers(label, conn):
    print_section(f"2. DUPLICATE certificate_number WITHIN {label}")
    rows = fetch_all(conn, f"""
        SELECT certificate_number, COUNT(*) AS cnt,
               array_agg(id::text ORDER BY created_at) AS ids
        FROM {SESSION_TABLE}
        WHERE certificate_number IS NOT NULL
          AND certificate_number != ''
        GROUP BY certificate_number
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC, certificate_number
    """)
    if rows:
        print(f"   ⚠️  Found {len(rows)} certificate_number(s) with duplicates!")
        print_rows(rows)
    else:
        print(f"   ✅ No duplicates — all certificate_number values are unique in {label}")


def check_cross_db_conflicts(hq, local):
    print_section("3. CROSS-DB CONFLICTS — same certificate_number, different id")

    print("   Fetching HQ cert map …")
    hq_rows = fetch_all(hq, f"""
        SELECT id::text AS id, certificate_number
        FROM {SESSION_TABLE}
        WHERE certificate_number IS NOT NULL AND certificate_number != ''
    """)
    hq_map = {r["certificate_number"]: r["id"] for r in hq_rows}

    print("   Fetching local cert map …")
    local_rows = fetch_all(local, f"""
        SELECT id::text AS id, certificate_number
        FROM {SESSION_TABLE}
        WHERE certificate_number IS NOT NULL AND certificate_number != ''
    """)
    local_map = {r["certificate_number"]: r["id"] for r in local_rows}

    conflicts = []
    for cert_num, hq_id in hq_map.items():
        local_id = local_map.get(cert_num)
        if local_id and local_id != hq_id:
            conflicts.append({
                "certificate_number": cert_num,
                "hq_id":    hq_id,
                "local_id": local_id,
            })

    if conflicts:
        print(f"   ❌ Found {len(conflicts)} conflict(s) — same cert number, different row id!")
        print(f"   {'certificate_number':<20} {'HQ id':<38} {'LOCAL id':<38}")
        print("   " + SEP2)
        for c in conflicts:
            print(f"   {c['certificate_number']:<20} {c['hq_id']:<38} {c['local_id']:<38}")
    else:
        print("   ✅ No cross-DB certificate_number conflicts found")

    return conflicts


def check_missing_sessions(hq, local):
    print_section("4. SESSIONS MISSING ON ONE SIDE")

    hq_ids    = {r["id"] for r in fetch_all(hq,    f"SELECT id FROM {SESSION_TABLE}")}
    local_ids = {r["id"] for r in fetch_all(local, f"SELECT id FROM {SESSION_TABLE}")}

    only_hq    = hq_ids    - local_ids
    only_local = local_ids - hq_ids

    print(f"   Total HQ sessions   : {len(hq_ids):,}")
    print(f"   Total local sessions: {len(local_ids):,}")
    print()

    if only_hq:
        print(f"   ⚠️  {len(only_hq)} session(s) exist at HQ but NOT locally:")
        for sid in sorted(only_hq)[:30]:
            print(f"      • {sid}")
        if len(only_hq) > 30:
            print(f"      … and {len(only_hq)-30} more")
    else:
        print("   ✅ All HQ sessions are present locally")

    print()

    if only_local:
        print(f"   ⚠️  {len(only_local)} session(s) exist locally but NOT at HQ:")
        for sid in sorted(only_local)[:30]:
            print(f"      • {sid}")
        if len(only_local) > 30:
            print(f"      … and {len(only_local)-30} more")
    else:
        print("   ✅ All local sessions are present at HQ")

    return only_hq, only_local


def check_cert_status_mismatches(hq, local):
    print_section("5. certificate_number / certificate_status MISMATCHES (same id)")

    # Build maps keyed by id
    hq_cols    = safe_cols(hq,    "public", "CalSoft_calibrationsession",
                           ["id", "certificate_number", "certificate_status", "session_status"])
    local_cols = safe_cols(local, "public", "CalSoft_calibrationsession",
                           ["id", "certificate_number", "certificate_status", "session_status"])

    col_str_hq    = ", ".join(hq_cols)
    col_str_local = ", ".join(local_cols)

    hq_rows    = {r["id"]: dict(r) for r in fetch_all(hq,    f"SELECT {col_str_hq}    FROM {SESSION_TABLE}")}
    local_rows = {r["id"]: dict(r) for r in fetch_all(local, f"SELECT {col_str_local} FROM {SESSION_TABLE}")}

    mismatches = []
    common_ids = set(hq_rows) & set(local_rows)
    compare_cols = ["certificate_number", "certificate_status", "session_status"]

    for rid in common_ids:
        diffs = {}
        for col in compare_cols:
            hq_val    = hq_rows[rid].get(col)
            local_val = local_rows[rid].get(col)
            if hq_val != local_val:
                diffs[col] = {"hq": hq_val, "local": local_val}
        if diffs:
            mismatches.append({"id": rid, "diffs": diffs})

    if mismatches:
        print(f"   ⚠️  {len(mismatches)} session(s) have field mismatches between HQ and local:")
        for m in mismatches[:40]:
            print(f"   • id={m['id']}")
            for col, vals in m["diffs"].items():
                print(f"       {col}: HQ={repr(vals['hq'])}  LOCAL={repr(vals['local'])}")
        if len(mismatches) > 40:
            print(f"   … and {len(mismatches)-40} more")
    else:
        print("   ✅ No certificate_number / status mismatches on common rows")


def check_null_cert_numbers(hq, local):
    print_section("6. SESSIONS WITH NULL / EMPTY certificate_number (certs not yet generated)")

    for label, conn in [("HQ", hq), ("LOCAL", local)]:
        rows = fetch_all(conn, f"""
            SELECT COUNT(*) AS n FROM {SESSION_TABLE}
            WHERE certificate_number IS NULL OR certificate_number = ''
        """)
        print(f"   {label}: {rows[0]['n']:,} sessions without a certificate number")

        # Break down by session_status if column exists
        if col_exists(conn, "public", "CalSoft_calibrationsession", "session_status"):
            breakdown = fetch_all(conn, f"""
                SELECT session_status, COUNT(*) AS n
                FROM {SESSION_TABLE}
                WHERE certificate_number IS NULL OR certificate_number = ''
                GROUP BY session_status
                ORDER BY n DESC
            """)
            for b in breakdown:
                print(f"      └─ session_status={b['session_status']!r}: {b['n']}")


def check_pending_certs(hq, local):
    print_section("7. CERTIFICATE STATUS BREAKDOWN")

    for label, conn in [("HQ", hq), ("LOCAL", local)]:
        if not col_exists(conn, "public", "CalSoft_calibrationsession", "certificate_status"):
            print(f"   {label}: certificate_status column not found — skipping")
            continue
        rows = fetch_all(conn, f"""
            SELECT certificate_status, COUNT(*) AS n
            FROM {SESSION_TABLE}
            GROUP BY certificate_status
            ORDER BY n DESC
        """)
        print(f"   {label}:")
        for r in rows:
            print(f"      {r['certificate_status']!r:30} : {r['n']:,}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print(SEP)
    print("  CERTIFICATE & CALIBRATION SESSION CHECKER")
    print("  HQ  : Render PostgreSQL (cirqen_hq_db1)")
    print("  LOCAL: 127.0.0.1:2215 (cirqen1)")
    print(SEP)

    try:
        hq    = connect_hq()
        local = connect_local()
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        sys.exit(1)

    try:
        check_row_counts(hq, local)
        check_duplicate_cert_numbers("HQ",    hq)
        check_duplicate_cert_numbers("LOCAL", local)
        check_cross_db_conflicts(hq, local)
        check_missing_sessions(hq, local)
        check_cert_status_mismatches(hq, local)
        check_null_cert_numbers(hq, local)
        check_pending_certs(hq, local)
    finally:
        hq.close()
        local.close()

    print()
    print(SEP)
    print("  CHECK COMPLETE")
    print(SEP)
    print()


if __name__ == "__main__":
    main()
