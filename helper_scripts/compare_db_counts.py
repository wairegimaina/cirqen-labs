#!/usr/bin/env python3
"""
compare_db_counts.py
Compares row counts across all synced tables between the local DB and the HQ DB.
"""

import psycopg2
from psycopg2 import OperationalError
from tabulate import tabulate  # pip install tabulate

# ── Connection configs (from ~/.cirqen/data/config.json) ─────────────────────

LOCAL_DB = {
    "host": "127.0.0.1",
    "port": 2215,
    "database": "cirqen1",
    "user": "cirqen1",
    "password": "Btwelvetech@2024",
}

HQ_DB = {
    "host": "dpg-d8fj2c59j78s738al2vg-a.ohio-postgres.render.com",
    "port": 5432,
    "database": "cirqen_hq_db1",
    "user": "cirqen_hq_db1_user",
    "password": "cTAU3kJL3NNlUYA9rR07kh87FKHA6c24",
}

# ── Tables to compare (sync_tables from config) ───────────────────────────────

SYNC_TABLES = [
    "public.accounts_customuser",
    "public.workshop_workshop",
    "public.Inventory_department",
    "public.Inventory_manufacturer",
    "public.Inventory_equipmentdescription",
    "public.Inventory_equipment",
    "public.users_userprofile",
    "public.users_usersecuritylog",
    "public.users_usersignature",
    "public.CalSoft_parametercategory",
    "public.CalSoft_parameter",
    "public.CalSoft_subparameter",
    "public.CalSoft_standardtype",
    "public.CalSoft_standard",
    "public.CalSoft_standardparameter",
    "public.CalSoft_calibrationprocedure",
    "public.CalSoft_calibrationparameter",
    "public.CalSoft_setvalue",
    "public.calSchedules_calibrationschedule",
    "public.CalSoft_calibrationsession",
    "public.CalSoft_calibrationreading",
    "public.CalSoft_sessionparameterresolution",
    "public.CalSoft_historicalcalibration",
    "public.CalSoft_calibrationworkflow",
    "public.CalSoft_calibrationreport",
    "public.CalSoft_calibrationauditlog",
    "public.CalSoft_calibrationnotification",
    "public.CalSoft_equipmentcalibrationprocedure",
    "public.calSchedules_calibrationauditlog",
    "public.pending_certificates",
    "public.jobcard_jobcard",
    "public.jobcard_sparepartused",
    "public.machineReports_equipmentcategory",
    "public.machineReports_equipmentstatusreport",
    "public.machineReports_machinerepairhistory",
    "public.machineReports_workshopequipmentreport",
    "public.parts_tools_accessoriesname",
    "public.parts_tools_accessoriesmanufacturer",
    "public.parts_tools_accessories",
    "public.parts_tools_accessoryrequest",
    "public.parts_tools_toolsmanufacturer",
    "public.parts_tools_toolname",
    "public.parts_tools_tools",
    "public.ppms_auditlog",
    "public.ppms_ppmschedule",
    "public.reporthub_report",
]

# ─────────────────────────────────────────────────────────────────────────────


def connect(cfg: dict, label: str):
    try:
        conn = psycopg2.connect(**cfg)
        conn.autocommit = True
        print(f"  ✔  Connected to {label}")
        return conn
    except OperationalError as e:
        print(f"  ✘  Could not connect to {label}: {e}")
        return None


def get_count(cur, table: str) -> int | None:
    """Return row count for a table, or None if the table doesn't exist."""
    schema, tbl = table.split(".", 1)
    try:
        cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{tbl}"')
        return cur.fetchone()[0]
    except Exception:
        return None


def main():
    print("\n=== Cirqen DB Row-Count Comparison ===\n")

    print("Connecting to LOCAL  DB …")
    local_conn = connect(LOCAL_DB, "LOCAL (127.0.0.1:2215/cirqen1)")

    print("Connecting to HQ     DB …")
    hq_conn = connect(HQ_DB, "HQ    (render.com/cirqen_hq_db1)")

    if not local_conn and not hq_conn:
        print("\nNeither database is reachable. Exiting.")
        return

    local_cur = local_conn.cursor() if local_conn else None
    hq_cur = hq_conn.cursor() if hq_conn else None

    rows = []
    in_sync = 0
    out_of_sync = 0
    missing = 0

    for table in SYNC_TABLES:
        local_count = get_count(local_cur, table) if local_cur else "N/A"
        hq_count = get_count(hq_cur, table) if hq_cur else "N/A"

        # Determine status
        if local_count is None or hq_count is None:
            status = "⚠  MISSING"
            missing += 1
        elif local_count == hq_count:
            status = "✔  IN SYNC"
            in_sync += 1
        else:
            diff = (hq_count or 0) - (local_count or 0)
            status = f"✘  DIFF ({diff:+d})"
            out_of_sync += 1

        rows.append(
            [
                table.replace("public.", ""),
                local_count if local_count is not None else "—",
                hq_count if hq_count is not None else "—",
                status,
            ]
        )

    # ── Print table ───────────────────────────────────────────────────────────
    print()
    print(
        tabulate(
            rows,
            headers=["Table", "LOCAL", "HQ", "Status"],
            tablefmt="github",
            intfmt=",",
            colalign=("left", "right", "right", "left"),
        )
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    total = len(SYNC_TABLES)
    print(f"""
┌─────────────────────────────┐
│        SUMMARY              │
├─────────────────────────────┤
│  Total tables checked : {total:>3} │
│  ✔  In sync           : {in_sync:>3} │
│  ✘  Out of sync       : {out_of_sync:>3} │
│  ⚠  Missing / error   : {missing:>3} │
└─────────────────────────────┘
""")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    if local_conn:
        local_conn.close()
    if hq_conn:
        hq_conn.close()


if __name__ == "__main__":
    main()
