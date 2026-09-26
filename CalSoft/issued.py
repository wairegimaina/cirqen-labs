"""Issued certificates are stored once and served unchanged afterwards.

``issued_pdf(session, user, build)`` returns the PDF bytes for a session:

* not issued yet (no number, not approved, or declined): ``build()``'s output,
  not stored;
* issued, first time: ``build()``'s output, stored with its SHA-256;
* issued, already stored: the stored bytes, after checking the fingerprint.

A stored file that no longer matches its fingerprint raises
``CertificateTampered``: the copy on disk was altered, and serving it (or
silently regenerating it) would hide that.
"""
import hashlib
import logging
import uuid

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction

logger = logging.getLogger(__name__)


class CertificateTampered(Exception):
    """The stored issued certificate no longer matches its fingerprint."""


def _is_placeholder(number):
    """Before HQ assigns a number, a session can carry its own UUID."""
    try:
        uuid.UUID(number)
    except ValueError:
        return False
    return True


def is_issued(session):
    number = (session.certificate_number or "").strip()
    return bool(number) and session.status == "approved" and not _is_placeholder(number)


def fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def _read_verified(record):
    with record.pdf.open("rb") as fh:
        data = fh.read()
    if fingerprint(data) != record.sha256:
        logger.error("Issued certificate %s failed its fingerprint check (stored %s)",
                     record.certificate_number, record.sha256)
        raise CertificateTampered(
            f"The stored copy of certificate {record.certificate_number} does not match its "
            f"fingerprint and was not served. Report this to the quality office.")
    return data


def issued_pdf(session, user, build):
    """PDF bytes for ``session``; see the module docstring."""
    from CalSoft.models import IssuedCertificate

    if not is_issued(session):
        return build()

    record = IssuedCertificate.objects.filter(
        session=session, certificate_number=session.certificate_number).first()
    if record:
        return _read_verified(record)

    data = build()
    digest = fingerprint(data)
    try:
        with transaction.atomic():
            record = IssuedCertificate(
                session=session, certificate_number=session.certificate_number,
                sha256=digest, size=len(data), issued_by=user if getattr(user, "pk", None) else None)
            record.pdf.save(f"{session.certificate_number}-{session.pk}.pdf", ContentFile(data), save=False)
            record.save()
    except IntegrityError:
        # Another request stored it first; serve that one.
        record = IssuedCertificate.objects.get(session=session, certificate_number=session.certificate_number)
        return _read_verified(record)
    logger.info("Stored issued certificate %s (sha256 %s)", session.certificate_number, digest)
    return data
