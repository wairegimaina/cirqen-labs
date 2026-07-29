#!/usr/bin/env python3
"""
sync_status.py — one-glance sync health for a client.

Combines the agent's status file (agent_status.json) with live counts from the
local diagnostic tables (outbox backlog, quarantined conflicts, schema drift).
Credentials come from .env / config.json (no secrets here).

Usage:
    python3 helper_scripts/sync_status.py
    python3 helper_scripts/sync_status.py --json
"""
import os
import sys
import json
import glob
import argparse
from pathlib import Path
from datetime import datetime

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _creds import LOCAL_DB  # noqa: E402


def _find_status_file():
    candidates = [
        Path.home() / ".cmms" / "sync_state" / "agent_status.json",
        Path.home() / ".cirqen" / "data" / "sync_state" / "agent_status.json",
    ]
    candidates += [Path(p) for p in glob.glob(str(Path.home() / "**" / "agent_status.json"), recursive=True)]
    for c in candidates:
        if c.exists():
            return c
    return None


def _table_count(cur, table, where=""):
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    if cur.fetchone()[0] is None:
        return None  # table absent
    cur.execute(f"SELECT count(*) FROM {table} {where}")
    return int(cur.fetchone()[0])


def _db_metrics():
    m = {}
    try:
        conn = psycopg2.connect(
            host=LOCAL_DB["host"], port=LOCAL_DB["port"], dbname=LOCAL_DB["database"],
            user=LOCAL_DB["user"], password=LOCAL_DB["password"], connect_timeout=5,
        )
        cur = conn.cursor()
        m["outbox_backlog"] = _table_count(cur, "sync_outbox")
        m["conflicts_unreviewed"] = _table_count(cur, "sync_conflicts", "WHERE reviewed = FALSE")
        m["schema_drift_unresolved"] = _table_count(cur, "sync_schema_drift", "WHERE resolved = FALSE")
        conn.close()
        m["db_reachable"] = True
    except psycopg2.Error as e:
        m["db_reachable"] = False
        m["db_error"] = str(e).splitlines()[0]
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    status = {}
    sf = _find_status_file()
    if sf:
        try:
            status = json.loads(sf.read_text())
        except Exception:
            status = {}

    metrics = _db_metrics()
    combined = {"status_file": str(sf) if sf else None, "agent": status, "local_db": metrics}

    if args.json:
        print(json.dumps(combined, indent=2, default=str))
        return

    def fmt(v):
        return "—" if v is None else v

    print("=" * 56)
    print(" CIRQEN SYNC STATUS")
    print("=" * 56)
    if status:
        online = status.get("hq_online")
        print(f"  HQ reachable        : {'🟢 online' if online else '🔴 offline'}")
        print(f"  Client / machine    : {status.get('client_id','?')} / {status.get('machine_id','?')}")
        print(f"  Last sync           : {status.get('last_sync','?')}")
        print(f"  Last status update  : {status.get('last_update','?')}")
        print(f"  Pending changes     : {fmt(status.get('pending_changes'))}")
        print(f"  Unreviewed conflicts: {fmt(status.get('unreviewed_conflicts'))}")
        print(f"  Schema drift        : {fmt(status.get('schema_drift'))}")
    else:
        print("  (no agent_status.json found — is the sync agent running?)")

    print("-" * 56)
    if metrics.get("db_reachable"):
        print(f"  Outbox backlog      : {fmt(metrics.get('outbox_backlog'))}")
        print(f"  Conflicts (unrev.)  : {fmt(metrics.get('conflicts_unreviewed'))}")
        print(f"  Schema drift (open) : {fmt(metrics.get('schema_drift_unresolved'))}")
    else:
        print(f"  Local DB            : 🔴 unreachable ({metrics.get('db_error','?')})")
    print("=" * 56)

    # Non-zero exit if something needs attention.
    attention = any([
        (status.get("unreviewed_conflicts") or 0),
        (status.get("schema_drift") or 0),
        (metrics.get("conflicts_unreviewed") or 0),
        (metrics.get("schema_drift_unresolved") or 0),
    ])
    sys.exit(2 if attention else 0)


if __name__ == "__main__":
    main()
