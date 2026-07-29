import os
import psycopg2

dsn = os.getenv("HQ_DATABASE_URL")
conn = psycopg2.connect(dsn)
with conn.cursor() as cur:
    cur.execute("""
        SELECT table_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema='public' AND column_name='source_updated_at'
        ORDER BY table_name
        LIMIT 5
    """)
    rows = cur.fetchall()
    if not rows:
        print("No table on HQ has source_updated_at yet.")
    for r in rows:
        print(r)
conn.close()
