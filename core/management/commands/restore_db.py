from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core import backups
from updates.db_snapshot import SnapshotError


class Command(BaseCommand):
    help = ("Restore the local database from a backup file. A pre-restore backup is "
            "taken first; the restore is all-or-nothing.")

    def add_arguments(self, parser):
        parser.add_argument("backup", help="Backup file name (from backup_db --list) or path.")
        parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")

    def handle(self, *args, backup, yes, **options):
        path = Path(backup)
        if not path.exists():
            path = backups.backup_dir() / backup
        if not path.exists():
            raise CommandError(f"Backup not found: {backup}")
        if not yes:
            answer = input(f"Replace the local database with {path.name}? Stop the app first. [yes/no] ")
            if answer.strip().lower() != "yes":
                raise CommandError("Cancelled.")
        try:
            safety = backups.restore_backup(path)
        except SnapshotError as exc:
            raise CommandError(f"Restore failed, database unchanged: {exc}")
        self.stdout.write(self.style.SUCCESS(f"Restored {path.name}. Previous state saved as {safety.name}."))
