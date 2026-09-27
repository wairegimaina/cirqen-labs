"""Who this site is: name, address, contacts and logo, for pages and PDFs.

Values come from core.models.SiteProfile (the Site details page), falling back
to config.json (client.name, client.email, client.phone via SITE_NAME and
REPORT_CONTACT). Looked up once per process and refreshed when the profile is
saved (in every worker, via the cache key's version).
"""
from django.core.cache import cache

_CACHE_KEY = "core:site-profile:v1"
_local = {}


def clear_cache():
    _local.clear()
    try:
        cache.delete(_CACHE_KEY)
    except Exception:  # cache down: the local copy is already cleared
        pass


def _profile():
    """{"name", "address", "phone", "email", "logo_path", "stamp"} or {}."""
    try:
        stamp = cache.get(_CACHE_KEY)
    except Exception:
        stamp = None
    if _local and stamp is not None and _local.get("stamp") == stamp:
        return _local
    try:
        from core.models import SiteProfile

        row = SiteProfile.objects.filter(pk=1).first()
    except Exception:  # no database yet (migrations, early startup)
        return {}
    data = {"stamp": str(row.updated_at) if row else "none"}
    if row:
        logo = ""
        if row.logo:
            try:
                logo = row.logo.path
            except (ValueError, NotImplementedError):
                logo = ""
        data.update(name=row.name, address=row.address, phone=row.phone, email=row.email, logo_path=logo)
    _local.clear()
    _local.update(data)
    try:
        cache.set(_CACHE_KEY, data["stamp"], None)
    except Exception:
        pass
    return _local


def _setting(name, default=None):
    try:
        from django.conf import settings

        return getattr(settings, name, default)
    except Exception:  # used outside a configured Django process
        return default


def organisation_name(default="Calibration Laboratory"):
    """The site's name for PDFs, pages and machine-readable payloads."""
    return _profile().get("name") or _setting("SITE_NAME") or default


def organisation_slug(default="CALIBRATION_LABORATORY"):
    """``organisation_name`` in the upper-snake form the QR payload uses."""
    name = organisation_name(default=default)
    return "_".join(name.upper().split())


def organisation_address():
    return _profile().get("address") or ""


def logo_path():
    """Filesystem path of the uploaded site logo, or None."""
    import os

    path = _profile().get("logo_path")
    return path if path and os.path.exists(path) else None


def contact_line(*extra):
    """'Email: … | Phone: … | <extra>', omitting unset parts."""
    profile = _profile()
    contact = _setting("REPORT_CONTACT", {}) or {}
    email = profile.get("email") or contact.get("email")
    phone = profile.get("phone") or contact.get("phone")
    parts = []
    if email:
        parts.append(f"Email: {email}")
    if phone:
        parts.append(f"Phone: {phone}")
    parts.extend(part for part in extra if part)
    return " | ".join(parts)
