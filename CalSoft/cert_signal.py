"""Wake the sync agent the moment a certificate number is needed.

The agent otherwise finds new approvals on its certificate interval. A
PostgreSQL NOTIFY issued inside the approval's transaction is delivered to the
agent's LISTEN connection when (and only if) that transaction commits, so the
request reaches HQ within about a second of the click.
"""
from django.db import connection

CHANNEL = "cirqen_certificates"


def announce_pending_certificate(session_id):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        cur.execute("SELECT pg_notify(%s, %s)", [CHANNEL, str(session_id)])
