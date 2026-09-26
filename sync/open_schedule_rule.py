"""One open schedule per equipment: the rule every writer and resolver shares.

The database enforces it with a partial unique index per schedule table:

    UNIQUE (equipment_id)
    WHERE active_status AND NOT pending_delete AND status <> 'completed'

A violation of that index says only ``Key (equipment_id)=(...)``. The
generic unique-conflict resolvers read that key and act on "the" row holding
it, but an equipment has many rows under the key that the index does not
cover (its completed history). Left to the generic path, the conflict would
re-point references onto, or delete, a completed schedule. So these
violations are recognised by constraint name and resolved here instead:
never by deleting, only by retiring the surplus *open* row.

Which open schedule survives, everywhere (HQ upload, desktop download, the
audit repair): work under way first, then the earliest month, then the
lowest id, so every machine reaches the same answer on its own.
"""

ONE_OPEN_CONSTRAINTS = {
    "ppms_one_open_schedule": "ppms_ppmschedule",
    "calschedules_one_open_schedule": "calSchedules_calibrationschedule",
}

UNDER_WAY = ("in_progress", "pending_approval")

OPEN_SQL = "active_status AND NOT pending_delete AND status <> 'completed'"


def constraint_name(error):
    """The violated constraint's name, from psycopg2's diagnostics or the message."""
    name = getattr(getattr(error, "diag", None), "constraint_name", None)
    if name:
        return name
    text = str(error)
    try:
        return text.split('constraint "')[1].split('"')[0]
    except IndexError:
        return None


def is_one_open_violation(error):
    return constraint_name(error) in ONE_OPEN_CONSTRAINTS


def keeper_key(status, scheduled_month, row_id):
    """Sort key: the smallest sorts first and is the one kept."""
    return (status not in UNDER_WAY, str(scheduled_month), str(row_id))


def retire_other_open(cursor, table, equipment_id, keep_id):
    """Retire every other open schedule of ``equipment_id``; return their ids.

    ``updated_at`` is bumped so the change propagates like any other edit.
    Completed schedules are never touched (the WHERE clause is the index's).
    """
    cursor.execute(
        f'UPDATE "{table}" SET active_status = FALSE, updated_at = NOW() '
        f"WHERE equipment_id = %s AND id <> %s AND {OPEN_SQL} RETURNING id",
        (str(equipment_id), str(keep_id)),
    )
    return [row[0] if not isinstance(row, dict) else row["id"] for row in cursor.fetchall()]
