"""Deterministic identities for upload events.

HQ records every applied upload in ``audit_log`` with
``ON CONFLICT (event_id) DO NOTHING`` and serves that table to every other
machine as its download feed. Events used to get a fresh ``uuid4`` each time they
were built, so every re-send of an unchanged row — a retried batch, a request HQ
applied but answered too slowly, a drift-reconciler re-scan — added another audit
row. One calibration schedule that had not changed since August reached 235 audit
rows this way, and the download backlog grew past 300,000 events for about 3,000
distinct rows.

An id derived from what is being sent (table, row, row version, operation) makes
every re-send of the same version land on the audit row HQ already has, while a
genuinely new version still gets a new id.
"""
import uuid

_NAMESPACE = uuid.UUID("5b1f3c1e-8f0a-4d7e-9a57-2f6c0e8b4d21")


def stable_event_id(table, row_id, version, operation):
    """UUID that is identical for every send of the same row version."""
    if hasattr(version, "isoformat"):
        version = version.isoformat()
    return str(uuid.uuid5(_NAMESPACE, f"{table}|{row_id}|{version}|{operation}"))
