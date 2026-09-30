"""Is this PC still waiting for the HOD's approval? Written by the sync agent
(sync_agent_4.refresh_device_status) into the data folder; read here for the
notice at the top of every page."""
import json

from django.conf import settings


def waiting_for_approval() -> bool:
    try:
        data = json.loads((settings.DATA_PATH / "device_status.json").read_text())
    except (OSError, ValueError, TypeError):
        return False
    return bool(data.get("pending_approval"))
