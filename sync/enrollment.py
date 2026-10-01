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
    hospital = str(config_manager.get("sync.hospital_code") or "").strip().upper()
    if hospital:
        # The installer's token goes only to an HQ that proves it is this
        # PC's hospital (hq_handshake), never to whatever answers the address.
        import hq_handshake

        ok, why = hq_handshake.confirm(f"{hq_base_url.rstrip('/')}/api/sync", hospital)
        if not ok:
            LOG.error("Not enrolling: %s", why)
            return None
    key = enroll(hq_base_url, client_id, config_manager.get("client.name") or client_id, code)
    if key:
        config_manager.set("sync.auth_token", key)
        config_manager.set("sync.enrollment_code", "")
        config_manager.set("sync.key_own", True)      # enrolled: never the shared key
        LOG.info("Enrolled with HQ as %s; key saved to config.json", client_id)
        forget_token_in_provisioning(getattr(config_manager, "data_path", None), code)
    return key


def rekey(hq_base_url, client_id, client_name, shared_key, hospital_code="", session=None):
    """Swap the old shared sync key for this PC's own (HQ's /api/sync/rekey).

    Returns (key, done): key is the new key or None; done is True when there
    is nothing more to do (swapped, or HQ says this PC already has its own).
    """
    http = session or requests
    headers = {"X-API-Key": shared_key, "X-Client-ID": client_id,
               "User-Agent": f"CMMS-Sync-Agent/rekey (Client-ID: {client_id})"}
    if hospital_code:
        headers["X-Cirqen-Hospital"] = hospital_code
    try:
        response = http.post(f"{hq_base_url.rstrip('/')}/api/sync/rekey",
                             json={"client_id": client_id, "client_name": client_name},
                             headers=headers, timeout=ENROLL_TIMEOUT)
    except requests.RequestException as exc:
        LOG.warning("Key swap deferred, HQ unreachable: %s", exc)
        return None, False
    if response.status_code == 200:
        return response.json().get("api_key"), True
    if response.status_code == 400:
        return None, True            # this key is already this PC's own
    if response.status_code == 404:
        LOG.info("HQ cannot swap keys yet (older HQ); will try again next start")
    elif response.status_code == 409:
        LOG.error("HQ already has a key for %s but this PC holds the old shared key; "
                  "ask Cirqen support to re-issue its key.", client_id)
    else:
        LOG.warning("Key swap refused by HQ (%s): %s", response.status_code, response.text[:200])
    return None, False


def ensure_own_key(config_manager, hq_base_url, client_id):
    """A PC installed with the old shared sync key swaps it for its own, once.

    Runs at every agent start until done; afterwards sync.key_own is set and
    nothing is sent. Returns the new key, or None if nothing changed.
    """
    if config_manager.get("sync.key_own"):
        return None
    current = config_manager.get("sync.auth_token")
    if not current:
        return None
    hospital = str(config_manager.get("sync.hospital_code") or "").strip().upper()
    key, done = rekey(hq_base_url, client_id, config_manager.get("client.name") or client_id, current, hospital)
    if key:
        replace = getattr(config_manager, "replace_secret", None)
        (replace or config_manager.set)("sync.auth_token", key)
        LOG.info("Swapped the old shared sync key for this PC's own key; saved to config.json")
        forget_shared_key_in_provisioning(getattr(config_manager, "data_path", None), current)
    if done:
        config_manager.set("sync.key_own", True)
    return key


def ensure_own_key_for_data_path(data_path, hq_base_url, client_id):
    try:
        from config import load_config
    except ImportError:
        from .config import load_config
    config_manager = load_config(Path(data_path) if data_path else Path.home() / ".cmms")
    return ensure_own_key(config_manager, hq_base_url, client_id)


def forget_shared_key_in_provisioning(data_path, shared_key):
    """Remove the old shared key from the provisioning.json files this PC reads."""
    _scrub_provisioning(data_path, "auth_token", shared_key)


def forget_token_in_provisioning(data_path, code):
    """Remove the used enrollment token from every provisioning.json this PC
    reads, so a copy of this PC's files cannot enroll another computer. The
    rest of the file (hospital code, addresses) stays."""
    _scrub_provisioning(data_path, "enrollment_code", code)


def _scrub_provisioning(data_path, field, value):
    import json

    try:
        from config import _provisioning_candidates_for
    except ImportError:
        return
    for path in _provisioning_candidates_for(Path(data_path) if data_path else Path.home() / ".cmms"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        sync = data.get("sync") if isinstance(data, dict) else None
        if not isinstance(sync, dict) or not value or sync.get(field) != value:
            continue
        sync[field] = ""
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(path)
            LOG.info("Removed sync.%s from %s", field, path)
        except OSError as exc:
            LOG.warning("Could not remove sync.%s from %s (%s); delete that file by hand", field, path, exc)


def ensure_client_key_for_data_path(data_path, hq_base_url, client_id):
    try:
        from config import load_config
    except ImportError:
        from .config import load_config
    config_manager = load_config(Path(data_path) if data_path else Path.home() / ".cmms")
    return ensure_client_key(config_manager, hq_base_url, client_id)
