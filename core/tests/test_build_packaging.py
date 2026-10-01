"""build.py packaging: neutral archive by default, a hospital's archive with
its admin-panel installer file, and never the old shared sync key."""

import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import build

PANEL_FILE = {
    "sync": {"hospital_code": "CH0002", "enrollment_code": "cqe1.token",
             "api_url": "https://hq-ch0002.cirqenlabs.com/api/sync"},
    "update": {"api_key": "update-key"},
}


def _names(archive):
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            return z.namelist()
    with tarfile.open(archive) as t:
        return t.getnames()


class PackagingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dist = self.root / "dist"
        (self.dist / "Cirqen").mkdir(parents=True)
        (self.dist / "Cirqen" / "Cirqen").write_text("app")
        (self.root / "version.txt").write_text("1.6.0\n")
        for name, value in (("DIST_DIR", self.dist), ("PROJECT_ROOT", self.root),
                            ("PROVISIONING_FILE", None)):
            patcher = mock.patch.object(build, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write(self, data, name="provisioning-ch0002.json"):
        path = self.root / name
        path.write_text(json.dumps(data))
        return path

    def _archives(self):
        return sorted(p for p in self.dist.iterdir() if p.is_file())

    def test_neutral_package_has_no_installer_file(self):
        # A provisioning.json left at the project root (the old format) is
        # not picked up any more.
        self._write({"sync": {"auth_token": "shared"}}, "provisioning.json")
        (self.dist / "Cirqen" / "provisioning.json").write_text("{}")
        self.assertTrue(build.package_distribution())
        [archive] = self._archives()
        self.assertIn("v1.6.0", archive.name)
        self.assertNotIn("Cirqen/provisioning.json", _names(archive))
        self.assertFalse((self.dist / "Cirqen" / "provisioning.json").exists())

    def test_hospital_package_carries_its_file_and_leaves_dist_clean(self):
        build.PROVISIONING_FILE = self._write(PANEL_FILE)
        self.assertTrue(build.package_distribution())
        [archive] = self._archives()
        self.assertTrue(archive.name.startswith("Cirqen_"))
        self.assertIn("_v1.6.0_CH0002", archive.name)
        self.assertIn("Cirqen/provisioning.json", _names(archive))
        self.assertFalse((self.dist / "Cirqen" / "provisioning.json").exists())

    def test_shared_sync_key_is_refused(self):
        bad = {"sync": dict(PANEL_FILE["sync"], auth_token="shared")}
        build.PROVISIONING_FILE = self._write(bad)
        self.assertFalse(build.package_distribution())
        self.assertEqual(self._archives(), [])

    def test_file_without_enrollment_code_is_refused(self):
        with self.assertRaisesRegex(ValueError, "enrollment_code"):
            build.load_panel_provisioning(self._write({"sync": {"hospital_code": "CH0002"}}))

    def test_file_without_hospital_code_is_refused(self):
        with self.assertRaisesRegex(ValueError, "hospital_code"):
            build.load_panel_provisioning(self._write({"sync": {"enrollment_code": "t"}}))

    def test_unreadable_file_is_refused(self):
        path = self.root / "broken.json"
        path.write_text("{not json")
        with self.assertRaisesRegex(ValueError, "cannot read"):
            build.load_panel_provisioning(path)

    def test_the_stamp_step_puts_the_runtime_id_and_version_into_the_app(self):
        (self.dist / "Cirqen" / "_internal").mkdir()
        (self.root / "runtime.json").write_text('{"python": "3.14"}')
        (self.root / "requirements.txt").write_text("django\n")
        self.assertTrue(build.stamp_runtime_id())
        internal = self.dist / "Cirqen" / "_internal"
        self.assertEqual((internal / "version.txt").read_text(), "1.6.0")
        self.assertEqual((self.dist / "Cirqen" / "version.txt").read_text(), "1.6.0")
        self.assertEqual(len((internal / "runtime_id.txt").read_text().strip()), 16)

    def test_an_ubuntu_postgresql_is_laid_out_to_find_its_own_files(self):
        if build.IS_WINDOWS:
            self.skipTest("Linux layout")
        pg = self.root / "runtime" / "postgresql"
        (pg / "bin").mkdir(parents=True)
        (pg / "lib").mkdir()
        (pg / "share" / "extension").mkdir(parents=True)
        (pg / "share" / "postgres.bki").write_text("bki")
        (pg / "lib" / "plpgsql.so").write_text("so")
        config = pg / "bin" / "pg_config"
        config.write_text("#!/bin/sh\necho /usr/share/postgresql/18\n")
        config.chmod(0o755)
        with mock.patch.object(build, "RUNTIME_DIR", self.root / "runtime"):
            self.assertTrue(build.relocatable_postgres_layout(pg))
            self.assertTrue(build.relocatable_postgres_layout(pg))          # again: nothing changes
        self.assertTrue((pg / "bin").is_symlink())
        self.assertTrue((pg / "bin" / "pg_config").is_file())            # what the app calls still works
        self.assertTrue((pg / "lib" / "postgresql" / "18" / "bin" / "pg_config").is_file())
        self.assertTrue((pg / "lib" / "postgresql" / "18" / "lib" / "plpgsql.so").is_file())
        self.assertTrue((pg / "share" / "postgresql" / "18" / "postgres.bki").is_file())
        self.assertTrue((pg / "share" / "postgresql" / "18" / "extension").is_dir())
