#!/usr/bin/env python3
"""
fix_cert_conflicts.py
─────────────────────
Fixes the two certificate_number conflicts between local and HQ.

Situation:
  • HQ has BNH-0001 on id f69c61dc-…  (correct authoritative row)
  • LOCAL has BNH-0001 on id bf2cd7b4-…  (orphan — wrong id, cert assigned locally)

  • HQ has BNH-0093 on id bc1d074c-…  (correct authoritative row)
  • LOCAL has BNH-0093 on id b9c75740-…  (orphan — wrong id, cert assigned locally)

  Additionally the HQ rows (f69c61dc, bc1d074c) exist locally but with
  certificate_number = NULL — they were never given their cert number locally.

What this script does (all on LOCAL, then uploads to HQ):
  Step 1 — Inspect: print current state of all 4 rows
  Step 2 — Find next available cert numbers (129, 130, …) on HQ
  Step 3 — Fix the HQ rows locally:
              f69c61dc → certificate_number = BNH-0001  (already correct on HQ, just missing locally)
              bc1d074c → certificate_number = BNH-0093  (same)
  Step 4 — Re-number the orphan rows with the next available numbers:
              bf2cd7b4 → BNH-0129
              b9c75740 → BNH-0130   (or whatever next slots are free on BOTH sides)
  Step 5 — Upload all 4 corrected rows to HQ via the sync API
  Step 6 — Final verification

Run on the sync agent machine:
    python3 fix_cert_conflicts.py

Set DRY_RUN = True to preview without making any changes.
"""

import sys
import re
import json
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────

DRY_RUN = False  # Set True to preview only

HQ_DSN = (
    "postgresql://cirqen_hq_db1_user:cTAU3kJL3NNlUYA9rR07kh87FKHA6c24"
    "@dpg-d8fj2c59j78s738al2vg-a.ohio-postgres.render.com/cirqen_hq_db1"
)

LOCAL_CFG = dict(
    host="127.0.0.1",
    port=2215,
    dbname="cirqen1",
    user="cirqen1",
    password="Btwelvetech@2024",
)

# Sync API — adjust if your API URL is different
SYNC_API_URL = "http://127.0.0.1:8000/api/sync"  # or your HQ URL
SYNC_API_KEY = ""  # set if your server requires X-Api-Key
CLIENT_ID = "fix-cert-conflicts-script"

SESSION_TABLE = 'public."CalSoft_calibrationsession"'

# The two conflict pairs
# (local_orphan_id, hq_authoritative_id, cert_number_that_belongs_to_hq_row)
CONFLICTS = [
    ("bf2cd7b4-614d-4f25-a9b9-b741250c3089", "f69c61dc-7bab-41c0-bb93-34120c08fa48", "BNH-0001"),
    ("b9c75740-9871-4ba7-901d-9ea437ea0d41", "bc1d074c-87a4-4ef2-9356-43321c91c98d", "BNH-0093"),
]

SEP = "=" * 80
SEP2 = "-" * 80

# ── Helpers ───────────────────────────────────────────────────────────────────


def now_utc():
    return datetime.now(timezone.utc)


def fetch_row(conn, row_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM {SESSION_TABLE} WHERE id = %s", (row_id,))
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


def get_max_cert_number(conn):
    """Return the highest BNH-NNNN number currently in the table."""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT certificate_number
            FROM {SESSION_TABLE}
            WHERE certificate_number ~ '^BNH-[0-9]+$'
            ORDER BY CAST(SPLIT_PART(certificate_number, '-', 2) AS INT) DESC
            LIMIT 1
        """)
        row = cur.fetchone()
    if row:
        return int(row[0].split("-")[1])
    return 0


def next_cert_number(n):
    return f"BNH-{n:04d}"


def cert_number_exists(conn, cert_num):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT 1 FROM {SESSION_TABLE}
            WHERE certificate_number = %s
        """,
            (cert_num,),
        )
        return cur.fetchone() is not None


def row_to_sync_event(row, operation="u", client_id=CLIENT_ID):
    """Convert a DB row dict to a sync upload event."""
    data = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            data[k] = v.isoformat()
        elif v is None:
            data[k] = None
        else:
            data[k] = v

    return {
        "event_id": f"fix-{row['id']}-{now_utc().strftime('%Y%m%d%H%M%S')}",
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
    """Upload a batch of sync events to HQ via the sync API."""
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


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    print(SEP)
    print("  CERTIFICATE CONFLICT FIX SCRIPT")
    print(f"  DRY RUN: {DRY_RUN}")
    print(SEP)

    # ── Connect ───────────────────────────────────────────────────────────────
    print("\n🔌 Connecting …")
    try:
        hq = psycopg2.connect(HQ_DSN, connect_timeout=15)
        local = psycopg2.connect(**LOCAL_CFG, connect_timeout=10)
        hq.autocommit = False
        local.autocommit = False
        print("   ✅ Both DBs connected")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    # ── Step 1: Inspect current state ─────────────────────────────────────────
    print()
    print(SEP)
    print("  STEP 1 — CURRENT STATE")
    print(SEP)

    for orphan_id, hq_id, cert_num in CONFLICTS:
        print(f"\n  Conflict: {cert_num}")
        print(SEP2)
        print_row(f"LOCAL orphan  ({orphan_id[:8]}…)", fetch_row(local, orphan_id))
        print_row(f"LOCAL hq-row  ({hq_id[:8]}…)", fetch_row(local, hq_id))
        print_row(f"HQ    hq-row  ({hq_id[:8]}…)", fetch_row(hq, hq_id))

    # ── Step 2: Find next free cert numbers ───────────────────────────────────
    print()
    print(SEP)
    print("  STEP 2 — FIND NEXT AVAILABLE CERT NUMBERS")
    print(SEP)

    max_hq = get_max_cert_number(hq)
    max_local = get_max_cert_number(local)
    next_num = max(max_hq, max_local) + 1

    print(f"   Max cert number on HQ    : BNH-{max_hq:04d}")
    print(f"   Max cert number on LOCAL : BNH-{max_local:04d}")
    print(f"   Starting new numbers at  : BNH-{next_num:04d}")

    # Assign a new number to each orphan row
    orphan_new_certs = {}
    for orphan_id, hq_id, cert_num in CONFLICTS:
        # Skip if orphan row doesn't exist locally (already cleaned)
        if fetch_row(local, orphan_id) is None:
            print(f"   Orphan {orphan_id[:8]}… not found — skipping")
            continue

        candidate = next_cert_number(next_num)
        # Make sure it's free on both sides
        while cert_number_exists(hq, candidate) or cert_number_exists(local, candidate):
            next_num += 1
            candidate = next_cert_number(next_num)

        orphan_new_certs[orphan_id] = candidate
        print(f"   Orphan {orphan_id[:8]}… → will get {candidate}")
        next_num += 1

    # ── Step 3 & 4: Apply fixes locally ──────────────────────────────────────
    print()
    print(SEP)
    print("  STEP 3 — FIX HQ ROWS LOCALLY (restore their certificate_number)")
    print("  STEP 4 — RE-NUMBER ORPHAN ROWS")
    print(SEP)

    events_to_upload = []

    try:
        with local.cursor(cursor_factory=RealDictCursor) as cur:

            for orphan_id, hq_id, cert_num in CONFLICTS:

                orphan_row = fetch_row(local, orphan_id)
                hq_local_row = fetch_row(local, hq_id)
                new_cert = orphan_new_certs.get(orphan_id)

                # PASS A: clear orphan cert number first to free the unique constraint
                if orphan_row is None:
                    print(f"   INFO  Orphan {orphan_id[:8]}... not found locally - skipping")
                else:
                    print(
                        f"   [1/3] Clearing cert from orphan {orphan_id[:8]}... "
                        f"(was {orphan_row.get('certificate_number')!r})"
                    )
                    if not DRY_RUN:
                        cur.execute(
                            f"""UPDATE {SESSION_TABLE}
                               SET certificate_number = NULL, updated_at = %s
                               WHERE id = %s""",
                            (now_utc(), orphan_id),
                        )

                # PASS B: restore the correct cert number onto the HQ row
                if hq_local_row is None:
                    print(f"   WARN  HQ row {hq_id[:8]}... not found locally - skipping restore")
                else:
                    current_cert = hq_local_row.get("certificate_number")
                    if current_cert == cert_num:
                        print(f"   [2/3] {hq_id[:8]}... already has {cert_num} - no change")
                    else:
                        print(
                            f"   [2/3] Restoring {cert_num} -> local row {hq_id[:8]}... "
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

                # PASS C: assign new number to orphan
                if orphan_row is None:
                    pass
                elif not new_cert:
                    print(f"   WARN  No new cert allocated for {orphan_id[:8]}... - skipping")
                else:
                    print(f"   [3/3] Assigning {new_cert} -> orphan {orphan_id[:8]}...")
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
            print("\n   ✅ LOCAL DB committed")
        else:
            local.rollback()
            print("\n   ℹ️  DRY RUN — local changes rolled back")

    except Exception as e:
        local.rollback()
        print(f"\n   ❌ Error updating local DB: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # ── Step 5: Upload to HQ ──────────────────────────────────────────────────
    print()
    print(SEP)
    print("  STEP 5 — UPLOAD CORRECTED ROWS TO HQ")
    print(SEP)

    if not events_to_upload:
        print("   ℹ️  No events to upload")
    elif DRY_RUN:
        print(f"   ℹ️  DRY RUN — would upload {len(events_to_upload)} event(s):")
        for e in events_to_upload:
            print(f"      • {e['row_id'][:8]}… cert={e['data'].get('certificate_number')}")
    else:
        print(f"   📤 Uploading {len(events_to_upload)} event(s) to HQ …")
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
                print(f"   ⚠️  Upload returned HTTP {status}: {body[:300]}")
                print()
                print("   The local DB has been fixed. Re-run the sync agent")
                print("   or call the upload endpoint manually to push to HQ.")
        except requests.exceptions.ConnectionError as ce:
            print(f"   ⚠️  Could not reach sync API: {ce}")
            print()
            print("   LOCAL DB has been fixed. The sync agent will push")
            print("   the corrected rows to HQ on its next upload cycle.")

    # ── Step 6: Verify ────────────────────────────────────────────────────────
    print()
    print(SEP)
    print("  STEP 6 — FINAL LOCAL STATE")
    print(SEP)

    all_ids = [oid for oid, _, _ in CONFLICTS] + [hid for _, hid, _ in CONFLICTS]
    for rid in all_ids:
        row = fetch_row(local, rid)
        if row:
            print(
                f"\n  {rid[:8]}…  cert={row.get('certificate_number')!r}  "
                f"status={row.get('session_status')!r}  updated={row.get('updated_at')}"
            )
        else:
            print(f"\n  {rid[:8]}…  NOT FOUND")

    print()
    print(SEP)
    print("  DONE — run cert_checker.py to confirm clean state")
    print(SEP)
    print()

    hq.close()
    local.close()


if __name__ == "__main__":
    main()
