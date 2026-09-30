"""Enrollment tokens and installer settings for one hospital.

A token lets new PCs enroll with that hospital's HQ: signed by Control's key
under its own context, for one hospital, until an expiry, for at most
max_uses PCs. The HQ checks it with CONTROL_PUBLIC_KEY (hq_server's
hospital_identity.verify_enrollment_token); nothing per token is set on the
HQ. The token travels in the installer's provisioning.json, with the
hospital code and its addresses.
"""
from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone

CONTEXT = b"cirqen-enrollment-v1\n"   # same as hq_server/hospital_identity.py
PREFIX = "cqe1."


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def new_document(hospital: str, max_uses: int, days: int) -> str:
    now = datetime.now(timezone.utc)
    return json.dumps({
        "type": "cirqen-enrollment", "v": 1, "hospital": hospital, "token_id": secrets.token_hex(8),
        "max_uses": max_uses, "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
    }, sort_keys=True, separators=(",", ":"))


def token_for(signing_key, document: str) -> str:
    """Ed25519 is deterministic, so the same document always gives the same token."""
    raw = document.encode()
    return PREFIX + _b64url(raw) + "." + _b64url(signing_key.sign(CONTEXT + raw))


def provisioning(hospital: dict, token: str, update_api_key: str) -> dict:
    """The installer's provisioning.json for this hospital."""
    sync = {"hospital_code": hospital["code"], "enrollment_code": token}
    if hospital.get("sync_url"):
        sync["api_url"] = hospital["sync_url"]
    update = {"api_key": update_api_key} if update_api_key else {}
    if hospital.get("updates_url"):
        update["server_url"] = hospital["updates_url"]
    return {"sync": sync, **({"update": update} if update else {})}
