"""Cache-busting static URLs without collectstatic (IMPROVEMENT_PLAN.md 5.4).

``{% static "js/app.js" %}`` renders ``/static/js/app.js?v=<content hash>``.

The desktop build serves /static/ straight from the source directories through
runserver's static handler, and in-app updates overwrite those files in place.
Hashed *filenames* (ManifestStaticFilesStorage) would need a collectstatic run
after every update and a different static server; a content-hash query string
gives browsers the same guarantee — a changed file gets a new URL, so nobody has
to hard-refresh after an update — with no change to how files are served.
"""
import hashlib
import os
import threading

from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage

_versions = {}  # (absolute path, mtime_ns, size) -> short hash
_lock = threading.Lock()


def file_version(name):
    """Short content hash for a static file, or "" if it cannot be found."""
    path = finders.find(name)
    if not path or isinstance(path, list):
        return ""
    try:
        stat = os.stat(path)
    except OSError:
        return ""
    key = (path, stat.st_mtime_ns, stat.st_size)
    with _lock:
        cached = _versions.get(key)
    if cached is not None:
        return cached
    digest = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    version = digest.hexdigest()[:12]
    with _lock:
        _versions[key] = version
    return version


class VersionedStaticFilesStorage(StaticFilesStorage):
    def url(self, name):
        url = super().url(name)
        if "?" in url:
            return url
        version = file_version(name)
        return f"{url}?v={version}" if version else url
