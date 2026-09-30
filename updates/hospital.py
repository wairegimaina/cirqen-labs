"""This PC's hospital code (config sync.hospital_code), for the update server:
it offers each hospital the release chosen for it in the admin panel."""
from django.conf import settings


def hospital_code() -> str:
    config = getattr(settings, "CIRQEN_CONFIG", None)
    try:
        return str(config.get("sync.hospital_code") or "").strip().upper() if config else ""
    except Exception:  # noqa: BLE001 - never block an update check on this
        return ""


def latest_params(current_version: str, machine_id: str) -> dict:
    params = {"current_version": current_version, "machine_id": machine_id}
    code = hospital_code()
    if code:
        params["hospital_code"] = code
    return params
