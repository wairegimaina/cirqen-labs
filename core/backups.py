"""Local database backups (IMPROVEMENT_PLAN.md section 9).

Backups are pg_dumps of the local database's public schema, written to
<data dir>/backups/db-YYYYmmdd-HHMMSS-ffffff.pgdump. Restoring replays one inside a
single transaction (updates.db_snapshot), and first takes a "pre-restore"
backup so a restore of the wrong file can itself be undone.
"""
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


def list_backups():
    """Newest first."""
    return sorted(backup_dir().glob(f"{PREFIX}*{SUFFIX}"), reverse=True)


def prune(keep):
    """Delete all but the newest ``keep`` scheduled backups; pre-restore ones are kept."""
    scheduled = [p for p in list_backups() if "-pre-restore" not in p.name]
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
