#!/usr/bin/env python3
"""
compare_local_hq.py — compare this PC's database with HQ's, table by table.

Reports:
  * tables that exist on only one side (sync tables marked)
  * columns missing on one side, and columns whose types differ
  * for every table on both sides: row counts, rows only here, rows only on
    HQ, and rows present on both whose contents differ (with the columns)

HQ is opened read-only; nothing is written anywhere except the report.

Usage (from cirqen-labs):
    venv/bin/python helper_scripts/compare_local_hq.py [--out report.md] [--all-tables]

The installed app's database (~/.local/share/cirqen, port 2216 on the dev PC):
    venv/bin/python helper_scripts/compare_local_hq.py --local-config ~/.local/share/cirqen/config.json --local-port 2216

HQ address: HQ_DATABASE_URL if set, else HQ's Supabase session pooler. The
password is asked for (hidden) and never stored. The local database comes from
_creds (env / ~/.cirqen/data/config.json / .env) unless --local-config names a
config.json to take it from (the repo .env is then ignored: it holds the dev
database's password).

Rows "only on HQ" are normal for data other sites uploaded; the useful signals
are rows only here (not uploaded), rows that differ, and schema gaps.
"""
import argparse
import getpass
import os
import sys
from urllib.parse import urlparse, unquote

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from _creds import LOCAL_DB  # noqa: E402

DEFAULT_HQ_URL = (
    "postgresql://postgres.nwlwaeeyduxroykrgksi@aws-0-eu-north-1.pooler.supabase.com:5432/postgres?sslmode=require"
)

# Bookkeeping that legitimately differs between a site and HQ.
IGNORED_COLUMNS = {"needs_sync", "source_updated_at", "last_synced_at", "sync_status"}
# Framework tables, not application data.
SKIP_PREFIXES = ("django_", "auth_", "pg_")
SAMPLES = 5


def sync_tables():
    try:
        import config

        return [t.split(".", 1)[-1] for t in config.CirqenConfig.DEFAULT_CONFIG["sync_tables"]]
    except Exception as exc:  # report still works, just without the marks
        print(f"(could not read sync_tables from config.py: {exc})")
        return []


def hq_connect():
    url = os.getenv("HQ_DATABASE_URL") or DEFAULT_HQ_URL
    u = urlparse(url)
    password = unquote(u.password) if u.password else getpass.getpass(f"HQ database password for {u.username}@{u.hostname}: ")
    params = dict(p.split("=", 1) for p in u.query.split("&") if "=" in p)
    conn = psycopg2.connect(
        host=u.hostname, port=u.port or 5432, dbname=u.path.lstrip("/") or "postgres",
        user=unquote(u.username or ""), password=password, sslmode=params.get("sslmode", "require"),
        connect_timeout=20, options="-c default_transaction_read_only=on -c statement_timeout=120000",
    )
    return conn, f"{u.hostname}:{u.port or 5432}/{u.path.lstrip('/')}"


def prepare(conn):
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'UTC'")


def columns(cur):
    cur.execute(
        """SELECT table_name, column_name, data_type
           FROM information_schema.columns
           WHERE table_schema = 'public'
           ORDER BY table_name, ordinal_position"""
    )
    out = {}
    for table, col, dtype in cur.fetchall():
        out.setdefault(table, {})[col] = dtype
    return out


def primary_keys(cur):
    cur.execute(
        """SELECT tc.table_name, kcu.column_name
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
             ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
           WHERE tc.table_schema = 'public' AND tc.constraint_type = 'PRIMARY KEY'
           ORDER BY tc.table_name, kcu.ordinal_position"""
    )
    out = {}
    for table, col in cur.fetchall():
        out.setdefault(table, []).append(col)
    return out


def q(name):
    return '"' + name.replace('"', '""') + '"'


def row_hashes(cur, table, pk, cols):
    """{pk text: md5 of the compared columns} for every row."""
    key = " || '|' || ".join(f"COALESCE({q(c)}::text, '∅')" for c in pk)
    body = ", ".join(q(c) for c in cols)
    cur.execute(f"SELECT {key}, md5(ROW({body})::text) FROM {q(table)}")
    return dict(cur.fetchall())


def fetch_row(cur, table, pk, key_text, cols):
    key = " || '|' || ".join(f"COALESCE({q(c)}::text, '∅')" for c in pk)
    cur.execute(f"SELECT {', '.join(q(c) + '::text' for c in cols)} FROM {q(table)} WHERE {key} = %s LIMIT 1", [key_text])
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else {}


def short(v, n=60):
    s = "NULL" if v is None else str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="db_compare_report.md")
    ap.add_argument("--local-config", help="config.json whose local_db to compare (e.g. the installed app's).")
    ap.add_argument("--local-port", type=int, help="Override the local database port.")
    ap.add_argument("--all-tables", action="store_true", help="Compare data in every shared table, not only sync tables.")
    args = ap.parse_args()

    synced = sync_tables()
    synced_set = set(synced)

    local_db = dict(LOCAL_DB)
    if args.local_config:
        import json

        cfg = json.load(open(os.path.expanduser(args.local_config)))["local_db"]
        local_db = {"host": cfg.get("host", "127.0.0.1"), "port": int(cfg.get("port", 2215)),
                    "database": cfg["database"], "user": cfg["user"], "password": cfg.get("password", "")}
    if args.local_port:
        local_db["port"] = args.local_port
    local = psycopg2.connect(**local_db, connect_timeout=10)
    prepare(local)
    hq, hq_label = hq_connect()
    prepare(hq)
    lcur, hcur = local.cursor(), hq.cursor()

    lcols, hcols = columns(lcur), columns(hcur)
    lpk, hpk = primary_keys(lcur), primary_keys(hcur)
    keep = lambda t: not t.startswith(SKIP_PREFIXES)  # noqa: E731
    ltables = {t for t in lcols if keep(t)}
    htables = {t for t in hcols if keep(t)}

    lines = [f"# Local vs HQ database\n",
             f"- Local: `{local_db['host']}:{local_db['port']}/{local_db['database']}`",
             f"- HQ: `{hq_label}` (read-only)",
             f"- Sync tables in config.py: {len(synced)}\n"]

    # ── Tables ────────────────────────────────────────────────────────────
    lines.append("## Tables on one side only\n")
    missing_sync_on_hq = [t for t in synced if t not in htables]
    missing_sync_here = [t for t in synced if t not in ltables]
    if missing_sync_on_hq:
        lines.append("**Sync tables missing on HQ (uploads of these fail):** " + ", ".join(f"`{t}`" for t in missing_sync_on_hq) + "\n")
    if missing_sync_here:
        lines.append("**Sync tables missing here:** " + ", ".join(f"`{t}`" for t in missing_sync_here) + "\n")
    only_l = sorted(ltables - htables - synced_set)
    only_h = sorted(htables - ltables - synced_set)
    lines.append("- Other tables only here: " + (", ".join(f"`{t}`" for t in only_l) or "none"))
    lines.append("- Other tables only on HQ: " + (", ".join(f"`{t}`" for t in only_h) or "none") + "\n")

    # ── Columns ───────────────────────────────────────────────────────────
    lines.append("## Column differences (shared tables)\n")
    col_rows = []
    for t in sorted(ltables & htables):
        lc, hc = lcols[t], hcols[t]
        mark = " (sync)" if t in synced_set else ""
        for c in lc:
            if c not in hc and c not in IGNORED_COLUMNS:
                col_rows.append(f"| `{t}`{mark} | `{c}` | only here ({lc[c]}) |")
        for c in hc:
            if c not in lc and c not in IGNORED_COLUMNS:
                col_rows.append(f"| `{t}`{mark} | `{c}` | only on HQ ({hc[c]}) |")
        for c in lc.keys() & hc.keys():
            if lc[c] != hc[c]:
                col_rows.append(f"| `{t}`{mark} | `{c}` | type here {lc[c]}, on HQ {hc[c]} |")
    if col_rows:
        lines += ["| Table | Column | Difference |", "|---|---|---|", *col_rows, ""]
    else:
        lines.append("None.\n")

    # ── Data ──────────────────────────────────────────────────────────────
    lines.append("## Data\n")
    lines.append("| Table | Rows here | Rows on HQ | Only here | Only on HQ | Differ | Differ beyond updated_at |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|")
    details = []
    order = [t for t in synced if t in ltables & htables]
    if args.all_tables:
        order += sorted((ltables & htables) - set(order))
    totals = [0, 0, 0, 0]
    for t in order:
        print(f"  comparing {t} …", flush=True)
        pk = lpk.get(t) or hpk.get(t)
        cols = [c for c in lcols[t] if c in hcols[t] and c not in IGNORED_COLUMNS]
        if not pk or any(c not in hcols[t] for c in pk):
            lines.append(f"| `{t}` | – | – | – | – | – | no shared primary key |")
            continue
        try:
            lh, hh = row_hashes(lcur, t, pk, cols), row_hashes(hcur, t, pk, cols)
            content = [c for c in cols if c != "updated_at"]  # HQ re-stamps updated_at on upload
            lc_, hc_ = row_hashes(lcur, t, pk, content), row_hashes(hcur, t, pk, content)
        except Exception as exc:
            lines.append(f"| `{t}` | – | – | – | – | – | error: {short(exc, 80)} |")
            continue
        only_here = sorted(lh.keys() - hh.keys())
        only_hq = sorted(hh.keys() - lh.keys())
        differ = sorted(k for k in lh.keys() & hh.keys() if lh[k] != hh[k])
        real = sorted(k for k in lc_.keys() & hc_.keys() if lc_[k] != hc_[k])
        totals[0] += len(only_here); totals[1] += len(only_hq); totals[2] += len(differ); totals[3] += len(real)
        lines.append(f"| `{t}` | {len(lh):,} | {len(hh):,} | {len(only_here):,} | {len(only_hq):,} | {len(differ):,} | {len(real):,} |")

        if only_here or real:
            d = [f"### `{t}`\n"]
            if only_here:
                d.append(f"Only here (first {min(SAMPLES, len(only_here))}): " + ", ".join(f"`{k}`" for k in only_here[:SAMPLES]) + "\n")
            for k in real[:SAMPLES]:
                a, b = fetch_row(lcur, t, pk, k, cols), fetch_row(hcur, t, pk, k, cols)
                changed = [c for c in cols if a.get(c) != b.get(c)]
                d.append(f"- `{k}` differs in " + ", ".join(
                    f"`{c}` (here `{short(a.get(c))}` / HQ `{short(b.get(c))}`)" for c in changed[:6]))
            details += d + [""]

    lines.append("")
    lines.append(f"**Totals:** {totals[0]:,} rows only here, {totals[1]:,} only on HQ, {totals[2]:,} differ, {totals[3]:,} of them beyond updated_at.\n")
    if details:
        lines.append("## Samples\n")
        lines += details

    with open(args.out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nReport written to {os.path.abspath(args.out)}")
    local.close(); hq.close()


if __name__ == "__main__":
    main()
