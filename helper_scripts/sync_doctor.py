#!/usr/bin/env python3
"""
Why is this table not syncing? — answer it directly, then fix it.

The whole problem with this sync is that it fails SILENTLY: uploads return 200,
the agent logs success, and a table quietly stops converging. Row counts tell you
*that* something is wrong but never *what*. This tool closes that gap by showing
the four things that actually determine whether a row gets uploaded:

  1. the per-table upload checkpoint the agent is scanning from,
  2. how many local rows are NEWER than it (i.e. what the agent can even see),
  3. how many are OLDER than it (i.e. invisible — stranded behind the checkpoint),
  4. whether HQ already has them, and what is sitting in its dead-letter queue.

Read-only by default. `--push` force-uploads a table regardless of checkpoints.

Usage
-----
    python sync_doctor.py                          # diagnose every out-of-sync table
    python sync_doctor.py public.ppms_ppmschedule  # one table, in detail
    python sync_doctor.py --push public.ppms_ppmschedule
    python sync_doctor.py --push-all               # every table reported behind
"""
import json
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _creds import HQ_DB, LOCAL_DB, require  # noqa: E402

require("local", "hq")

STATE_FILE = Path(
    os.environ.get(
        "CIRQEN_SYNC_STATE",
        os.path.expanduser("~/.local/share/cirqen/sync_state/sync_state.json"),
    )
)
EPOCH = "1970-01-01T00:00:00+00:00"


def load_state():
    try:
        with STATE_FILE.open() as fh:
            return json.load(fh)
    except Exception as e:
        print(f"⚠️  Could not read agent state ({e}) — checkpoint info unavailable")
        return {}


def split(table):
    return table.split(".", 1) if "." in table else ("public", table)


def counts(conn, table):
    schema, tbl = split(table)
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{tbl}"')
        return cur.fetchone()[0]


def visibility(conn, table, checkpoint):
    """How many local rows the agent can currently SEE, vs stranded behind it."""
    schema, tbl = split(table)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM information_schema.columns
               WHERE table_schema=%s AND table_name=%s AND column_name='updated_at'""",
            (schema, tbl),
        )
        if not cur.fetchone():
            return None, None
        cur.execute(
            f'SELECT COUNT(*) FROM "{schema}"."{tbl}" WHERE updated_at > %s::timestamptz',
            (checkpoint,),
        )
        newer = cur.fetchone()[0]
        cur.execute(
            f'SELECT COUNT(*) FROM "{schema}"."{tbl}" WHERE updated_at <= %s::timestamptz',
            (checkpoint,),
        )
        return newer, cur.fetchone()[0]


def missing_ids(local, hq, table, limit=5):
    """Sample local ids HQ does not have — proof of what is actually absent."""
    schema, tbl = split(table)
    with local.cursor() as cur:
        cur.execute(f'SELECT id FROM "{schema}"."{tbl}" ORDER BY updated_at DESC LIMIT 4000')
        # Compare as text: id is uuid on some tables and text/int on others, and
        # `uuid = text` has no operator in Postgres.
        local_ids = [str(r[0]) for r in cur.fetchall()]
    if not local_ids:
        return []
    with hq.cursor() as cur:
        cur.execute(
            f'SELECT id::text FROM "{schema}"."{tbl}" WHERE id::text = ANY(%s)',
            (local_ids,),
        )
        hq_ids = {r[0] for r in cur.fetchall()}
    return [i for i in local_ids if i not in hq_ids][:limit]


def dead_letters(hq, table=None):
    with hq.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT to_regclass('public.sync_dead_letter') IS NOT NULL AS ok")
        if not cur.fetchone()["ok"]:
            return []
        q = ("SELECT table_name, left(error,90) AS error, COUNT(*) AS n "
             "FROM sync_dead_letter WHERE resolved = FALSE")
        p = []
        if table:
            q += " AND table_name = %s"
            p.append(table)
        q += " GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10"
        cur.execute(q, p)
        return [dict(r) for r in cur.fetchall()]


def diagnose(tables=None):
    state = load_state()
    # autocommit: a probe that fails on one table (e.g. it exists locally but
    # not on HQ) must not abort the session for every table after it.
    local = psycopg2.connect(**LOCAL_DB); local.autocommit = True
    hq = psycopg2.connect(**HQ_DB); hq.autocommit = True

    print(f"\nAgent state: {STATE_FILE}")
    print(f"Global checkpoint: {state.get('last_upload_time', '(unset)')}\n")

    if not tables:
        # Everything the agent knows about, filtered to real deficits.
        with local.cursor() as cur:
            cur.execute("""SELECT table_schema||'.'||table_name FROM information_schema.tables
                           WHERE table_schema='public' AND table_type='BASE TABLE'""")
            candidates = [r[0] for r in cur.fetchall()]
        tables = []
        for t in candidates:
            try:
                if counts(local, t) > counts(hq, t):
                    tables.append(t)
            except Exception:
                continue

    if not tables:
        print("✅ No table has more rows locally than on HQ.")
        return

    for table in tables:
        try:
            l, h = counts(local, table), counts(hq, table)
        except Exception as e:
            print(f"── {table}\n   ⚠️  {e}\n")
            continue

        ckpt = state.get(f"last_upload_time:{table}") or state.get("last_upload_time") or EPOCH
        per_table = f"last_upload_time:{table}" in state
        newer, older = visibility(local, table, ckpt)

        print(f"── {table}")
        print(f"   local={l}  hq={h}  deficit={l - h}")
        print(f"   checkpoint : {ckpt}  ({'per-table' if per_table else 'GLOBAL — not yet per-table'})")
        if newer is None:
            print("   ⚠️  no updated_at column — the agent's change detection cannot see this table at all")
        else:
            print(f"   rows the agent can SEE (updated_at > checkpoint) : {newer}")
            print(f"   rows STRANDED behind the checkpoint             : {older}")
            if newer == 0 and l > h:
                print("   ⛔ DIAGNOSIS: every missing row is behind the checkpoint.")
                print("      The agent will never re-scan them. Fix: --push this table.")
            elif newer > 0:
                print("   ✅ DIAGNOSIS: rows are visible; the upload loop should be working")
                print("      through them. If the deficit is not shrinking, they are being")
                print("      rejected — check the dead letters below.")

        gone = missing_ids(local, hq, table)
        if gone:
            print(f"   sample ids missing on HQ: {', '.join(str(g) for g in gone)}")

        dl = dead_letters(hq, table)
        if dl:
            print("   dead-lettered:")
            for d in dl:
                print(f"      {d['n']:>5}  {d['error']}")
        print()

    total_dl = dead_letters(hq)
    if total_dl:
        print("Dead letters across all tables:")
        for d in total_dl:
            print(f"   {d['n']:>5}  {d['table_name']}  {d['error']}")


def push(tables):
    """Force-upload every local row of a table, ignoring checkpoints entirely."""
    import psycopg2.pool

    # load_agent_config lives in agent_prelude, not config — importing it from
    # the wrong module fails only at --push time, which is the worst moment.
    from sync.agent_prelude import load_agent_config
    from sync.data_checker_client import DataCheckerClient

    cfg = load_agent_config(None)
    api_url = cfg["api_url"].rstrip("/")
    base = api_url[: -len("/api/sync")] if api_url.endswith("/api/sync") else api_url

    pool = psycopg2.pool.SimpleConnectionPool(1, 4, **LOCAL_DB)
    state = load_state()

    # client_id lives in a sibling FILE, not in sync_state.json, and falling
    # back to None makes HQ reject every batch with "client_id is required".
    # Derive it the same way the agent does so the push is attributed to this
    # machine (HQ uses it for echo-suppression and dead-letter attribution).
    client_id = state.get("client_id") or cfg.get("client_id")
    if not client_id:
        id_file = STATE_FILE.parent / "client_id"
        if id_file.exists():
            client_id = id_file.read_text().strip()
    if not client_id:
        from sync.device_id_generator import get_or_create_client_id
        client_id = get_or_create_client_id()
    if not client_id:
        raise SystemExit("❌ Could not determine client_id — HQ will reject the upload.")
    print(f"   client_id: {client_id}")

    dc = DataCheckerClient(
        hq_url=base,
        api_key=cfg.get("auth_token") or cfg.get("api_key"),
        client_id=client_id,
        local_pool=pool,
        allowed_tables=list(tables),
    )
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": cfg.get("auth_token") or cfg.get("api_key"),
        "X-Client-ID": client_id,
        "X-Device-ID": client_id,
    }

    for table in tables:
        print(f"\n⬆️  Force-pushing {table} …")
        res = dc.push_table_to_hq(
            table=table,
            client_id=client_id,
            machine_id=client_id,
            upload_url=f"{api_url}/upload",
            http_headers=headers,
        )
        print(f"   {res}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--push-all" in sys.argv:
        local = psycopg2.connect(**LOCAL_DB); local.autocommit = True
        hq = psycopg2.connect(**HQ_DB); hq.autocommit = True
        with local.cursor() as cur:
            cur.execute("""SELECT table_schema||'.'||table_name FROM information_schema.tables
                           WHERE table_schema='public' AND table_type='BASE TABLE'""")
            cands = [r[0] for r in cur.fetchall()]
        behind = []
        for t in cands:
            try:
                if counts(local, t) > counts(hq, t):
                    behind.append(t)
            except Exception:
                pass
        print(f"Pushing {len(behind)} table(s) behind HQ: {', '.join(behind)}")
        push(behind)
    elif "--push" in sys.argv:
        if not args:
            raise SystemExit("--push needs a table, e.g. --push public.ppms_ppmschedule")
        push(args)
    else:
        diagnose(args or None)


if __name__ == "__main__":
    import psycopg2.pool  # noqa: F401  (imported lazily for --push)
    main()
