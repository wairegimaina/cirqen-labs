"""Indexes for the sync poller and the busiest filters (IMPROVEMENT_PLAN.md, data model).

The sync agent's timestamp poller runs ``WHERE updated_at > %s`` against every
synced table on every cycle (sync_agent_2.py), and HQ downloads filter the same
way; none of those columns was indexed. A few composite indexes cover the most
frequent page filters.

Created with CREATE INDEX CONCURRENTLY IF NOT EXISTS so a large table is not
locked for writes, from a non-atomic migration (core 0001). Everything here is
best-effort: a missing table or column is skipped, a failure is logged rather
than raised (an index must never fail an update), and an invalid index left by
an interrupted concurrent build is dropped and rebuilt.
"""
import logging

logger = logging.getLogger(__name__)

# Tables listed in sync_tables (sync/config.py) that carry updated_at.
SYNCED_TABLES = [
    "accounts_customuser", "workshop_workshop", "Inventory_department", "Inventory_manufacturer",
    "Inventory_equipmentdescription", "Inventory_equipment", "users_userprofile",
    "users_usersecuritylog", "users_usersignature", "CalSoft_parametercategory", "CalSoft_parameter",
    "CalSoft_subparameter", "CalSoft_standardtype", "CalSoft_standard", "CalSoft_standardparameter",
    "CalSoft_calibrationprocedure", "CalSoft_calibrationparameter", "CalSoft_setvalue",
    "calSchedules_calibrationschedule", "CalSoft_calibrationsession", "CalSoft_calibrationreading",
    "CalSoft_sessionparameterresolution", "CalSoft_historicalcalibration", "CalSoft_calibrationworkflow",
    "CalSoft_calibrationreport", "CalSoft_calibrationauditlog", "CalSoft_calibrationnotification",
    "CalSoft_equipmentcalibrationprocedure", "calSchedules_calibrationauditlog", "pending_certificates",
    "jobcard_jobcard", "jobcard_sparepartused", "machineReports_equipmentcategory",
    "machineReports_equipmentstatusreport", "machineReports_machinerepairhistory",
    "machineReports_workshopequipmentreport", "parts_tools_accessoriesname",
    "parts_tools_accessoriesmanufacturer", "parts_tools_accessories", "parts_tools_accessoryrequest",
    "ppms_ppmschedule",
]

# (table, columns): composite indexes for the most frequent page filters.
COMPOSITE = [
    ("CalSoft_calibrationsession", ("status", "approved_at")),     # approval queue, certificates by date
    ("Inventory_equipment", ("department_id", "active_status")),   # every inventory list
    ("CalSoft_calibrationnotification", ("recipient_id", "is_read")),  # unread badge on every page
]


def planned_indexes():
    """[(index name, table, columns)]"""
    plan = [(_name(table, ("updated_at",)), table, ("updated_at",)) for table in SYNCED_TABLES]
    plan += [(_name(table, cols), table, cols) for table, cols in COMPOSITE]
    return plan


def _name(table, columns):
    name = f"cq_{table.lower()}_{'_'.join(columns)}"
    return name[:63]


def _columns_exist(cursor, table, columns):
    cursor.execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = ANY(%s)",
        [table, list(columns)],
    )
    return cursor.fetchone()[0] == len(columns)


def _drop_if_invalid(cursor, name):
    cursor.execute(
        "SELECT NOT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = %s", [name],
    )
    row = cursor.fetchone()
    if row and row[0]:
        cursor.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')


def ensure_indexes(cursor):
    """Create the planned indexes on a PostgreSQL connection in autocommit mode.

    Returns (created_or_present, skipped, failed) name lists.
    """
    done, skipped, failed = [], [], []
    for name, table, columns in planned_indexes():
        try:
            if not _columns_exist(cursor, table, columns):
                skipped.append(name)
                continue
            _drop_if_invalid(cursor, name)
            column_sql = ", ".join(f'"{c}"' for c in columns)
            cursor.execute(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" ON public."{table}" ({column_sql})')
            done.append(name)
        except Exception as exc:  # an optimisation must never fail a migration
            logger.warning("Could not create index %s on %s: %s", name, table, exc)
            failed.append(name)
    return done, skipped, failed
