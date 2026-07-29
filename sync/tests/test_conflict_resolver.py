"""Deterministic conflict resolution (pure logic — no DB)."""
from sync.conflict_resolver import ConflictResolverMixin

R = ConflictResolverMixin()
T0 = "2026-07-21T10:00:00+00:00"
T5 = "2026-07-21T10:00:05+00:00"


def test_newer_wins_regardless_of_arg_order():
    assert R.resolve_conflict(T0, T5, "A", "B") == "remote"
    assert R.resolve_conflict(T5, T0, "A", "B") == "local"


def test_sub_second_difference_is_not_a_tie():
    # The old 1-second fuzzy window is gone: 0.5s apart => newer wins.
    assert R.resolve_conflict(
        "2026-07-21T10:00:00.0+00:00", "2026-07-21T10:00:00.5+00:00", "A", "B"
    ) == "remote"


def test_exact_tie_broken_by_stable_key():
    assert R.resolve_conflict(T0, T0, "clientB", "clientA") == "local"   # B > A
    assert R.resolve_conflict(T0, T0, "clientA", "clientB") == "remote"  # A < B


def test_tie_is_convergent():
    # Same two records, opposite "who is local" — both peers must pick clientB.
    w1 = R.resolve_conflict(T0, T0, "clientA", "clientB")  # local=A remote=B
    w2 = R.resolve_conflict(T0, T0, "clientB", "clientA")  # local=B remote=A
    assert w1 == "remote" and w2 == "local"  # both select clientB


def test_missing_timestamps():
    assert R.resolve_conflict(None, T0, "A", "B") == "remote"
    assert R.resolve_conflict(T0, None, "A", "B") == "local"
    assert R.resolve_conflict(None, None, "A", "B") == "remote"


def test_naive_and_aware_timestamps_compared_as_utc():
    assert R.resolve_conflict("2026-07-21T10:00:00", T5, "A", "B") == "remote"
