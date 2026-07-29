#!/usr/bin/env python3
"""
fix_cert_conflicts.py
─────────────────────
Dynamically detects and resolves certificate_number conflicts between Local and HQ DBs.

What this script does:
  Step 1 — Dynamically query HQ and LOCAL to find all cross-DB conflicts:
            - Duplicate certificate_number assigned to different UUIDs across DBs.
  Step 2 — Determine next available certificate numbers (e.g., BNH-0006, BNH-0007, etc.)
  Step 3 — Temporarily clear certificate_number from local orphan rows to release constraints.
  Step 4 — Restore authoritative HQ certificate numbers onto matching local sessions.
  Step 5 — Assign newly allocated, non-conflicting certificate numbers to local orphans.
  Step 6 — Upload updated rows to HQ via the sync API.
  Step 7 — Print final local verification state.

Run on the sync agent machine:
    python3 fix_cert_conflicts.py

Set DRY_RUN = True to preview changes without committing them.
"""

import sys
import re
import json
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone
import os

# ── Config ────────────────────────────────────────────────────────────────────

DRY_RUN = False  # Set to True to preview actions without committing changes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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

# Sync API Configuration
SYNC_API_URL = "http://127.0.0.1:8000/api/sync"  # Adjust if using remote HQ endpoint
SYNC_API_KEY = ""  # Set if API authentication is required
CLIENT_ID = "fix-cert-conflicts-script"

SESSION_TABLE = 'public."CalSoft_calibrationsession"'

SEP = "=" * 80
SEP2 = "-" * 80

# ── Helpers ───────────────────────────────────────────────────────────────────


def now_utc():
    return datetime.now(timezone.utc)


def fetch_row(conn, row_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM {SESSION_TABLE} WHERE id = %s", (str(row_id),))
        return cur.fetchone()


def print_row(label, row):
    if row is None:
        print(f"  {label}: *** NOT FOUND ***")
        return
    print(f"  {label}:")
    important = [
        "id",
        "certificate_number",
        "session_status",
        "active_status",
        "pending_delete",
        "created_at",
        "updated_at",
    ]
    for k in important:
        if k in row:
            print(f"    {k}: {row[k]}")


def find_conflicts(hq_conn, local_conn):
    """
    Dynamically fetches map of certificate numbers from both databases and
    returns a list of tuples: (local_orphan_id, hq_authoritative_id, cert_number)
    """
    with hq_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT id::text AS id, certificate_number
            FROM {SESSION_TABLE}
            WHERE certificate_number IS NOT NULL AND certificate_number != ''
        """)
        hq_map = {r["certificate_number"]: r["id"] for r in cur.fetchall()}

    with local_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT id::text AS id, certificate_number
            FROM {SESSION_TABLE}
            WHERE certificate_number IS NOT NULL AND certificate_number != ''
        """)
        local_map = {r["certificate_number"]: r["id"] for r in cur.fetchall()}

    conflicts = []
    for cert_num, hq_id in hq_map.items():
        local_id = local_map.get(cert_num)
        if local_id and local_id != hq_id:
            conflicts.append((local_id, hq_id, cert_num))

    return conflicts


def get_max_cert_number(conn):
    """Return the highest BNH-NNNN integer sequence found in the table."""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT certificate_number
            FROM {SESSION_TABLE}
            WHERE certificate_number ~ '^BNH-[0-9]+$'
            ORDER BY CAST(SPLIT_PART(certificate_number, '-', 2) AS INT) DESC
            LIMIT 1
        """)
        row = cur.fetchone()
    if row and row[0]:
        return int(row[0].split("-")[1])
    return 0


def next_cert_number(n):
    return f"BNH-{n:04d}"


def cert_number_exists(conn, cert_num):
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM {SESSION_TABLE} WHERE certificate_number = %s",
            (cert_num,),
        )
        return cur.fetchone() is not None

from decimal import Decimal
from uuid import UUID


def row_to_sync_event(row, operation="u", client_id=CLIENT_ID):
    """Convert a database dict row into a JSON-safe sync event payload."""
    data = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            data[k] = v.isoformat()
        elif isinstance(v, (Decimal, UUID)):
            data[k] = str(v)
        elif v is None:
            data[k] = None
        else:
            data[k] = str(v) if k == "id" else v

    return {
        "event_id": f"fix-{row['id']}-{now_utc().strftime('%Y%m%d%H%M%S%f')}",
        "table": "public.CalSoft_calibrationsession",
        "row_id": str(row["id"]),
        "operation": operation,
        "data": data,
        "created_at": data.get("updated_at") or now_utc().isoformat(),
        "source": "fix_script",
        "client_id": client_id,
        "machine_id": "fix-script",
    }


def upload_events(events):
    """Batch upload payload via sync API."""
    url = f"{SYNC_API_URL}/upload"
    headers = {"Content-Type": "application/json"}
    if SYNC_API_KEY:
        headers["X-Api-Key"] = SYNC_API_KEY

    payload = {
        "events": events,
        "client_id": CLIENT_ID,
    }

    r = requests.post(url, json=payload, headers=headers, timeout=30)
    return r.status_code, r.text


# ── Main Script ───────────────────────────────────────────────────────────────


def main():
    print(SEP)
    print("  AUTOMATIC CERTIFICATE CONFLICT FIX SCRIPT")
    print(f"  DRY RUN: {DRY_RUN}")
    print(SEP)

    # ── Connect ───────────────────────────────────────────────────────────────
    print("\n🔌 Connecting to databases …")
    try:
        hq = psycopg2.connect(HQ_DSN, connect_timeout=15)
        local = psycopg2.connect(**LOCAL_CFG, connect_timeout=10)
        hq.autocommit = False
        local.autocommit = False
        print("   ✅ Both DBs connected")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    # ── Step 1: Query Conflicts Dynamically ───────────────────────────────────
    print()
    print(SEP)
    print("  STEP 1 — DYNAMICALLY DETECT CONFLICTS")
    print(SEP)

    conflicts = find_conflicts(hq, local)

    if not conflicts:
        print("   ✅ No cross-DB certificate_number conflicts detected!")
        hq.close()
        local.close()
        sys.exit(0)

    print(f"   Found {len(conflicts)} conflict(s):")
    for orphan_id, hq_id, cert_num in conflicts:
        print(f"\n  Conflict: {cert_num}")
        print(SEP2)
        print_row(f"LOCAL orphan  ({orphan_id[:8]}…)", fetch_row(local, orphan_id))
        print_row(f"LOCAL hq-row  ({hq_id[:8]}…)", fetch_row(local, hq_id))
        print_row(f"HQ    hq-row  ({hq_id[:8]}…)", fetch_row(hq, hq_id))

    # ── Step 2: Allocate New Certificate Numbers ──────────────────────────────
    print()
    print(SEP)
    print("  STEP 2 — ALLOCATE NEW CERTIFICATE NUMBERS")
    print(SEP)

    max_hq = get_max_cert_number(hq)
    max_local = get_max_cert_number(local)
    next_num = max(max_hq, max_local) + 1

    print(f"   Max cert number on HQ    : BNH-{max_hq:04d}")
    print(f"   Max cert number on LOCAL : BNH-{max_local:04d}")
    print(f"   Starting new sequence at : BNH-{next_num:04d}")

    orphan_new_certs = {}
    for orphan_id, hq_id, cert_num in conflicts:
        if fetch_row(local, orphan_id) is None:
            print(f"   Orphan {orphan_id[:8]}… not found locally — skipping")
            continue

        candidate = next_cert_number(next_num)
        while cert_number_exists(hq, candidate) or cert_number_exists(local, candidate):
            next_num += 1
            candidate = next_cert_number(next_num)

        orphan_new_certs[orphan_id] = candidate
        print(f"   Orphan {orphan_id[:8]}… will receive new cert → {candidate}")
        next_num += 1

    # ── Steps 3 & 4: Execute Local DB Fixes ───────────────────────────────────
    print()
    print(SEP)
    print("  STEP 3 & 4 — UPDATE LOCAL DATABASE RECORDS")
    print(SEP)

    events_to_upload = []

    try:
        with local.cursor(cursor_factory=RealDictCursor) as cur:
            for orphan_id, hq_id, cert_num in conflicts:
                orphan_row = fetch_row(local, orphan_id)
                hq_local_row = fetch_row(local, hq_id)
                new_cert = orphan_new_certs.get(orphan_id)

                # Step 3A: Clear certificate_number on orphan to break UNIQUE constraints
                if orphan_row:
                    print(
                        f"   [1/3] Clearing certificate_number on orphan {orphan_id[:8]}… "
                        f"(was {orphan_row.get('certificate_number')!r})"
                    )
                    if not DRY_RUN:
                        cur.execute(
                            f"""UPDATE {SESSION_TABLE}
                               SET certificate_number = NULL, updated_at = %s
                               WHERE id = %s""",
                            (now_utc(), orphan_id),
                        )

                # Step 3B: Restore correct certificate_number on matching HQ session locally
                if hq_local_row is None:
                    print(
                        f"   WARN  HQ row {hq_id[:8]}… not present in Local DB — skipping restore"
                    )
                else:
                    current_cert = hq_local_row.get("certificate_number")
                    if current_cert == cert_num:
                        print(f"   [2/3] Local HQ row {hq_id[:8]}… already holds {cert_num}")
                    else:
                        print(
                            f"   [2/3] Restoring {cert_num} → Local HQ row {hq_id[:8]}… "
                            f"(was {current_cert!r})"
                        )
                        if not DRY_RUN:
                            cur.execute(
                                f"""UPDATE {SESSION_TABLE}
                                   SET certificate_number = %s, updated_at = %s
                                   WHERE id = %s""",
                                (cert_num, now_utc(), hq_id),
                            )

                    updated_hq = dict(hq_local_row)
                    updated_hq["certificate_number"] = cert_num
                    updated_hq["updated_at"] = now_utc()
                    events_to_upload.append(row_to_sync_event(updated_hq))

                # Step 3C: Re-assign orphan session with new certificate number
                if orphan_row and new_cert:
                    print(f"   [3/3] Assigning {new_cert} → Orphan row {orphan_id[:8]}…")
                    if not DRY_RUN:
                        cur.execute(
                            f"""UPDATE {SESSION_TABLE}
                               SET certificate_number = %s, updated_at = %s
                               WHERE id = %s""",
                            (new_cert, now_utc(), orphan_id),
                        )
                    updated_orphan = dict(orphan_row)
                    updated_orphan["certificate_number"] = new_cert
                    updated_orphan["updated_at"] = now_utc()
                    events_to_upload.append(row_to_sync_event(updated_orphan))

                print()

        if not DRY_RUN:
            local.commit()
            print("   ✅ LOCAL DB updates committed successfully.")
        else:
            local.rollback()
            print("   ℹ️ DRY RUN — Local changes rolled back.")

    except Exception as e:
        local.rollback()
        print(f"\n   ❌ Exception occurred updating local database: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # ── Step 5: Upload Sync Payload to HQ ─────────────────────────────────────
    print()
    print(SEP)
    print("  STEP 5 — UPLOAD UPDATED RECORDS TO HQ")
    print(SEP)

    if not events_to_upload:
        print("   ℹ️ No sync events to process.")
    elif DRY_RUN:
        print(f"   ℹ️ DRY RUN — would post {len(events_to_upload)} sync event(s):")
        for e in events_to_upload:
            print(f"      • {e['row_id'][:8]}… cert={e['data'].get('certificate_number')}")
    else:
        print(f"   📤 Uploading {len(events_to_upload)} sync event(s) to HQ …")
        try:
            status, body = upload_events(events_to_upload)
            if status == 200:
                print(f"   ✅ Upload successful (HTTP {status})")
                try:
                    resp = json.loads(body)
                    print(f"      processed={resp.get('processed')}, failed={resp.get('failed')}")
                except Exception:
                    pass
            else:
                print(f"   ⚠️ API returned HTTP {status}: {body[:300]}")
                print(
                    "   Note: Local DB changes are saved. Sync agent will sync changes on next pass."
                )
        except requests.exceptions.ConnectionError as ce:
            print(f"   ⚠️ Sync API unreachable ({ce}).")
            print("   Local DB is fixed; Sync Agent will push changes once connection restores.")

    # ── Step 6: Verify State ──────────────────────────────────────────────────
    print()
    print(SEP)
    print("  STEP 6 — FINAL LOCAL STATE VERIFICATION")
    print(SEP)

    all_ids = [oid for oid, _, _ in conflicts] + [hid for _, hid, _ in conflicts]
    for rid in all_ids:
        row = fetch_row(local, rid)
        if row:
            print(
                f"  {rid[:8]}…  cert={row.get('certificate_number')!r}  "
                f"status={row.get('session_status')!r}  updated={row.get('updated_at')}"
            )
        else:
            print(f"  {rid[:8]}…  NOT FOUND")

    print()
    print(SEP)
    print("  DONE — Run `python3 cert_checker.py` to confirm clean state.")
    print(SEP)
    print()

    hq.close()
    local.close()


if __name__ == "__main__":
    main()
