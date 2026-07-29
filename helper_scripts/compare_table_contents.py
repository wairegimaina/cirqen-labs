#!/usr/bin/env python3
"""
compare_table_contents.py

Compares actual table CONTENTS (not just schema) between the local client
DB and HQ, for every table present on both sides.

Method mirrors what HQ's own data_checker.py already does for client
bootstrap comparisons, so results here match what the app itself would
report:
  - exact row count
  - MD5 checksum over (id, updated_at) pairs ordered by id
    (falls back to id-only if the table has no updated_at column)

A mismatched checksum with matching row count usually means some rows
differ in content/updated_at without the row count changing (e.g. an
update that didn't propagate). A mismatched row count means rows are
outright missing on one side.

Usage:
    POSTGRES_LOCAL_HOST=... POSTGRES_LOCAL_PORT=2215 \\
    POSTGRES_LOCAL_DB=... POSTGRES_LOCAL_USER=... POSTGRES_LOCAL_PASSWORD=... \\
    HQ_DATABASE_URL="postgresql://user:pass@host:port/db" \\
    python3 compare_table_contents.py [table1 table2 ...]

If no table names are given on the command line, it compares every base
table present in BOTH databases (excluding known infra tables).
"""

import os
import sys
import json
import hashlib

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    import psycopg2
except ImportError:
    print("psycopg2 is required: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


EXCLUDE_PATTERNS = (
    "django_migrations",
    "django_session",
    "django_admin_log",
    "sync_outbox",
    "sync_",
    "auth_permission",
)


def local_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_LOCAL_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_LOCAL_PORT", "2215")),
        dbname=os.getenv("POSTGRES_LOCAL_DB", "cirqen1"),
        user=os.getenv("POSTGRES_LOCAL_USER", "cirqen1"),
        password=os.getenv("POSTGRES_LOCAL_PASSWORD", ""),
    )


def hq_conn():
    dsn = os.getenv("HQ_DATABASE_URL") or os.getenv("DATABASE_URL")
    if dsn:
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HQ_HOST"),
        port=int(os.getenv("POSTGRES_HQ_PORT", "5432")),
        dbname=os.getenv("POSTGRES_HQ_DB"),
        user=os.getenv("POSTGRES_HQ_USER"),
        password=os.getenv("POSTGRES_HQ_PASSWORD", ""),
        sslmode=os.getenv("POSTGRES_HQ_SSLMODE", "require"),
    )


def common_tables(lconn, hconn):
    def base_tables(conn):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public' AND table_type='BASE TABLE'
            """)
            return {r[0] for r in cur.fetchall() if not any(p in r[0] for p in EXCLUDE_PATTERNS)}

    return sorted(base_tables(lconn) & base_tables(hconn))


def table_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
        """,
            (table,),
        )
        return {r[0] for r in cur.fetchall()}


def row_count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM public."{table}"')
        return cur.fetchone()[0]


def checksum(conn, table, cols):
    if "id" not in cols:
        return None, "no id column"
    order_and_select = "id, updated_at" if "updated_at" in cols else "id"
    with conn.cursor() as cur:
        cur.execute(f'SELECT {order_and_select} FROM public."{table}" ORDER BY id')
        rows = cur.fetchall()
    digest = hashlib.md5(json.dumps(rows, default=str).encode()).hexdigest()
    basis = "id+updated_at" if "updated_at" in cols else "id only"
    return digest, basis


def main():
    requested = sys.argv[1:]

    print("Connecting to local (client) DB...")
    lconn = local_conn()
    print("Connecting to HQ DB...")
    hconn = hq_conn()

    tables = requested if requested else common_tables(lconn, hconn)
    print(f"\nComparing {len(tables)} table(s)...\n")

    mismatches = []
    for t in tables:
        try:
            lcount = row_count(lconn, t)
            hcount = row_count(hconn, t)
        except Exception as exc:
            print(f"⚠️  {t}: could not count rows ({exc})")
            lconn.rollback()
            hconn.rollback()
            continue

        lcols = table_columns(lconn, t)
        hcols = table_columns(hconn, t)

        lsum, lbasis = checksum(lconn, t, lcols)
        hsum, hbasis = checksum(hconn, t, hcols)

        count_ok = lcount == hcount
        sum_ok = (lsum == hsum) if (lsum is not None and hsum is not None) else None

        if count_ok and (sum_ok is True or sum_ok is None):
            status = "✅"
        else:
            status = "❌"
            mismatches.append(t)

        print(
            f"{status} {t}: local={lcount} rows, hq={hcount} rows"
            f"{'' if count_ok else '  <-- COUNT MISMATCH'}"
        )
        if sum_ok is False:
            print(
                f"     checksum differs (local basis={lbasis}, hq basis={hbasis})"
                f" -- same row count but content differs"
            )
        elif lsum is None or hsum is None:
            print(f"     (skipped checksum: {lbasis if lsum is None else hbasis})")

    lconn.close()
    hconn.close()

    print(f"\n{len(mismatches)} of {len(tables)} table(s) differ.")
    if mismatches:
        print("Tables needing attention:")
        for t in mismatches:
            print(f"   - {t}")


if __name__ == "__main__":
    main()
