"""
Updates app views
=================
Configure in settings.py:

    APP_VERSION = '1.0.0'   # bump to match hq_server/version.txt on each release

    UPDATE_SYSTEM = {
        'enabled': True,
        'server_url': 'https://updates.example.com',  # your update server
        'api_key': os.environ.get('HQ_API_KEY', ''),      # from Render env vars
        'check_interval_hours': 24,
        'auto_apply_updates': False,
    }

Endpoints:
  GET  /updates/                    → admin dashboard
  POST /updates/check/              → check HQ Render API for a new version
  POST /updates/upload/             → upload a .zip package manually
  POST /updates/apply/<version>/    → apply an update (async, SSE stream)
  GET  /updates/stream/<version>/   → SSE progress stream
  POST /updates/rollback/<version>/ → restore previous backup
  POST /updates/delete/<version>/   → deactivate a package
  GET  /updates/history/            → JSON list of applied updates
  GET  /updates/broadcast/          → broadcast dashboard (machine overview)
"""

import hashlib
import json
import os
import queue
import threading
import time
import zipfile

import requests
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import ClientMachine, UpdateHistory, UpdatePackage, UpdateSettings
from .updater import Updater, rollback

# ── In-memory store for active progress queues (keyed by version string) ──────
_active_updates: dict = {}
_active_lock = threading.Lock()


# ── Config helpers ─────────────────────────────────────────────────────────────

def _get_hq_config() -> dict:
    cfg = getattr(settings, "UPDATE_SYSTEM", {})
    return {
        "server_url": cfg.get("server_url", "").rstrip("/"),
        "api_key":    cfg.get("api_key", ""),
        "enabled":    cfg.get("enabled", True),
    }


def _hq_headers(cfg: dict) -> dict:
    """Auth header sent to Render HQ on every API call."""
    return {"X-Api-Key": cfg["api_key"]}


def _current_version() -> str:
    return getattr(settings, "APP_VERSION", "1.0.0")


def _machine_id() -> str:
    try:
        from sync.device_id_generator import get_device_id
        return get_device_id()
    except Exception:
        import socket
        return socket.gethostname()


# ── Dashboard ──────────────────────────────────────────────────────────────────

@staff_member_required
def dashboard(request):
    cfg = _get_hq_config()
    update_settings = UpdateSettings.get_settings()
    history  = UpdateHistory.objects.select_related("package", "applied_by").order_by("-started_at")[:20]
    packages = UpdatePackage.objects.filter(is_active=True).order_by("-created_at")[:10]
    return render(request, "update/check_updates.html", {
        "current_version": _current_version(),
        "update_settings": update_settings,
        "history":          history,
        "packages":         packages,
        "hq_server_url":    cfg["server_url"],
        "hq_configured":    bool(cfg["server_url"] and cfg["api_key"]),
    })


# ── Check HQ API on Render ─────────────────────────────────────────────────────

@staff_member_required
@require_POST
def check_updates(request):
    """
    Ask the HQ Render API whether a newer version exists.
    Hits: GET {server_url}/api/updates/latest/?current_version=X&machine_id=Y
    Saves the package record locally if an update is available.
    """
    cfg = _get_hq_config()

    if not cfg["server_url"]:
        return JsonResponse(
            {"error": "HQ server URL not configured. Add 'server_url' to settings.UPDATE_SYSTEM."},
            status=503,
        )
    if not cfg["api_key"]:
        return JsonResponse(
            {"error": "HQ API key not configured. Add 'api_key' to settings.UPDATE_SYSTEM."},
            status=503,
        )

    current = _current_version()
    mid = _machine_id()

    try:
        resp = requests.get(
            f"{cfg['server_url']}/api/updates/latest/",
            params={"current_version": current, "machine_id": mid},
            headers=_hq_headers(cfg),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

    except requests.ConnectionError:
        return JsonResponse(
            {"error": "Cannot reach HQ server. Check server_url in settings.", "update_available": False},
            status=503,
        )
    except requests.Timeout:
        return JsonResponse(
            {"error": "HQ server timed out (15 s). Render free tier may be sleeping.", "update_available": False},
            status=504,
        )
    except requests.HTTPError as exc:
        if resp.status_code == 401:
            return JsonResponse(
                {"error": "Invalid API key. Check api_key in settings.UPDATE_SYSTEM.", "update_available": False},
                status=401,
            )
        return JsonResponse({"error": str(exc), "update_available": False}, status=502)
    except Exception as exc:
        return JsonResponse({"error": str(exc), "update_available": False}, status=500)

    # Record last-check time
    us = UpdateSettings.get_settings()
    us.last_check = timezone.now()
    us.save()

    # Update this machine's record
    ClientMachine.objects.update_or_create(
        machine_id=mid,
        defaults={
            "current_version": current,
            "last_check":      timezone.now(),
            "hostname":        mid,
        },
    )

    # If an update is available, save the package info locally so the admin
    # can apply it from the dashboard without checking again.
    if data.get("update_available"):
        UpdatePackage.objects.update_or_create(
            version=data["version"],
            defaults={
                "changes":       data.get("changes", ""),
                "critical":      data.get("critical", False),
                "size_bytes":    data.get("size_bytes", 0),
                "checksum":      data.get("checksum", ""),
                "package_path":  data.get("download_url", ""),  # full Render URL
                "min_version":   data.get("min_version", "0.0.0"),
                "manifest_data": data,
                "source":        "hq_server",
                "is_active":     True,
                "fetched_at":    timezone.now(),
                "file_count":    data.get("file_count", 0),
            },
        )

    return JsonResponse(data)


# ── Local upload ───────────────────────────────────────────────────────────────

@staff_member_required
@require_POST
def upload_package(request):
    """
    Accept a .zip update package uploaded directly from the dev laptop.
    The zip must contain manifest.json at root and a files/ directory.
    Used when Render is unreachable or you want to push a hotfix manually.
    """
    uploaded = request.FILES.get("package_file")
    if not uploaded:
        return JsonResponse({"error": "No file provided. Attach 'package_file' in the form."}, status=400)

    ext = os.path.splitext(uploaded.name)[1].lower()
    if ext not in (".zip", ".xz", ".gz"):
        return JsonResponse({"error": "Only .zip, .tar.xz, or .tar.gz files accepted."}, status=400)

    manifest = {}
    if ext == ".zip":
        try:
            with zipfile.ZipFile(uploaded, "r") as zf:
                if "manifest.json" not in zf.namelist():
                    return JsonResponse(
                        {"error": "manifest.json not found in zip. Build the package with build_package.py."},
                        status=400,
                    )
                manifest = json.loads(zf.read("manifest.json"))
        except zipfile.BadZipFile:
            return JsonResponse({"error": "Not a valid zip archive."}, status=400)
        except json.JSONDecodeError:
            return JsonResponse({"error": "manifest.json is not valid JSON."}, status=400)

    version = manifest.get("version") or request.POST.get("version", "").strip()
    if not version:
        return JsonResponse(
            {"error": "Could not determine version. Ensure manifest.json has a 'version' field."},
            status=400,
        )

    if UpdatePackage.objects.filter(version=version).exists():
        return JsonResponse(
            {"error": f"Version {version} already exists. Delete it first."},
            status=409,
        )

    # Compute checksum before saving
    uploaded.seek(0)
    h = hashlib.sha256()
    for chunk in uploaded.chunks():
        h.update(chunk)
    checksum  = h.hexdigest()
    file_size = uploaded.size
    uploaded.seek(0)

    pkg = UpdatePackage(
        version=version,
        changes=manifest.get("changes", ""),
        critical=manifest.get("critical", False),
        min_version=manifest.get("min_version", "0.0.0"),
        size_bytes=file_size,
        checksum=checksum,
        manifest_data=manifest,
        source="local_upload",
        is_active=True,
        uploaded_by=request.user,
        file_count=len(manifest.get("files", [])),
    )
    pkg.uploaded_file.save(uploaded.name, uploaded, save=False)
    pkg.save()

    return JsonResponse({
        "success":    True,
        "version":    version,
        "size_mb":    round(file_size / 1048576, 2),
        "checksum":   checksum,
        "file_count": pkg.file_count,
        "message":    f"Package v{version} uploaded. You can now apply it.",
    })


# ── Apply ──────────────────────────────────────────────────────────────────────

@staff_member_required
@require_POST
def apply_update(request, version):
    """
    Start applying an update in a background thread.
    The browser should then connect to /updates/stream/<version>/ for live progress.
    """
    with _active_lock:
        if version in _active_updates:
            return JsonResponse({"error": "Update already in progress."}, status=409)

    try:
        pkg = UpdatePackage.objects.get(version=version, is_active=True)
    except UpdatePackage.DoesNotExist:
        return JsonResponse({"error": f"Unknown version: {version}"}, status=404)

    history = UpdateHistory.objects.create(
        package=pkg,
        machine_id=_machine_id(),
        client_id=_machine_id(),
        status="downloading",
        applied_by=request.user,
    )

    progress_q = queue.Queue()
    with _active_lock:
        _active_updates[version] = progress_q

    cfg = _get_hq_config()

    def _run():
        # Local upload → use the file on disk directly
        # HQ server package → download from Render with auth header
        if pkg.source == "local_upload" and pkg.uploaded_file:
            package_source = pkg.uploaded_file.path
            is_local = True
            headers = {}
        else:
            package_source = pkg.package_path   # full Render download URL
            is_local = False
            headers = _hq_headers(cfg)          # X-Api-Key — required by Render

        updater = Updater(
            package_url=package_source,
            version=version,
            progress_queue=progress_q,
            expected_checksum=pkg.checksum,
            is_local_file=is_local,
            download_headers=headers,
        )
        try:
            updater.run()
            history.mark_success()

            # Update this machine's version record
            ClientMachine.objects.filter(machine_id=_machine_id()).update(
                current_version=version,
                last_update=timezone.now(),
            )
            # Record the successful update timestamp
            us = UpdateSettings.get_settings()
            us.last_successful_update = timezone.now()
            us.save()

        except Exception as exc:
            history.mark_failed(str(exc))
        finally:
            with _active_lock:
                _active_updates.pop(version, None)

    threading.Thread(target=_run, daemon=True).start()
    return JsonResponse({"started": True, "stream_url": f"/updates/stream/{version}/"})


# ── SSE progress stream ────────────────────────────────────────────────────────

@staff_member_required
@require_GET
def stream_progress(request, version):
    """Server-Sent Events — browser connects here for live update progress."""

    def _generate():
        # Wait up to 5 s for the background thread to register its queue
        for _ in range(50):
            with _active_lock:
                q = _active_updates.get(version)
            if q is not None:
                break
            time.sleep(0.1)
        else:
            yield 'event: error\ndata: {"message": "No active update found."}\n\n'
            return

        yield 'event: connected\ndata: {"message": "Connected to update stream."}\n\n'

        while True:
            try:
                msg = q.get(timeout=30)
                payload = json.dumps(msg["data"])
                yield f"event: {msg['event']}\ndata: {payload}\n\n"
                if msg["event"] in ("complete", "error"):
                    break
            except queue.Empty:
                yield ": heartbeat\n\n"

    resp = StreamingHttpResponse(_generate(), content_type="text/event-stream")
    resp["Cache-Control"]      = "no-cache"
    resp["X-Accel-Buffering"]  = "no"
    return resp


# ── Rollback ───────────────────────────────────────────────────────────────────

@staff_member_required
@require_POST
def rollback_update(request, version):
    q = queue.Queue()
    ok = rollback(version, q)
    messages = []
    try:
        while True:
            messages.append(q.get_nowait())
    except queue.Empty:
        pass
    return JsonResponse({"success": ok, "messages": messages})


# ── Delete package ─────────────────────────────────────────────────────────────

@staff_member_required
@require_POST
def delete_package(request, version):
    try:
        pkg = UpdatePackage.objects.get(version=version)
        pkg.is_active = False
        pkg.save()
        return JsonResponse({"success": True})
    except UpdatePackage.DoesNotExist:
        return JsonResponse({"error": f"Package v{version} not found."}, status=404)


# ── History JSON ───────────────────────────────────────────────────────────────

@staff_member_required
@require_GET
def update_history(request):
    history = UpdateHistory.objects.select_related("package").order_by("-started_at")[:50]
    data = [
        {
            "version":      h.package.version,
            "status":       h.status,
            "started_at":   h.started_at.isoformat(),
            "completed_at": h.completed_at.isoformat() if h.completed_at else None,
            "applied_by":   str(h.applied_by) if h.applied_by else "Auto",
            "error":        h.error_message or None,
        }
        for h in history
    ]
    return JsonResponse({"history": data})


# ── Broadcast dashboard ────────────────────────────────────────────────────────

@staff_member_required
@require_GET
def broadcast_dashboard(request):
    """Admin view: see all registered client machines and their update status."""
    cfg      = _get_hq_config()
    machines = ClientMachine.objects.all().order_by("-last_check")
    return render(request, "update/broadcast_dashboard.html", {
        "current_version": _current_version(),
        "server_url":      cfg["server_url"],
        "machines":        machines,
        "total_machines":  machines.count(),
        "up_to_date":      machines.filter(current_version=_current_version()).count(),
    })
