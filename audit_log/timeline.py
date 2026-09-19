"""One timeline over the audit records the system actually writes.

There are two audit trails, and only one of them was ever shown.

``audit_log.AuditLog`` is a row-level change log — table name, row id, old and
new values — written by the Inventory transfer and reactivation flows and by
the sync agents. It is the right record for "what changed in the database", and
it is nearly unreadable if you want to know what a person did.

``CalSoft.CalibrationAuditLog`` is the opposite: a human-readable trail of who
approved a session, completed a schedule or edited a procedure, written from
five places across the calibration module. Nothing displayed it, so every one
of those entries was being recorded and never read.

This module normalises both into one shape so the viewer can show them
together, and so a filter can narrow to either. Nothing is copied or migrated —
each record stays in its own table and is read in place.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Merging two tables in Python means reading a bounded window from each rather
# than the whole history. This is the cap per source before merging; the viewer
# paginates what comes back.
MERGE_WINDOW = 500

SOURCE_SYSTEM = "system"
SOURCE_CALIBRATION = "calibration"

SOURCE_CHOICES = [
    ("", "All activity"),
    (SOURCE_CALIBRATION, "Calibration activity"),
    (SOURCE_SYSTEM, "System and sync changes"),
]


@dataclass(frozen=True)
class Entry:
    """One audit record, whichever trail it came from."""

    when: datetime
    source: str
    actor: str
    action: str
    detail: str
    subject: str = ""
    reference: str = ""

    @property
    def source_label(self):
        return "Calibration" if self.source == SOURCE_CALIBRATION else "System"


def _system_entries(limit=MERGE_WINDOW, table_name="", operation=""):
    from .models import AuditLog

    rows = AuditLog.objects.all()
    if table_name:
        rows = rows.filter(table_name__icontains=table_name)
    if operation:
        rows = rows.filter(operation=operation)

    labels = dict(AuditLog.OPERATION_CHOICES)
    entries = []
    for row in rows.order_by("-received_at")[:limit]:
        entries.append(Entry(
            when=row.received_at,
            source=SOURCE_SYSTEM,
            actor=getattr(row, "source", "") or "system",
            action=labels.get(row.operation, row.operation or "Change"),
            detail=f"{row.table_name}",
            subject=str(getattr(row, "row_id", "") or ""),
            reference=str(row.event_id),
        ))
    return entries


def _calibration_entries(limit=MERGE_WINDOW, search=""):
    """Calibration activity, read in place from CalSoft.

    Imported lazily: audit_log must not depend on the calibration module at
    import time, and a deployment without it should still show system changes
    rather than fail to load the page.
    """
    try:
        from CalSoft.models import CalibrationAuditLog
    except Exception:
        logger.warning("Calibration audit records unavailable", exc_info=True)
        return []

    rows = CalibrationAuditLog.objects.filter(active_status=True)
    if search:
        rows = rows.filter(description__icontains=search)

    rows = rows.select_related("user", "equipment", "session").order_by("-timestamp")[:limit]

    entries = []
    for row in rows:
        user = row.user
        actor = (
            user.get_full_name() or user.get_username()
            if user else "system"
        )
        equipment = row.equipment
        subject = ""
        if equipment is not None:
            description = getattr(equipment.description, "name", None) or equipment.model or ""
            subject = f"{description} · {equipment.serial_number}".strip(" ·")

        entries.append(Entry(
            when=row.timestamp,
            source=SOURCE_CALIBRATION,
            actor=actor,
            action=(row.action or "").replace("_", " ").capitalize() or "Activity",
            detail=row.description or "",
            subject=subject,
            reference=str(row.id),
        ))
    return entries


def build_timeline(source="", table_name="", operation="", search=""):
    """Audit records from both trails, newest first.

    ``source`` narrows to one trail. ``table_name`` and ``operation`` apply to
    system changes only; ``search`` applies to calibration descriptions. A
    filter that belongs to the other trail excludes it, rather than being
    silently ignored — a filtered view should not show records the filter could
    not have been applied to.
    """
    entries = []

    wants_system = source in ("", SOURCE_SYSTEM)
    wants_calibration = source in ("", SOURCE_CALIBRATION)

    # A table or operation filter is meaningless for calibration activity, so
    # narrowing by one of those means the user is asking about system changes.
    if table_name or operation:
        wants_calibration = False

    if wants_system:
        entries.extend(_system_entries(table_name=table_name, operation=operation))
    if wants_calibration:
        entries.extend(_calibration_entries(search=search))

    entries.sort(key=lambda e: e.when, reverse=True)
    return entries


def counts():
    """How many records exist in each trail, for the viewer's header."""
    from .models import AuditLog

    result = {"system": AuditLog.objects.count(), "calibration": 0}
    try:
        from CalSoft.models import CalibrationAuditLog

        result["calibration"] = CalibrationAuditLog.objects.filter(active_status=True).count()
    except Exception:
        logger.debug("Calibration audit count unavailable", exc_info=True)
    return result
