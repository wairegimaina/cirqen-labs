import os, sys
import psycopg2

dsn = os.getenv("HQ_DATABASE_URL")
if not dsn:
    print("Set HQ_DATABASE_URL first")
    sys.exit(1)

tables = [
    "reporthub_report",
    "accounts_customuser",
    "workshop_workshop",
    "Inventory_department",
    "Inventory_manufacturer",
    "Inventory_equipmentdescription",
]

conn = psycopg2.connect(dsn)
with conn.cursor() as cur:
    for t in tables:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s
                  AND column_name='source_updated_at'
            )
        """,
            (t,),
        )
        has_col = cur.fetchone()[0]
        print(
            f"{'✅' if has_col else '❌'} public.{t}: source_updated_at present on HQ = {has_col}"
        )
conn.close()
