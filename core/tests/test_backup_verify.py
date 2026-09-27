"""The daily backup check: the newest backup is recent and readable."""
import os
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from core import backups

LISTING = "\n".join(
    f"{n}; 0 {n} TABLE DATA public {name} cirqen" for n, name in enumerate(
        ["Inventory_equipment", "jobcard_jobcard", "CalSoft_calibrationsession", "users_userprofile"], 1))


class BackupVerifyTests(SimpleTestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)
        override = override_settings(DATA_PATH=self.data)
        override.enable()
        self.addCleanup(override.disable)

    def _backup(self, hours_old=1):
        path = Path(self.data) / "backups" / "db-20260926-123000-000000.pgdump"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"PGDMP fake")
        stamp = time.time() - hours_old * 3600
        os.utime(path, (stamp, stamp))
        return path

    def _pg_restore(self, returncode=0, stdout=LISTING, stderr=""):
        return mock.patch("subprocess.run", return_value=SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr))

    def test_a_recent_readable_backup_passes(self):
        self._backup()
        with self._pg_restore(), mock.patch("updates.db_snapshot.find_pg_tool", return_value="pg_restore"):
            summary = backups.verify_latest()
        self.assertEqual(summary["tables"], 4)

    def test_no_backup_fails(self):
        with self.assertRaisesRegex(backups.BackupCheckFailed, "No backup"):
            backups.verify_latest()

    def test_an_old_backup_fails(self):
        self._backup(hours_old=50)
        with self.assertRaisesRegex(backups.BackupCheckFailed, "50 hours old"):
            backups.verify_latest()

    def test_an_unreadable_backup_fails(self):
        self._backup()
        with self._pg_restore(returncode=1, stdout="", stderr="input file appears to be truncated"), \
                mock.patch("updates.db_snapshot.find_pg_tool", return_value="pg_restore"):
            with self.assertRaisesRegex(backups.BackupCheckFailed, "truncated"):
                backups.verify_latest()

    def test_a_backup_missing_core_tables_fails(self):
        self._backup()
        with self._pg_restore(stdout=LISTING.split("\n")[0]), \
                mock.patch("updates.db_snapshot.find_pg_tool", return_value="pg_restore"):
            with self.assertRaisesRegex(backups.BackupCheckFailed, "jobcard_jobcard"):
                backups.verify_latest()

    @override_settings(BACKUP_KEEP=30)
    def test_retention_follows_the_setting(self):
        self.assertEqual(backups.keep_count(), 30)
