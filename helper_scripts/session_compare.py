#!/usr/bin/env python3
"""
session_compare.py
─────────────────────────────────────────────────────────────────────────────
Deep diagnostic for CalibrationSession rows across LOCAL and HQ databases.

Checks:
  • Session status breakdown (pending_review / approved / rejected /
                               approved_pending_certificate)
  • Certificate number presence vs absence
  • PendingCertificate table: sync_status, retry counts, last attempt
  • Sessions that exist on LOCAL but not on HQ (not yet synced)
  • Sessions that exist on HQ but not on LOCAL (orphaned on HQ)
  • Mismatch in status between LOCAL and HQ for the same session UUID

Usage:
    pip install psycopg2-binary tabulate
    python3 session_compare.py
─────────────────────────────────────────────────────────────────────────────
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from tabulate import tabulate
from datetime import datetime, timezone

# ── Connection configs ────────────────────────────────────────────────────────

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _creds import LOCAL_DB, HQ_DB, require  # loaded from .env / config.json — no secrets here

require("local", "hq")

# Status display labels
STATUS_LABELS = {
    "pending_review":               "⏳ Pending Review",
    "approved":                     "✅ Approved",
    "rejected":                     "❌ Rejected",
    "approved_pending_certificate": "🕐 Awaiting Certificate",
}

PENDING_CERT_LABELS = {
    "pending":    "⏳ Pending",
    "processing": "🔄 Processing",
    "completed":  "✅ Completed",
    "failed":     "❌ Failed",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def connect(cfg: dict, label: str):
    try:
        conn = psycopg2.connect(**cfg)
        conn.autocommit = True
        print(f"  ✔  Connected to {label}")
        return conn
    except Exception as e:
        print(f"  ✘  Could not connect to {label}: {e}")
        return None


def age(dt):
    """Return human-readable age from a datetime."""
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


def fetch_sessions(conn):
    """
    Returns dict keyed by session UUID with all relevant fields.
    """
    if not conn:
        return {}
    sql = """
        SELECT
            s.id::text                          AS id,
            s.status,
            s.certificate_number,
            s.overall_pass,
            s.device_serial,
            s.device_model,
            s.workshop_name,
            s."Department_name",
            s.approved_at,
            s.rejected_at,
            s.rejection_reason,
            s.created_at,
            s.updated_at,
            s.needs_sync,
            s.pending_delete,
            u_perf.username                     AS performed_by,
            u_appr.username                     AS approved_by,
            u_rej.username                      AS rejected_by,
            -- PendingCertificate join
            pc.sync_status                      AS pc_sync_status,
            pc.retry_count                      AS pc_retry_count,
            pc.last_attempt                     AS pc_last_attempt,
            pc.error_message                    AS pc_error_message,
            pc.processed_at                     AS pc_processed_at
        FROM   "CalSoft_calibrationsession"  s
        LEFT JOIN accounts_customuser   u_perf ON u_perf.id = s.performed_by_id
        LEFT JOIN accounts_customuser   u_appr ON u_appr.id = s.approved_by_id
        LEFT JOIN accounts_customuser   u_rej  ON u_rej.id  = s.rejected_by_id
        LEFT JOIN pending_certificates  pc     ON pc.session_id = s.id
        WHERE  s.pending_delete = false
        ORDER  BY s.created_at DESC
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            cur.execute(sql)
            return {row["id"]: dict(row) for row in cur.fetchall()}
        except Exception as e:
            print(f"  ⚠  Query failed: {e}")
            return {}


def section(title):
    width = 80
    print(f"\n{'═' * width}")
    print(f"  {title}")
    print(f"{'═' * width}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║        Cirqen  —  CalibrationSession Deep Diagnostic        ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    print("Connecting …")
    local_conn = connect(LOCAL_DB, "LOCAL  (127.0.0.1:2215)")
    hq_conn    = connect(HQ_DB,    "HQ     (render.com)")
    print()

    local_sessions = fetch_sessions(local_conn)
    hq_sessions    = fetch_sessions(hq_conn)

    local_ids = set(local_sessions.keys())
    hq_ids    = set(hq_sessions.keys())

    # ── 1. STATUS BREAKDOWN ───────────────────────────────────────────────────
    section("1 · SESSION STATUS BREAKDOWN")

    all_statuses = [
        "pending_review",
        "approved",
        "rejected",
        "approved_pending_certificate",
    ]

    breakdown_rows = []
    for st in all_statuses:
        local_n = sum(1 for s in local_sessions.values() if s["status"] == st)
        hq_n    = sum(1 for s in hq_sessions.values()    if s["status"] == st)
        label   = STATUS_LABELS.get(st, st)
        breakdown_rows.append([label, local_n, hq_n])

    breakdown_rows.append(["─" * 30, "─" * 6, "─" * 6])
    breakdown_rows.append(["TOTAL", len(local_sessions), len(hq_sessions)])

    print(tabulate(
        breakdown_rows,
        headers=["Status", "LOCAL", "HQ"],
        tablefmt="github",
        colalign=("left", "right", "right"),
    ))

    # ── 2. AWAITING CERTIFICATE — DETAIL ─────────────────────────────────────
    section("2 · AWAITING CERTIFICATE — DETAILED VIEW (LOCAL)")

    awaiting = [
        s for s in local_sessions.values()
        if s["status"] == "approved_pending_certificate"
    ]

    if not awaiting:
        print("  ✔  No sessions awaiting certificate on LOCAL.")
    else:
        rows = []
        for s in awaiting:
            pc_status = PENDING_CERT_LABELS.get(s["pc_sync_status"] or "", "⚠  No PendingCertificate row!")
            rows.append([
                str(s["id"])[:8] + "…",
                s["device_serial"] or "—",
                s["device_model"]  or "—",
                s["workshop_name"] or "—",
                s["approved_by"]   or "—",
                age(s["approved_at"]),
                pc_status,
                s["pc_retry_count"] if s["pc_retry_count"] is not None else "—",
                age(s["pc_last_attempt"]),
                (s["pc_error_message"] or "")[:50] or "—",
            ])

        print(tabulate(
            rows,
            headers=[
                "Session ID", "Serial", "Model", "Workshop",
                "Approved By", "Approved", "PC Status",
                "Retries", "Last Try", "Error"
            ],
            tablefmt="github",
        ))

        # Sub-summary: breakdown of PendingCertificate sync_status
        print()
        pc_counts = {}
        no_pc = 0
        for s in awaiting:
            if s["pc_sync_status"] is None:
                no_pc += 1
            else:
                pc_counts[s["pc_sync_status"]] = pc_counts.get(s["pc_sync_status"], 0) + 1

        print("  PendingCertificate breakdown:")
        for k, v in pc_counts.items():
            print(f"    {PENDING_CERT_LABELS.get(k, k):<25} {v}")
        if no_pc:
            print(f"    ⚠  Missing PendingCertificate row   {no_pc}  ← BUG: session stuck with no tracking row")

    # ── 3. CERTIFICATE NUMBER HEALTH ─────────────────────────────────────────
    section("3 · CERTIFICATE NUMBER HEALTH")

    def cert_health(sessions, label):
        approved     = [s for s in sessions.values() if s["status"] == "approved"]
        with_cert    = [s for s in approved if s["certificate_number"]]
        without_cert = [s for s in approved if not s["certificate_number"]]

        print(f"\n  [{label}]")
        print(f"    Approved sessions          : {len(approved)}")
        print(f"    ✔  Have certificate number : {len(with_cert)}")
        print(f"    ✘  Missing certificate     : {len(without_cert)}")

        if without_cert:
            rows = [
                [str(s["id"])[:8] + "…", s["device_serial"] or "—",
                 s["approved_by"] or "—", age(s["approved_at"])]
                for s in without_cert
            ]
            print()
            print(tabulate(rows,
                headers=["Session ID", "Serial", "Approved By", "Approved Age"],
                tablefmt="simple"))

    cert_health(local_sessions, "LOCAL")
    if hq_sessions:
        cert_health(hq_sessions, "HQ")
    else:
        print("\n  [HQ] — offline, skipped")

    # ── 4. SYNC DELTA  (LOCAL ↔ HQ) ──────────────────────────────────────────
    section("4 · SYNC DELTA  (LOCAL ↔ HQ)")

    only_local = local_ids - hq_ids
    only_hq    = hq_ids    - local_ids
    in_both    = local_ids & hq_ids

    print(f"  Sessions on LOCAL only (not yet synced to HQ) : {len(only_local)}")
    print(f"  Sessions on HQ only    (not on LOCAL)         : {len(only_hq)}")
    print(f"  Sessions on both                              : {len(in_both)}")

    if only_local:
        print("\n  ── LOCAL-only sessions ──")
        rows = []
        for sid in sorted(only_local):
            s = local_sessions[sid]
            rows.append([
                sid[:8] + "…",
                STATUS_LABELS.get(s["status"], s["status"]),
                s["device_serial"] or "—",
                age(s["created_at"]),
                "YES" if s["needs_sync"] else "no",
            ])
        print(tabulate(rows,
            headers=["Session ID", "Status", "Serial", "Created", "needs_sync"],
            tablefmt="simple"))

    if only_hq and hq_sessions:
        print("\n  ── HQ-only sessions (orphaned) ──")
        rows = []
        for sid in sorted(only_hq):
            s = hq_sessions[sid]
            rows.append([
                sid[:8] + "…",
                STATUS_LABELS.get(s["status"], s["status"]),
                s["device_serial"] or "—",
                age(s["created_at"]),
            ])
        print(tabulate(rows,
            headers=["Session ID", "Status", "Serial", "Created"],
            tablefmt="simple"))

    # ── 5. STATUS MISMATCH (same UUID, different status) ─────────────────────
    section("5 · STATUS MISMATCH  (same session, different status on each DB)")

    if not hq_sessions:
        print("  HQ offline — skipped.")
    else:
        mismatches = []
        for sid in in_both:
            ls = local_sessions[sid]
            hs = hq_sessions[sid]
            if ls["status"] != hs["status"]:
                mismatches.append([
                    sid[:8] + "…",
                    ls["device_serial"] or "—",
                    STATUS_LABELS.get(ls["status"], ls["status"]),
                    STATUS_LABELS.get(hs["status"], hs["status"]),
                    age(ls["updated_at"]),
                    age(hs["updated_at"]),
                ])

        if mismatches:
            print(tabulate(mismatches,
                headers=["Session ID", "Serial", "LOCAL status", "HQ status",
                         "LOCAL updated", "HQ updated"],
                tablefmt="github"))
        else:
            print("  ✔  No status mismatches found.")

    # ── 6. NEEDS_SYNC BACKLOG ─────────────────────────────────────────────────
    section("6 · LOCAL needs_sync BACKLOG")

    backlog = [s for s in local_sessions.values() if s.get("needs_sync")]
    print(f"  Sessions marked needs_sync=TRUE : {len(backlog)}")
    if backlog:
        by_status = {}
        for s in backlog:
            by_status[s["status"]] = by_status.get(s["status"], 0) + 1
        for st, cnt in by_status.items():
            print(f"    {STATUS_LABELS.get(st, st):<35} {cnt}")

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    section("SUMMARY")

    awaiting_count  = sum(1 for s in local_sessions.values()
                          if s["status"] == "approved_pending_certificate")
    failed_pc       = sum(1 for s in local_sessions.values()
                          if s["pc_sync_status"] == "failed")
    missing_pc_row  = sum(1 for s in local_sessions.values()
                          if s["status"] == "approved_pending_certificate"
                          and s["pc_sync_status"] is None)
    high_retry      = sum(1 for s in local_sessions.values()
                          if (s["pc_retry_count"] or 0) >= 3)

    print(f"""
  ┌─────────────────────────────────────────────────────┐
  │  Sessions awaiting certificate          : {awaiting_count:<6}     │
  │  PendingCertificate status=failed       : {failed_pc:<6}     │
  │  Awaiting cert but NO tracking row ⚠   : {missing_pc_row:<6}     │
  │  High retry count (≥3)                 : {high_retry:<6}     │
  │  LOCAL-only (not synced to HQ)          : {len(only_local):<6}     │
  │  needs_sync backlog                     : {len(backlog):<6}     │
  └─────────────────────────────────────────────────────┘
""")

    if local_conn:
        local_conn.close()
    if hq_conn:
        hq_conn.close()


if __name__ == "__main__":
    main()
