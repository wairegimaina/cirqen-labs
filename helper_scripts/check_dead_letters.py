#!/usr/bin/env python3
"""
Show what HQ failed to apply, and why.

Rows land in sync_dead_letter when an upload batch partially succeeds. HQ still
returns 200 in that case (so one bad row cannot block the queue forever), which
means the agent advances its checkpoint and NEVER re-sends them. A table can
therefore stop converging while every upload logs success — which is exactly
what a row-count deficit that refuses to shrink looks like.

Read-only. Uses the same credentials as compare_db_counts.py.
"""
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

from _creds import HQ_DB, require

require("hq")


def main():
    table_filter = sys.argv[1] if len(sys.argv) > 1 else None

    print("\n=== HQ Dead-Letter Report ===\n")
    with psycopg2.connect(**HQ_DB) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT to_regclass('public.sync_dead_letter') IS NOT NULL AS present")
        if not cur.fetchone()["present"]:
            print("sync_dead_letter table does not exist on HQ — nothing was ever recorded.")
            print("That means the missing rows were never dead-lettered, and a replay")
            print("cannot recover them: the agent must re-scan and re-upload instead.")
            return

        where, params = "WHERE resolved = FALSE", []
        if table_filter:
            where += " AND table_name = %s"
            params.append(table_filter)

        cur.execute(f"SELECT COUNT(*) AS n FROM sync_dead_letter {where}", params)
        total = cur.fetchone()["n"]
        print(f"Unresolved dead-lettered events: {total}\n")
        if not total:
            print("Nothing unresolved. If tables are still short, those rows were never")
            print("dead-lettered — they were simply never uploaded. See the note below.")
            return

        # Group on an error PREFIX: psycopg2 messages embed per-row detail, so
        # grouping on the full string yields one bucket per row and hides the pattern.
        cur.execute(
            f"""
            SELECT table_name, operation, left(error, 110) AS error, COUNT(*) AS count
            FROM sync_dead_letter {where}
            GROUP BY table_name, operation, left(error, 110)
            ORDER BY COUNT(*) DESC LIMIT 25
            """,
            params,
        )
        rows = cur.fetchall()
        print(f"{'TABLE':<42} {'OP':<6} {'COUNT':>7}  ERROR")
        print("-" * 120)
        for r in rows:
            err = (r["error"] or "").replace("\n", " ")[:60]
            print(f"{r['table_name'][:41]:<42} {str(r['operation'])[:5]:<6} {r['count']:>7}  {err}")

        cur.execute(
            f"SELECT table_name, COUNT(*) AS n FROM sync_dead_letter {where} "
            f"GROUP BY table_name ORDER BY COUNT(*) DESC",
            params,
        )
        print("\nPer-table totals:")
        for r in cur.fetchall():
            print(f"   {r['table_name']:<45} {r['n']:>7}")

    print(
        "\nIf these counts are far SMALLER than the row-count deficit, most missing\n"
        "rows were never dead-lettered — they were never uploaded at all, and the\n"
        "fix is to reset the agent's upload checkpoint so it re-scans everything,\n"
        "not to replay. Replay only recovers what is listed above."
    )


if __name__ == "__main__":
    main()
