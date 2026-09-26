"""Time-based one-time codes (RFC 6238) for a second sign-in step.

Works with any authenticator app (Google Authenticator, Microsoft
Authenticator, Aegis, ...): 6 digits, 30-second steps, SHA-1, as those apps
expect. A code is accepted for the current step and one step either side
(clock drift), and never twice (replay). Recovery codes are single-use and
stored hashed.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
STEP = 30
RECOVERY_CODES = 8


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret, step):
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now=None):
    return int((now if now is not None else time.time()) // STEP)


def matching_step(secret, code, now=None, window=1):
    """The step a code belongs to (within +/- window), or None."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in range(step - window, step + window + 1):
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret, account, issuer):
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"


def qr_data_uri(text):
    """A PNG data URI of ``text`` as a QR code, for the setup page."""
    import io

    import qrcode

    image = qrcode.make(text, box_size=6, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _hash_recovery(code):
    return hashlib.sha256(code.replace("-", "").lower().encode()).hexdigest()


def new_recovery_codes():
    """(plain codes to show once, hashes to store)."""
    plain = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(RECOVERY_CODES)]
    return plain, [_hash_recovery(c) for c in plain]


def use_recovery_code(stored_hashes, code):
    """Remaining hashes if ``code`` matched one (it is consumed), else None."""
    digest = _hash_recovery(str(code or "").strip())
    if digest in stored_hashes:
        return [h for h in stored_hashes if h != digest]
    return None
