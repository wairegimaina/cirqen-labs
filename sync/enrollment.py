"""First-run enrollment: trade the installer's code for this client's own key.

Installers carry an enrollment code (provisioning.json / SYNC_ENROLLMENT_CODE),
not a sync key. On first start the agent posts the code and its client_id to
HQ's /api/sync/enroll; HQ returns a key bound to that client_id, which is saved
to config.json and the code is cleared. See hq_server security_layer.py.
"""
import logging
from pathlib import Path

import requests

LOG = logging.getLogger("sync.enrollment")

ENROLL_TIMEOUT = 15


def enroll(hq_base_url, client_id, client_name, enrollment_code, session=None):
    """Return the issued key, or None (logged) if HQ refused or was unreachable."""
    http = session or requests
    try:
        response = http.post(
            f"{hq_base_url.rstrip('/')}/api/sync/enroll",
            json={"client_id": client_id, "client_name": client_name,
                  "enrollment_code": enrollment_code},
            timeout=ENROLL_TIMEOUT,
        )
    except requests.RequestException as exc:
        LOG.warning("Enrollment deferred, HQ unreachable: %s", exc)
        return None
    if response.status_code == 200:
        return response.json().get("api_key")
    if response.status_code == 409:
        LOG.error("HQ says client %s is already enrolled; ask HQ to re-issue its key "
                  "(POST /api/admin/generate_api_key with replace=true).", client_id)
    else:
        LOG.error("Enrollment refused by HQ (%s): %s", response.status_code, response.text[:200])
    return None


def ensure_client_key(config_manager, hq_base_url, client_id):
    """If no sync key is configured but an enrollment code is, enroll and persist.

    Returns the key to use (existing or new), or None.
    """
    existing = config_manager.get("sync.auth_token")
    if existing:
        return existing
    code = config_manager.get("sync.enrollment_code")
    if not code:
        return None
    key = enroll(hq_base_url, client_id, config_manager.get("client.name") or client_id, code)
    if key:
        config_manager.set("sync.auth_token", key)
        config_manager.set("sync.enrollment_code", "")
        LOG.info("Enrolled with HQ as %s; key saved to config.json", client_id)
    return key


def ensure_client_key_for_data_path(data_path, hq_base_url, client_id):
    try:
        from config import load_config
    except ImportError:
        from .config import load_config
    config_manager = load_config(Path(data_path) if data_path else Path.home() / ".cmms")
    return ensure_client_key(config_manager, hq_base_url, client_id)
