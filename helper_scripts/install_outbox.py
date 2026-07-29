#!/usr/bin/env python3
"""
install_outbox.py — install (or remove) the Phase-2 outbox CDC triggers on the
local database, without restarting the sync agent.

Uses the SAME DDL the agent uses (imported from sync.outbox), and the table list
from the app config. Credentials come from .env / config.json (no secrets here).

Usage:
    python3 helper_scripts/install_outbox.py            # install table+fn+triggers
    python3 helper_scripts/install_outbox.py --status   # show trigger/outbox state
    python3 helper_scripts/install_outbox.py --uninstall # drop triggers (keeps data)
"""
import os
import sys
import argparse

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _creds import LOCAL_DB, require  # noqa: E402
from sync.outbox import (  # noqa: E402
    _OUTBOX_TABLE_SQL, _OUTBOX_FUNCTION_SQL, _TRIGGER_NAME, OutboxMixin,
)


def _tables():
    from config import CirqenConfig
    from pathlib import Path
    cfg = CirqenConfig(Path.home() / ".cirqen" / "data", use_env_file=False)
    return cfg.get("sync_tables") or []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    require("local")
    conn = psycopg2.connect(
        host=LOCAL_DB["host"], port=LOCAL_DB["port"], dbname=LOCAL_DB["database"],
        user=LOCAL_DB["user"], password=LOCAL_DB["password"], connect_timeout=8,
    )
    conn.autocommit = False
    cur = conn.cursor()
    tables = _tables()

    if args.status:
        cur.execute("SELECT to_regclass('public.sync_outbox')")
        print("sync_outbox table:", "present" if cur.fetchone()[0] else "absent")
        cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname = %s", (_TRIGGER_NAME,))
        print(f"outbox triggers attached: {cur.fetchone()[0]} / {len(tables)} sync tables")
        cur.execute("SELECT to_regclass('public.sync_outbox')")
        if cur.fetchone()[0]:
            cur.execute("SELECT count(*), coalesce(max(seq),0) FROM sync_outbox")
            n, mx = cur.fetchone()
            print(f"outbox rows: {n} (max seq {mx})")
        return

    if args.uninstall:
        removed = 0
        for t in tables:
            schema, tbl = OutboxMixin._split_qualified(t)
            cur.execute("SELECT to_regclass(%s)", (f'"{schema}"."{tbl}"',))
            if cur.fetchone()[0] is None:
                continue
            cur.execute(f'DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON "{schema}"."{tbl}"')
            removed += 1
        conn.commit()
        print(f"Removed outbox triggers from {removed} table(s). "
              f"(sync_outbox table + data kept — drop manually if desired.)")
        return

    # install
    cur.execute(_OUTBOX_TABLE_SQL)
    cur.execute(_OUTBOX_FUNCTION_SQL)
    installed, skipped = 0, 0
    for t in tables:
        schema, tbl = OutboxMixin._split_qualified(t)
        cur.execute("SELECT to_regclass(%s)", (f'"{schema}"."{tbl}"',))
        if cur.fetchone()[0] is None:
            skipped += 1
            continue
        ident = f'"{schema}"."{tbl}"'
        cur.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON {ident}")
        cur.execute(
            f"CREATE TRIGGER {_TRIGGER_NAME} AFTER INSERT OR UPDATE OR DELETE ON {ident} "
            f"FOR EACH ROW EXECUTE FUNCTION cirqen_outbox_capture()"
        )
        installed += 1
    conn.commit()
    print(f"✅ Outbox installed: {installed} trigger(s) attached, {skipped} table(s) skipped.")
    print("   Enable in config: sync.use_outbox = true, then restart the agent.")


if __name__ == "__main__":
    main()
