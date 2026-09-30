"""Confirm an HQ is this hospital's before sending it anything.

    desktop  --  POST <sync url>/hello {nonce}  -->  HQ
    desktop  <-- {hospital, certificate, proof}  --  HQ

The desktop accepts the HQ only when all of these hold:

1. the certificate is signed by Cirqen Control (the update signing key every
   install already trusts, under its own "HQ certificate" context so no other
   signed document can pass for one)
2. the certificate is for this PC's hospital code
3. the certificate lists the exact address the desktop is talking to
4. the certificate has not expired
5. ``proof`` is the desktop's own random nonce signed by the key in the
   certificate, so the server really holds that key (a copied certificate
   is useless without it)

Anything else, including an HQ with no hospital identity, is a refusal: the
PC keeps working offline with its changes queued. The context strings and the
certificate format are shared with hq_server/hospital_identity.py and
hq_server/hq_certificates.py here; change all three together.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone

LOG = logging.getLogger(__name__)

CERTIFICATE_CONTEXT = b"cirqen-hq-certificate-v1\n"
HELLO_CONTEXT = b"cirqen-hq-hello-v1\n"
TIMEOUT = 15


def normalise_code(value) -> str:
    return str(value or "").strip().upper()


def _url(value) -> str:
    return str(value or "").strip().rstrip("/")


def hello_message(nonce: bytes, code: str) -> bytes:
    return HELLO_CONTEXT + nonce + b"\n" + code.encode()


def _parse_time(value):
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check_answer(answer: dict, nonce: bytes, sync_url: str, hospital_code: str, control_key) -> str:
    """Why ``answer`` must be refused, or "" when the HQ is confirmed."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    code = normalise_code(hospital_code)
    if control_key is None:
        return "no update signing public key on this PC, so no HQ can be confirmed"
    cert = answer.get("certificate") if isinstance(answer, dict) else None
    if not isinstance(cert, dict) or not cert.get("document") or not cert.get("signature"):
        return "HQ sent no certificate"
    try:
        control_key.verify(base64.b64decode(cert["signature"]),
                           CERTIFICATE_CONTEXT + str(cert["document"]).encode())
    except Exception:  # noqa: BLE001 - any failure is a refusal
        return "HQ certificate is not signed by Cirqen Control"
    try:
        fields = json.loads(cert["document"])
    except ValueError:
        return "HQ certificate is not readable"
    if fields.get("type") != "cirqen-hq-certificate":
        return "HQ certificate is of the wrong type"
    if normalise_code(fields.get("hospital")) != code:
        return f"this address belongs to hospital {fields.get('hospital')}, not {code}"
    if normalise_code(answer.get("hospital")) != code:
        return f"HQ answered as hospital {answer.get('hospital')}, not {code}"
    if _url(sync_url) not in {_url(u) for u in fields.get("hq_urls") or []}:
        return f"HQ certificate does not cover the address {sync_url}"
    try:
        if _parse_time(fields.get("expires_at")) <= datetime.now(timezone.utc):
            return f"HQ certificate expired on {fields.get('expires_at')}"
    except (TypeError, ValueError):
        return "HQ certificate has no usable expiry"
    try:
        hq_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(fields["hq_public_key"]))
        hq_key.verify(base64.b64decode(answer.get("proof") or ""), hello_message(nonce, code))
    except Exception:  # noqa: BLE001
        return "HQ could not prove it holds the certified key"
    return ""


def confirm(sync_url: str, hospital_code: str, session=None, control_key=None) -> tuple[bool, str]:
    """(True, "") when the HQ at ``sync_url`` proves it is ``hospital_code``'s,
    else (False, reason). Never raises."""
    import requests

    if control_key is None:
        try:
            from endpoint_sync import _public_key

            control_key = _public_key()
        except Exception:  # noqa: BLE001
            control_key = None
    http = session or requests
    nonce = os.urandom(32)
    try:
        response = http.post(f"{_url(sync_url)}/hello",
                             json={"nonce": base64.b64encode(nonce).decode()}, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return False, f"HQ unreachable: {str(exc)[:120]}"
    if response.status_code == 404:
        return False, "HQ has no hospital identity yet (no /hello)"
    if response.status_code != 200:
        return False, f"HQ answered HTTP {response.status_code} to the handshake"
    try:
        answer = response.json()
    except ValueError:
        return False, "HQ handshake answer was not JSON"
    why = check_answer(answer, nonce, sync_url, hospital_code, control_key)
    if why:
        LOG.error("🚫 Refusing HQ %s for hospital %s: %s", sync_url, normalise_code(hospital_code), why)
        return False, why
    return True, ""
