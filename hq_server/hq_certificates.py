"""Issue a hospital HQ's identity key and certificate.

A desktop with a hospital code syncs only with an HQ that shows a certificate
from Cirqen Control for that hospital and address, and proves it holds the
certified key (hq_handshake.py on the desktop, hospital_identity.py on HQ).
The certificate is signed with the update signing key (HQ_SIGNING_PRIVATE_KEY,
or hq_signing_key.private beside this file), which every install trusts,
under its own context so it cannot pass for any other signed document.

New hospital HQ (makes its key and certificate):
    python hq_server/hq_certificates.py new --hospital CH0001 \\
        --url https://hq-ch0001.cirqenlabs.com/api/sync \\
        --url https://old-hq.example.com/api/sync

Renew or add an address, keeping the HQ's key (its public key is in the old
certificate, or /api/sync/hello shows it):
    python hq_server/hq_certificates.py renew --hospital CH0001 \\
        --public-key <base64> --url https://hq-ch0001.cirqenlabs.com/api/sync

Paste the printed values into that hospital's HQ service on Render
(HOSPITAL_CODE, HQ_IDENTITY_PRIVATE_KEY, HQ_CERTIFICATE) and redeploy.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CERTIFICATE_CONTEXT = b"cirqen-hq-certificate-v1\n"  # same as hq_handshake.py / hospital_identity.py
CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,31}$")
DEFAULT_DAYS = 365


def _signing_key(path: str | None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = os.environ.get("HQ_SIGNING_PRIVATE_KEY", "").strip()
    if not raw:
        key_file = Path(path) if path else Path(__file__).with_name("hq_signing_key.private")
        if key_file.is_file():
            raw = key_file.read_text().strip()
    if not raw:
        sys.exit("No signing key: set HQ_SIGNING_PRIVATE_KEY or pass --signing-key-file")
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw))


def _raw_public(key) -> str:
    from cryptography.hazmat.primitives import serialization

    return base64.b64encode(key.public_bytes(serialization.Encoding.Raw,
                                             serialization.PublicFormat.Raw)).decode()


def _check_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("https://") or not url.endswith("/api/sync"):
        sys.exit(f"--url must be the HQ's https sync address ending in /api/sync, got {url!r}")
    return url


def issue(signing_key, hospital: str, hq_public_key_b64: str, urls: list[str],
          days: int = DEFAULT_DAYS, now: datetime | None = None) -> str:
    """HQ_CERTIFICATE: JSON {"document": ..., "signature": ...}."""
    now = now or datetime.now(timezone.utc)
    document = json.dumps({
        "type": "cirqen-hq-certificate",
        "v": 1,
        "hospital": hospital,
        "hq_urls": urls,
        "hq_public_key": hq_public_key_b64,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
    }, sort_keys=True, separators=(",", ":"))
    signature = base64.b64encode(signing_key.sign(CERTIFICATE_CONTEXT + document.encode())).decode()
    return json.dumps({"document": document, "signature": signature}, separators=(",", ":"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["new", "renew"])
    ap.add_argument("--hospital", required=True, help="hospital code, e.g. CH0001")
    ap.add_argument("--url", action="append", required=True,
                    help="an https sync address of this HQ (repeat for each name it answers on)")
    ap.add_argument("--public-key", help="renew: the HQ's existing public key (base64)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--signing-key-file")
    args = ap.parse_args(argv)

    hospital = args.hospital.strip().upper()
    if not CODE.match(hospital):
        sys.exit(f"--hospital {args.hospital!r} is not a valid code (e.g. CH0001)")
    urls = [_check_url(u) for u in args.url]
    signer = _signing_key(args.signing_key_file)

    private_b64 = ""
    if args.action == "new":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        hq_key = Ed25519PrivateKey.generate()
        private_b64 = base64.b64encode(hq_key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )).decode()
        public_b64 = _raw_public(hq_key.public_key())
    else:
        if not args.public_key:
            sys.exit("renew needs --public-key (the HQ's current public key)")
        public_b64 = args.public_key.strip()

    certificate = issue(signer, hospital, public_b64, urls, args.days)
    print(f"# Hospital {hospital}: set these on its HQ web service (Render > Environment), then redeploy.")
    print(f"# Covers: {', '.join(urls)}; expires in {args.days} days.")
    print(f"HOSPITAL_CODE={hospital}")
    if private_b64:
        print("# Secret: put the next value only on this HQ, nowhere else.")
        print(f"HQ_IDENTITY_PRIVATE_KEY={private_b64}")
    else:
        print("# HQ_IDENTITY_PRIVATE_KEY: unchanged")
    print(f"HQ_CERTIFICATE={certificate}")


if __name__ == "__main__":
    main()
