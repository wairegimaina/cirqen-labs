"""Which release a hospital's desktops are offered.

Each hospital, set in the admin panel:
  follow  the newest release (the default, and what a PC with no hospital
          code gets)
  pin     exactly release_version, once it is built; a PC already past it
          is not moved back
  hold    nothing new: the PCs stay on what they have (e.g. while that
          hospital's HQ is being upgraded)

The fleet-wide controls in each package's metadata (yanked, min_version,
rollout_percent) still apply to whatever is offered.
"""
from __future__ import annotations


def _version(v) -> tuple:
    try:
        parts = [int(x) for x in str(v).strip().split(".")]
    except ValueError:
        return (0, 0, 0)
    return tuple((parts + [0, 0, 0])[:3])


def policy_for(hospital_code: str | None) -> dict:
    """{"mode": "follow"|"pin"|"hold", "version": str} for this hospital."""
    if not hospital_code:
        return {"mode": "follow", "version": ""}
    try:
        import control_store

        if not control_store.db_path().exists():
            return {"mode": "follow", "version": ""}
        row = control_store.get_hospital(str(hospital_code).strip().upper())
    except Exception:  # noqa: BLE001 - no panel yet: behave as before
        return {"mode": "follow", "version": ""}
    if row is None:
        return {"mode": "follow", "version": ""}
    return {"mode": row.get("release_mode") or "follow", "version": row.get("release_version") or ""}


def choose(metas: list[dict], current_version: str, policy: dict) -> tuple[dict | None, str]:
    """(package metadata to offer or None, reason when None)."""
    live = [m for m in metas if not m.get("yanked")]
    if policy["mode"] == "hold":
        return None, "This hospital's releases are on hold"
    if policy["mode"] == "pin":
        target = next((m for m in live if _version(m.get("version")) == _version(policy["version"])), None)
        if target is None:
            return None, f"Pinned release {policy['version']} is not available"
    else:
        target = max(live, key=lambda m: _version(m.get("version")), default=None)
        if target is None:
            return None, "No packages available yet"
    if _version(target["version"]) <= _version(current_version):
        return None, ""
    return target, ""
