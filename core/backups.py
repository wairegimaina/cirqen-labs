"""Local database backups (IMPROVEMENT_PLAN.md section 9).

Backups are pg_dumps of the local database's public schema, written to
<data dir>/backups/db-YYYYmmdd-HHMMSS-ffffff.pgdump. Restoring replays one inside a
single transaction (updates.db_snapshot), and first takes a "pre-restore"
backup so a restore of the wrong file can itself be undone.
"""
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from django.conf import settings

from updates import db_snapshot

PREFIX = "db-"
SUFFIX = ".pgdump"


def backup_dir():
    directory = Path(getattr(settings, "DATA_PATH", Path.home() / ".cirqen" / "data")) / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def local_database():
    return settings.DATABASES["default"]


def create_backup(label=""):
    # Microseconds: a pre-restore backup taken in the same second as the backup
    # being restored must not overwrite it.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    name = f"{PREFIX}{stamp}{('-' + label) if label else ''}{SUFFIX}"
    return db_snapshot.dump(local_database(), backup_dir() / name)


def list_backups(directory=None):
    """Newest first."""
    return sorted((directory or backup_dir()).glob(f"{PREFIX}*{SUFFIX}"), reverse=True)


logger = logging.getLogger(__name__)


def copy_dir():
    """A second location for backups, off this machine's data disk.

    CIRQEN_BACKUP_COPY_DIR, or backups.copy_dir in config.json: typically a
    mounted network share, NAS or USB disk on a hospital server. None when not
    configured. A backup on the same disk as the database is lost with it.
    """
    configured = os.getenv("CIRQEN_BACKUP_COPY_DIR") or getattr(settings, "BACKUP_COPY_DIR", "")
    return Path(configured) if configured else None


def copy_offsite(path, keep):
    """Copy ``path`` to copy_dir() and prune old copies there.

    Returns the copy's path, or None when no copy location is configured.
    Raises OSError when the location is configured but unwritable, so the
    caller can report it; the local backup is unaffected either way.
    """
    target_dir = copy_dir()
    if target_dir is None:
        return None
    if not target_dir.is_dir():
        raise OSError(f"Backup copy location {target_dir} does not exist or is not mounted")
    copy = target_dir / Path(path).name
    partial = copy.with_suffix(copy.suffix + ".partial")
    shutil.copy2(path, partial)
    partial.replace(copy)  # never leave a half-written file under the real name
    prune(keep, directory=target_dir)
    return copy


def prune(keep, directory=None):
    """Delete all but the newest ``keep`` scheduled backups; pre-restore ones are kept."""
    scheduled = [p for p in list_backups(directory) if "-pre-restore" not in p.name]
    removed = scheduled[keep:]
    for path in removed:
        path.unlink(missing_ok=True)
    return removed


def restore_backup(path):
    """Restore ``path``; returns the safety backup taken just before."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    safety = create_backup(label="pre-restore")
    db_snapshot.restore(local_database(), path)
    return safety


def keep_count():
    """How many scheduled backups to keep: CIRQEN_BACKUP_KEEP / backups.keep,
    default 14 (one a day). Hospital servers set 30."""
    return int(getattr(settings, "BACKUP_KEEP", 14) or 14)


class BackupCheckFailed(Exception):
    """The newest backup is missing, too old, or unreadable."""


def verify_latest(max_age_hours=36):
    """Check the newest backup can be read back, without restoring it.

    ``pg_restore --list`` reads the whole archive's table of contents, which
    fails on a truncated or corrupt file; the list must name the core tables.
    Returns a summary dict; raises BackupCheckFailed with the reason.
    """
    import subprocess
    import time

    from updates.db_snapshot import find_pg_tool

    latest = next(iter(list_backups()), None)
    if latest is None:
        raise BackupCheckFailed("No backup exists yet.")
    age_hours = (time.time() - latest.stat().st_mtime) / 3600
    if age_hours > max_age_hours:
        raise BackupCheckFailed(f"The newest backup, {latest.name}, is {age_hours:.0f} hours old.")
    result = subprocess.run([find_pg_tool("pg_restore"), "--list", str(latest)],
                            capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise BackupCheckFailed(f"{latest.name} cannot be read: {result.stderr.strip()[:300]}")
    tables = set()
    for line in result.stdout.splitlines():
        # "<id>; <oid> <oid> TABLE DATA <schema> <table> <owner>"
        words = line.split()
        if "TABLE" in words and words[words.index("TABLE") + 1:words.index("TABLE") + 2] == ["DATA"]:
            at = words.index("TABLE") + 2
            if len(words) > at + 1:
                tables.add(words[at + 1])
    required = {"Inventory_equipment", "jobcard_jobcard", "CalSoft_calibrationsession", "users_userprofile"}
    missing = sorted(required - tables)
    if missing:
        raise BackupCheckFailed(f"{latest.name} has no data for: {', '.join(missing)}")
    return {"file": latest.name, "age_hours": round(age_hours, 1), "tables": len(tables),
            "size": latest.stat().st_size}
