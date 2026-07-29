"""
Deterministic last-write-wins conflict resolution.

The previous resolver compared ``updated_at`` with a 1-second "same moment"
tolerance and, inside that window, let whichever side happened to apply last
win — a non-deterministic outcome that could diverge between client and HQ.

``resolve_conflict`` replaces that with a total order over
``(updated_at, tiebreak_key)`` tuples:

    * different timestamps  -> newer wins
    * identical timestamps  -> the lexicographically greater key wins

Given both peers apply the same rule with the same inputs, they converge on the
SAME winner regardless of the order updates arrive in. (The HQ server should
mirror this rule; the client half is enforced here.)
"""
from datetime import datetime, timezone
from typing import Any, Optional


def _norm(ts: Any) -> Optional[datetime]:
    """Coerce a timestamp (datetime | ISO string | None) to aware UTC, or None."""
    if ts is None or ts == "":
        return None
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


class ConflictResolverMixin:
    """Provides a deterministic, convergent winner decision for LWW."""

    def resolve_conflict(
        self,
        local_ts: Any,
        remote_ts: Any,
        local_key: str = "",
        remote_key: str = "",
    ) -> str:
        """
        Return ``"local"`` or ``"remote"`` — the side that should win.

        Missing-timestamp policy (idempotent, overwrite-safe):
          * both missing   -> remote  (apply is an idempotent upsert)
          * local missing  -> remote
          * remote missing  -> local
        """
        lu = _norm(local_ts)
        ru = _norm(remote_ts)

        if lu is None and ru is None:
            return "remote"
        if lu is None:
            return "remote"
        if ru is None:
            return "local"

        if lu != ru:
            return "local" if lu > ru else "remote"

        # Exact tie -> stable, deterministic tiebreak.
        return "local" if str(local_key) > str(remote_key) else "remote"
