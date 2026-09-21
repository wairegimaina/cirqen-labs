#!/usr/bin/env python3
"""
Patch ~/.cirqen/data/config.json so hq_db points at the current HQ database
(config.HQ_ENDPOINT_DEFAULTS) instead of the old Render-managed Postgres
instance. Only touches the hq_db block; everything else (local_db, redis,
update, email, sync_tables, etc.) is left exactly as-is.

Mostly superseded: the app now migrates an old config.json itself on its next
start. Use this only to write the hq_db password from the environment.
"""

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import HQ_ENDPOINT_DEFAULTS as _HQ  # noqa: E402  the one place addresses live

config_path = Path.home() / ".cirqen" / "data" / "config.json"

if not config_path.exists():
    raise SystemExit(f"Config file not found at {config_path}")

# Back up the original before touching anything
backup_path = config_path.with_suffix(".json.bak")
shutil.copy2(config_path, backup_path)
print(f"Backed up original to {backup_path}")

with open(config_path) as f:
    cfg = json.load(f)

old_hq_db = cfg.get("hq_db", {})
print(f"Old hq_db.host: {old_hq_db.get('host')}")

cfg["hq_db"] = {
    "host": _HQ["hq_db.host"],
    "port": _HQ["hq_db.port"],
    "database": _HQ["hq_db.database"],
    "user": _HQ["hq_db.user"],
    "password": os.environ["POSTGRES_HQ_PASSWORD"],  # never commit the value
    "sslmode": _HQ["hq_db.sslmode"],
    "enabled": True,
}

# Normalize the email block to match config.py's DEFAULT_CONFIG shape
# (adds host/port/use_tls; keeps your existing host_user/host_password as-is)
old_email = cfg.get("email", {})
cfg["email"] = {
    "host": old_email.get("host", "smtp.gmail.com"),
    "port": old_email.get("port", 587),
    "use_tls": old_email.get("use_tls", True),
    "host_user": old_email.get("host_user", ""),
    "host_password": old_email.get("host_password", ""),
}

with open(config_path, "w") as f:
    json.dump(cfg, f, indent=4)

print(f"New hq_db.host: {cfg['hq_db']['host']}")
print(f"Email host: {cfg['email']['host']}:{cfg['email']['port']} (TLS={cfg['email']['use_tls']})")
print(f"Updated {config_path}")
