"""Each hospital's profile: which modules its PCs have, and their menu labels.

Edited in the admin panel, signed with Control's key under its own context,
served at /api/profiles/<code>/. The desktop (hospital_profile.py) keeps it
only if it is for its hospital and not older than the copy it has, so
profile_version only goes up. Module keys are shared with the desktop's
hospital_profile.MODULES; change both together.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

CONTEXT = b"cirqen-profile-v1\n"

# key: default menu label (hospital_profile.MODULES on the desktop)
MODULES = {
    "jobcard": "Work Orders",
    "inventory": "Inventory",
    "machine_reports": "Machine Reports",
    "reports": "Report Hub",
    "ppms": "PPM Schedules",
    "calibration": "Calibration",
    "parts_tools": "Parts & Tools",
}


def document(code: str, profile: dict, version: int) -> str:
    return json.dumps({
        "type": "cirqen-hospital-profile", "v": 1, "hospital": code, "profile_version": version,
        "modules": {k: bool(profile.get("modules", {}).get(k, True)) for k in MODULES},
        "labels": {k: v for k, v in (profile.get("labels") or {}).items() if k in MODULES and v},
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }, sort_keys=True, separators=(",", ":"))


def signed(code: str, profile: dict, version: int, signing_key) -> dict:
    import base64

    raw = document(code, profile, version)
    return {"document": raw, "signature": base64.b64encode(signing_key.sign(CONTEXT + raw.encode())).decode()}
