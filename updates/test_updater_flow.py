"""Update apply and rollback on disk (IMPROVEMENT_PLAN.md section 6).

Builds a real, SIGNED package (manifest + signature + files + a deletion),
applies it to a scratch install directory, and checks the result, the
automatic rollback when the post-update health check fails, and a manual
rollback afterwards. Migrations and HQ reporting are stubbed; the PostgreSQL
snapshot has its own test in updates/tests_pg.

Packages are signed here with a throwaway key because the shipped build now
carries a real UPDATE_PUBLIC_KEY, so an unsigned package is refused — which is
the point of signing, and is covered by its own test below.
"""
import base64
import hashlib
import json
import queue
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from updates import updater


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _throwaway_keypair():
    """A signing key for tests only; never the one the fleet trusts."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public_b64 = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode()
    return private, public_b64


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

        self.private, self.public_b64 = _throwaway_keypair()
        signing = override_settings(UPDATE_SYSTEM={"public_key": self.public_b64})
        signing.enable()
        self.addCleanup(signing.disable)

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
        manifest_bytes = json.dumps(manifest).encode()
        with zipfile.ZipFile(self.package, "w") as zf:
            zf.writestr("manifest.json", manifest_bytes)
            zf.writestr("manifest.sig", base64.b64encode(self.private.sign(manifest_bytes)).decode())
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

    def test_unsigned_package_is_refused(self):
        """The whole point of C-3: once a key is configured, an unsigned
        package must not be applied, however well-formed it is."""
        with zipfile.ZipFile(self.package, "r") as src:
            entries = {n: src.read(n) for n in src.namelist() if n != "manifest.sig"}
        with zipfile.ZipFile(self.package, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        self._run(healthy=True)
        self._assert_original()

    def test_package_signed_by_another_key_is_refused(self):
        """A package from a server that is not ours — the fleet-takeover case."""
        attacker, _ = _throwaway_keypair()
        with zipfile.ZipFile(self.package, "r") as src:
            entries = {n: src.read(n) for n in src.namelist()}
        entries["manifest.sig"] = base64.b64encode(
            attacker.sign(entries["manifest.json"])).decode().encode()
        with zipfile.ZipFile(self.package, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        self._run(healthy=True)
        self._assert_original()
