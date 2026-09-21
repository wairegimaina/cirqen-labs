from django.core.management.base import BaseCommand, CommandError

from core import backups
from updates.db_snapshot import SnapshotError


class Command(BaseCommand):
    help = "Back up the local database to <data dir>/backups and prune old backups."

    def add_arguments(self, parser):
        parser.add_argument("--keep", type=int, default=14,
                            help="How many scheduled backups to keep (default 14).")
        parser.add_argument("--list", action="store_true", dest="list_only",
                            help="List backups instead of creating one.")

    def handle(self, *args, keep, list_only, **options):
        if list_only:
            for path in backups.list_backups():
                self.stdout.write(f"{path.name}\t{path.stat().st_size:,} bytes")
            return
        try:
            path = backups.create_backup()
        except SnapshotError as exc:
            raise CommandError(f"Backup failed: {exc}")
        removed = backups.prune(keep)
        self.stdout.write(self.style.SUCCESS(f"Backup written: {path}"))
        if removed:
            self.stdout.write(f"Removed {len(removed)} old backup(s).")
