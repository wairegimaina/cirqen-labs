"""Upload event ids must be stable per row version (see sync/event_identity.py)."""
from datetime import datetime, timezone

from sync.event_identity import stable_event_id

TS = datetime(2026, 8, 6, 7, 9, 9, 532377, tzinfo=timezone.utc)


def test_same_row_version_gets_the_same_id():
    assert stable_event_id("public.t", "r1", TS, "u") == stable_event_id("public.t", "r1", TS, "u")


def test_datetime_and_its_isoformat_are_the_same_version():
    assert stable_event_id("public.t", "r1", TS, "u") == stable_event_id("public.t", "r1", TS.isoformat(), "u")


def test_new_version_row_or_operation_gets_a_new_id():
    base = stable_event_id("public.t", "r1", TS, "u")
    later = TS.replace(microsecond=532378)
    assert stable_event_id("public.t", "r1", later, "u") != base
    assert stable_event_id("public.t", "r2", TS, "u") != base
    assert stable_event_id("public.t", "r1", TS, "d") != base
