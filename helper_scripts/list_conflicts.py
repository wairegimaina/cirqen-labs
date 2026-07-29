#!/usr/bin/env python3
"""
list_conflicts.py — inspect the sync conflict quarantine (local DB).

Shows last-write-wins losers recorded by the sync agent instead of being
silently dropped. Credentials come from .env / config.json (no secrets here).

Usage:
    python3 helper_scripts/list_conflicts.py            # unreviewed conflicts
    python3 helper_scripts/list_conflicts.py --all      # include reviewed
    python3 helper_scripts/list_conflicts.py --mark 42  # mark id 42 reviewed
"""
import os
import sys
import argparse

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _creds import LOCAL_DB, require  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include already-reviewed conflicts")
    ap.add_argument("--mark", type=int, metavar="ID", help="mark a conflict id as reviewed")
    args = ap.parse_args()

    require("local")
    conn = psycopg2.connect(
        host=LOCAL_DB["host"], port=LOCAL_DB["port"], dbname=LOCAL_DB["database"],
        user=LOCAL_DB["user"], password=LOCAL_DB["password"], connect_timeout=8,
    )
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Table may not exist yet if no conflict has ever occurred.
    cur.execute("SELECT to_regclass('public.sync_conflicts') AS t")
    if cur.fetchone()["t"] is None:
        print("No sync_conflicts table yet — no conflicts have been quarantined.")
        return

    if args.mark is not None:
        cur.execute("UPDATE sync_conflicts SET reviewed = TRUE WHERE id = %s", (args.mark,))
        conn.commit()
        print(f"Marked conflict {args.mark} as reviewed ({cur.rowcount} row).")
        return

    where = "" if args.all else "WHERE reviewed = FALSE"
    cur.execute(f"""
        SELECT id, table_name, row_id, winner, resolution,
               local_updated_at, remote_updated_at, source, reviewed, detected_at
        FROM sync_conflicts {where}
        ORDER BY detected_at DESC
    """)
    rows = cur.fetchall()
    if not rows:
        print("No conflicts to show. 🎉")
        return

    print(f"{len(rows)} conflict(s):\n")
    for r in rows:
        print(f"  #{r['id']} [{r['detected_at']:%Y-%m-%d %H:%M}] {r['table_name']}[{r['row_id']}]")
        print(f"     winner={r['winner']} resolution={r['resolution']} source={r['source']} "
              f"reviewed={r['reviewed']}")
        print(f"     local_ts={r['local_updated_at']}  remote_ts={r['remote_updated_at']}")
    print("\nInspect the discarded payload:  SELECT remote_data FROM sync_conflicts WHERE id = <id>;")
    print("Mark handled:                   python3 helper_scripts/list_conflicts.py --mark <id>")


if __name__ == "__main__":
    main()
