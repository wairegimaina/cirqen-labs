#!/usr/bin/env python3
"""
hq_sessions.py
Dumps full detail of all sessions directly from the HQ database.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from tabulate import tabulate
from datetime import datetime, timezone

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _creds import HQ_DB, require  # loaded from .env / config.json — no secrets here

require("hq")


def age(dt):
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    mins = rem // 60
    if days > 0:
        return f"{days}d {hours}h ago"
    elif hours > 0:
        return f"{hours}h {mins}m ago"
    else:
        return f"{mins}m ago"


def main():
    print("\n=== HQ Sessions — Full Detail ===\n")

    conn = psycopg2.connect(**HQ_DB)
    conn.autocommit = True

    with conn.cursor(cursor_factory=RealDictCursor) as cur:

        # ── All sessions ──────────────────────────────────────────────────────
        cur.execute("""
            SELECT
                s.id::text,
                s.status,
                s.certificate_number,
                s.overall_pass,
                s.device_serial,
                s.device_model,
                s.workshop_name,
                s."Department_name",
                s.created_at,
                s.updated_at,
                s.approved_at,
                s.needs_sync,
                u_perf.username  AS performed_by,
                u_appr.username  AS approved_by,
                pc.sync_status   AS pc_sync_status,
                pc.retry_count   AS pc_retry_count,
                pc.last_attempt  AS pc_last_attempt,
                pc.processed_at  AS pc_processed_at,
                pc.error_message AS pc_error
            FROM   "CalSoft_calibrationsession" s
            LEFT JOIN accounts_customuser  u_perf ON u_perf.id = s.performed_by_id
            LEFT JOIN accounts_customuser  u_appr ON u_appr.id = s.approved_by_id
            LEFT JOIN pending_certificates pc     ON pc.session_id = s.id
            ORDER  BY s.created_at DESC
        """)
        sessions = cur.fetchall()

        print(f"Total sessions on HQ: {len(sessions)}\n")

        for s in sessions:
            print("─" * 70)
            print(f"  ID              : {s['id']}")
            print(f"  Status          : {s['status']}")
            print(f"  Certificate No  : {s['certificate_number'] or '⚠  NONE'}")
            print(f"  Overall Pass    : {s['overall_pass']}")
            print(f"  Device Serial   : {s['device_serial'] or '—'}")
            print(f"  Device Model    : {s['device_model'] or '—'}")
            print(f"  Workshop        : {s['workshop_name'] or '—'}")
            print(f"  Department      : {s['Department_name'] or '—'}")
            print(f"  Performed By    : {s['performed_by'] or '—'}")
            print(f"  Approved By     : {s['approved_by'] or '—'}")
            print(
                f"  Approved At     : {s['approved_at']} ({age(s['approved_at'])})"
                if s["approved_at"]
                else "  Approved At     : —"
            )
            print(f"  Created At      : {s['created_at']} ({age(s['created_at'])})")
            print(f"  Updated At      : {s['updated_at']} ({age(s['updated_at'])})")
            print(f"  needs_sync      : {s['needs_sync']}")
            print(f"  ── PendingCertificate ──")
            if s["pc_sync_status"]:
                print(f"     sync_status   : {s['pc_sync_status']}")
                print(f"     retry_count   : {s['pc_retry_count']}")
                print(
                    f"     last_attempt  : {s['pc_last_attempt']} ({age(s['pc_last_attempt'])})"
                    if s["pc_last_attempt"]
                    else "     last_attempt  : —"
                )
                print(
                    f"     processed_at  : {s['pc_processed_at']} ({age(s['pc_processed_at'])})"
                    if s["pc_processed_at"]
                    else "     processed_at  : —"
                )
                print(f"     error         : {s['pc_error'] or '—'}")
            else:
                print(f"     ⚠  No PendingCertificate row linked to this session")
        print("─" * 70)

        # ── PendingCertificate table dump ─────────────────────────────────────
        print("\n\n=== pending_certificates table (HQ) — all rows ===\n")
        cur.execute("""
            SELECT
                pc.id::text,
                pc.session_id::text,
                pc.machine_id,
                pc.sync_status,
                pc.retry_count,
                pc.last_attempt,
                pc.processed_at,
                pc.error_message,
                pc.created_at,
                pc.updated_at
            FROM pending_certificates pc
            ORDER BY pc.created_at DESC
        """)
        pcs = cur.fetchall()

        if not pcs:
            print("  No rows in pending_certificates.")
        else:
            rows = []
            for pc in pcs:
                rows.append(
                    [
                        pc["id"][:8] + "…",
                        pc["session_id"][:8] + "…",
                        pc["sync_status"],
                        pc["retry_count"],
                        age(pc["last_attempt"]),
                        age(pc["processed_at"]),
                        (pc["error_message"] or "—")[:60],
                        age(pc["created_at"]),
                    ]
                )
            print(
                tabulate(
                    rows,
                    headers=[
                        "PC ID",
                        "Session ID",
                        "sync_status",
                        "Retries",
                        "Last Attempt",
                        "Processed At",
                        "Error",
                        "Created",
                    ],
                    tablefmt="github",
                )
            )

    conn.close()


if __name__ == "__main__":
    main()
