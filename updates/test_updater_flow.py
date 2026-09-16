"""Update apply and rollback on disk (IMPROVEMENT_PLAN.md section 6).

Builds a real package (manifest + files + a deletion), applies it to a scratch
install directory, and checks the result, the automatic rollback when the
post-update health check fails, and a manual rollback afterwards.
Migrations and HQ reporting are stubbed; the PostgreSQL snapshot has its own
test in updates/tests_pg.
"""
import hashlib
import json
import queue
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from updates import updater


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class UpdaterFlowTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.install = root / "app"
        data = root / "data"
        for d in (self.install, data / "staging", data / "backups"):
            d.mkdir(parents=True)

        (self.install / "templates").mkdir()
        (self.install / "templates" / "page.html").write_text("old page")
        (self.install / "legacy.py").write_text("OLD = True\n")

        for name, value in {
            "BASE_DIR": self.install,
            "SENTINEL_FILE": self.install / ".restart_required",
            "UPDATE_STAGING": data / "staging",
            "UPDATE_BACKUPS": data / "backups",
            "STATE_FILE": data / "state.json",
            "LOCK_FILE": data / "update.lock",
        }.items():
            patcher = mock.patch.object(updater, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for method in ("_run_migrations", "_report"):
            patcher = mock.patch.object(updater.Updater, method)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.package = root / "update-1.6.0.zip"
        new_page, new_module = b"new page", b"NEW = True\n"
        manifest = {
            "version": "1.6.0",
            "files": [
                {"path": "templates/page.html", "sha256": _sha(new_page), "type": "template"},
                {"path": "core/new_module.py", "sha256": _sha(new_module), "type": "backend"},
            ],
            "deletions": ["legacy.py"],
        }
        with zipfile.ZipFile(self.package, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("files/templates/page.html", new_page)
            zf.writestr("files/core/new_module.py", new_module)

    def _run(self, healthy=True):
        with mock.patch.object(updater.Updater, "_health_check", return_value=healthy):
            updater.Updater(str(self.package), "1.6.0", queue.Queue(), is_local_file=True).run()

    def _assert_original(self):
        self.assertEqual((self.install / "templates" / "page.html").read_text(), "old page")
        self.assertEqual((self.install / "legacy.py").read_text(), "OLD = True\n")

    def test_apply_replaces_adds_and_deletes_files(self):
        self._run(healthy=True)
        self.assertEqual((self.install / "templates" / "page.html").read_text(), "new page")
        self.assertTrue((self.install / "core" / "new_module.py").exists())
        self.assertFalse((self.install / "legacy.py").exists())
        self.assertTrue(updater.SENTINEL_FILE.exists())
        self.assertFalse(updater.LOCK_FILE.exists())

    def test_failed_health_check_rolls_back_automatically(self):
        self._run(healthy=False)
        self._assert_original()

    def test_manual_rollback_restores_the_previous_version(self):
        self._run(healthy=True)
        self.assertTrue(updater.rollback("1.6.0"))
        self._assert_original()

    def test_tampered_package_is_refused_before_touching_files(self):
        with zipfile.ZipFile(self.package, "a") as zf:
            zf.writestr("files/templates/page.html", b"tampered")  # checksum no longer matches
        self._run(healthy=True)
        self._assert_original()
