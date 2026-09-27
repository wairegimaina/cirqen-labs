"""Large PDF reports built in the background and kept for 15 minutes.

A report that takes seconds (the equipment category PDF for thousands of
machines) used to hold a web worker for the whole build. Now the page asks for
it, Celery builds it, and the page downloads it when ready. The same request
within 15 minutes is served from disk at once.

A job runs the report's own view with a request rebuilt for the same user and
query string, so scoping and filters are exactly what the page would get.
Files live in DATA_PATH/report_cache; each has a small JSON sidecar naming its
owner, so one user can never fetch another's report.
"""
import hashlib
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings

logger = logging.getLogger(__name__)

FRESH_SECONDS = 15 * 60
STALE_RUNNING_SECONDS = 10 * 60

# name -> (dotted path of the view, download filename)
REPORTS = {
    "equipment_category": ("machineReports.views.exports.export_equipment_category_detailed_pdf",
                           "equipment_report.pdf"),
    "manufacturer_performance": ("machineReports.views.exports.export_manufacturer_performance_pdf",
                                 "manufacturer_performance.pdf"),
}


def cache_dir():
    path = Path(getattr(settings, "DATA_PATH", Path.cwd() / "data")) / "report_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_key(name, params, user_id):
    raw = json.dumps([name, sorted(params.items()), str(user_id)])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _paths(key):
    base = cache_dir() / key
    return base.with_suffix(".pdf"), base.with_suffix(".json")


def _meta(key):
    try:
        return json.loads(_paths(key)[1].read_text())
    except (OSError, ValueError):
        return None


def _write_meta(key, **meta):
    _paths(key)[1].write_text(json.dumps(meta))


def state(key, user_id):
    """'ready' | 'running' | 'failed' | 'missing' for this user's job."""
    meta = _meta(key)
    if not meta or meta.get("user_id") != str(user_id):
        return "missing"
    pdf, _ = _paths(key)
    now = time.time()
    if meta.get("status") == "ready" and pdf.exists() and now - meta.get("finished", 0) < FRESH_SECONDS:
        return "ready"
    if meta.get("status") == "running" and now - meta.get("started", 0) < STALE_RUNNING_SECONDS:
        return "running"
    if meta.get("status") == "failed" and now - meta.get("finished", 0) < 60:
        return "failed"
    return "missing"


def start(name, params, user):
    """Queue (or reuse) a report; returns (key, state)."""
    if name not in REPORTS:
        raise KeyError(name)
    params = {k: v for k, v in params.items() if k != "_"}
    key = job_key(name, params, user.pk)
    current = state(key, user.pk)
    if current in ("ready", "running"):
        return key, current
    _write_meta(key, status="running", name=name, user_id=str(user.pk), started=time.time())
    try:
        from core.tasks import build_report

        build_report.delay(name, params, str(user.pk), key)
    except Exception as exc:  # broker down: build here rather than fail
        logger.warning("Report queue unavailable (%s); building %s in the request", exc, name)
        build(name, params, str(user.pk), key)
    return key, state(key, user.pk)


def build(name, params, user_id, key):
    """Run the report's view as the user and store the PDF."""
    from importlib import import_module

    from django.contrib.auth import get_user_model
    from django.test import RequestFactory

    view_path, _filename = REPORTS[name]
    module, attr = view_path.rsplit(".", 1)
    view = getattr(import_module(module), attr)
    pdf, _ = _paths(key)
    try:
        user = get_user_model().objects.get(pk=user_id)
        request = RequestFactory().get("/report/?" + urlencode(params))
        request.user = user
        request.session = {}
        response = view(request)
        if response.status_code != 200 or not response.get("Content-Type", "").startswith("application/pdf"):
            raise RuntimeError(f"report view answered {response.status_code}")
        body = b"".join(response.streaming_content) if response.streaming else response.content
        tmp = pdf.with_suffix(".partial")
        tmp.write_bytes(body)
        tmp.replace(pdf)
        _write_meta(key, status="ready", name=name, user_id=str(user_id), finished=time.time(), size=len(body))
    except Exception as exc:
        logger.exception("Report %s failed", name)
        _write_meta(key, status="failed", name=name, user_id=str(user_id), finished=time.time(), error=str(exc)[:200])


def file_for(key, user_id):
    """(path, download filename) of a ready report owned by user_id, else None."""
    if state(key, user_id) != "ready":
        return None
    meta = _meta(key)
    return _paths(key)[0], REPORTS[meta["name"]][1]


def prune():
    """Delete reports older than the freshness window (run by beat)."""
    cutoff = time.time() - FRESH_SECONDS - 60
    removed = 0
    for path in cache_dir().iterdir():
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed
