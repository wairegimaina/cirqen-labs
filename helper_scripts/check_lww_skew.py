#!/usr/bin/env python3
"""
check_lww_skew.py

Compares the client (local_db) and HQ (hq_db) Postgres schemas to find every
table where `source_updated_at` exists on one side but not the other. Those
are exactly the tables that will hit the "Dropping unknown columns" warning
in upload_processing.py, since HQ drops any column its live schema doesn't
have (see _table_columns() / _upsert_entity()).

Usage:
    Set the same env vars the app already uses, then run:

        POSTGRES_LOCAL_HOST=127.0.0.1 POSTGRES_LOCAL_PORT=2215 \\
        POSTGRES_LOCAL_DB=cirqen1 POSTGRES_LOCAL_USER=cirqen1 \\
        POSTGRES_LOCAL_PASSWORD=*** \\
        POSTGRES_HQ_HOST=... POSTGRES_HQ_PORT=5432 \\
        POSTGRES_HQ_DB=... POSTGRES_HQ_USER=... POSTGRES_HQ_PASSWORD=*** \\
        POSTGRES_HQ_SSLMODE=require \\
        python3 check_lww_skew.py

    Or just make sure a `.env` (python-dotenv format) with those same key
    names is present in the current directory / repo root; it will be
    picked up automatically if python-dotenv is installed.

Excludes known non-synced / infra tables (migrations, sessions, outbox,
django internals) by default -- edit EXCLUDE_PATTERNS below if your infra
tables differ.
"""

import os
import sys

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
    "sync_",  # sync_agent internal bookkeeping tables, if any live in-db
    "auth_permission",
    "django_content_type",  # usually not app data worth comparing, remove if you DO sync it
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
    # Prefer a full DSN if given (e.g. the Supabase pooler connection string) --
    # simplest and avoids host/port drift between .env and the real HQ DB.
    dsn = os.getenv("HQ_DATABASE_URL") or os.getenv("DATABASE_URL")
    if dsn:
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HQ_HOST"),
        port=int(os.getenv("POSTGRES_HQ_PORT", "")),
        dbname=os.getenv("POSTGRES_HQ_DB"),
        user=os.getenv("POSTGRES_HQ_USER"),
        password=os.getenv("POSTGRES_HQ_PASSWORD", ""),
        sslmode=os.getenv("POSTGRES_HQ_SSLMODE", "require"),
    )


def tables_with_column_flag(conn, schema="public"):
    """
    Return {table_name: has_source_updated_at} for every base table in the
    given schema, excluding infra tables matched by EXCLUDE_PATTERNS.
    """
    query = """
        SELECT t.table_name,
               EXISTS (
                   SELECT 1 FROM information_schema.columns c
                   WHERE c.table_schema = t.table_schema
                     AND c.table_name = t.table_name
                     AND c.column_name = 'source_updated_at'
               ) AS has_col
        FROM information_schema.tables t
        WHERE t.table_schema = %s AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name;
    """
    with conn.cursor() as cur:
        cur.execute(query, (schema,))
        rows = cur.fetchall()

    result = {}
    for name, has_col in rows:
        if any(pat in name for pat in EXCLUDE_PATTERNS):
            continue
        result[name] = has_col
    return result


def main():
    print("Connecting to local (client) DB...")
    lconn = local_conn()
    print("Connecting to HQ DB...")
    hconn = hq_conn()

    local_tables = tables_with_column_flag(lconn)
    hq_tables = tables_with_column_flag(hconn)

    lconn.close()
    hconn.close()

    all_names = sorted(set(local_tables) | set(hq_tables))

    missing_on_hq = []  # client has col, HQ doesn't -> causes the warning
    missing_on_client = []  # HQ has col, client doesn't -> latent, no warning yet
    only_local = []
    only_hq = []
    both_ok = 0

    for name in all_names:
        in_local = name in local_tables
        in_hq = name in hq_tables
        if in_local and not in_hq:
            only_local.append(name)
            continue
        if in_hq and not in_local:
            only_hq.append(name)
            continue

        local_has = local_tables[name]
        hq_has = hq_tables[name]
        if local_has and not hq_has:
            missing_on_hq.append(name)
        elif hq_has and not local_has:
            missing_on_client.append(name)
        else:
            both_ok += 1

    print(f"\nChecked {len(all_names)} tables (present in either DB, infra tables excluded).\n")

    print(
        f"⚠️  Tables causing the 'Dropping unknown columns' warning "
        f"(client HAS source_updated_at, HQ does NOT -- {len(missing_on_hq)}):"
    )
    for t in missing_on_hq:
        print(f"   - {t}")

    print(
        f"\nℹ️  Tables where HQ HAS the column but client does NOT "
        f"(latent skew, no warning yet -- {len(missing_on_client)}):"
    )
    for t in missing_on_client:
        print(f"   - {t}")

    if only_local:
        print(f"\n❓ Tables that exist locally but not on HQ ({len(only_local)}):")
        for t in only_local:
            print(f"   - {t}")

    if only_hq:
        print(f"\n❓ Tables that exist on HQ but not locally ({len(only_hq)}):")
        for t in only_hq:
            print(f"   - {t}")

    print(f"\n✅ {both_ok} tables already match (both have it, or both don't).")

    if missing_on_hq:
        print(
            "\nSuggested fix: run the migration that adds `source_updated_at` "
            "to the tables listed above on the HQ database, matching what "
            "the client schema already has."
        )


if __name__ == "__main__":
    main()
