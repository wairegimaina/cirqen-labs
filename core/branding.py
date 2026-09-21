"""Organisation details printed on PDFs (job cards, certificates, reports).

The PDF headers used to hard-code "Phone: +254-XXX-XXXX" and made-up email
addresses. They now print whatever the site configured in config.json
(client.email / client.phone), and leave out anything not configured.
"""


def organisation_name(default="Calibration Laboratory"):
    """The configured site name, for PDFs and machine-readable payloads.

    Certificate output carried three competing identities: a generator class
    named after one hospital, a laboratory name in the PDF config, and a
    hard-coded constant in the QR payload. ``SITE_NAME`` comes from the same
    ``client.name`` the rest of the platform uses, so a deployment states who
    issued a certificate in one place.
    """
    try:
        from django.conf import settings

        return getattr(settings, "SITE_NAME", None) or default
    except Exception:  # used outside a configured Django process
        return default


def organisation_slug(default="CALIBRATION_LABORATORY"):
    """``organisation_name`` in the upper-snake form the QR payload uses."""
    name = organisation_name(default=default)
    return "_".join(name.upper().split())


def contact_line(*extra):
    """'Email: … | Phone: … | <extra>', omitting unset parts."""
    try:
        from django.conf import settings

        contact = getattr(settings, "REPORT_CONTACT", {}) or {}
    except Exception:  # used outside a configured Django process
        contact = {}
    parts = []
    if contact.get("email"):
        parts.append(f"Email: {contact['email']}")
    if contact.get("phone"):
        parts.append(f"Phone: {contact['phone']}")
    parts.extend(part for part in extra if part)
    return " | ".join(parts)
