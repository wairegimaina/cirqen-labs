"""Organisation details printed on PDFs (job cards, certificates, reports).

The PDF headers used to hard-code "Phone: +254-XXX-XXXX" and made-up email
addresses. They now print whatever the site configured in config.json
(client.email / client.phone), and leave out anything not configured.
"""


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
