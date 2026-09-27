"""Backups can be copied to a second location off the data disk (core.backups)."""
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from core import backups


class BackupCopyTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.local = Path(self.tmp.name) / "local"
        self.offsite = Path(self.tmp.name) / "share"
        self.local.mkdir()
        self.offsite.mkdir()

    def _backup(self, stamp):
        path = self.local / f"db-{stamp}.pgdump"
        path.write_bytes(b"dump " + stamp.encode())
        return path

    def test_not_configured_does_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CIRQEN_BACKUP_COPY_DIR", None)
            with self.settings(BACKUP_COPY_DIR=""):
                self.assertIsNone(backups.copy_offsite(self._backup("20260101-000000-000001"), keep=3))

    def test_copy_lands_and_old_copies_are_pruned(self):
        with mock.patch.dict(os.environ, {"CIRQEN_BACKUP_COPY_DIR": str(self.offsite)}):
            for day in range(1, 6):
                backups.copy_offsite(self._backup(f"202601{day:02d}-120000-000000"), keep=3)
        names = sorted(p.name for p in self.offsite.iterdir())
        self.assertEqual(names, [f"db-202601{d:02d}-120000-000000.pgdump" for d in (3, 4, 5)])
        self.assertEqual((self.offsite / names[-1]).read_bytes(), b"dump 20260105-120000-000000")
        self.assertFalse(list(self.offsite.glob("*.partial")))

    def test_unmounted_location_is_reported(self):
        missing = Path(self.tmp.name) / "not-mounted"
        with mock.patch.dict(os.environ, {"CIRQEN_BACKUP_COPY_DIR": str(missing)}):
            with self.assertRaises(OSError):
                backups.copy_offsite(self._backup("20260101-000000-000002"), keep=3)
