"""The update server re-signs a package that was built before it had a key.

A package is built once per version and kept on the server's disk. One built
while HQ_SIGNING_PRIVATE_KEY was unset stayed unsigned after the key was added,
and every desktop that ships the public key refused it on every attempt.
"""
import base64
import importlib.util
import json
import os
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase

_spec = importlib.util.spec_from_file_location(
    "hq_build_package", Path(settings.BASE_DIR) / "hq_server" / "build_package.py")
build_package = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_package)


def _seed_b64():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    return base64.b64encode(seed).decode()


class PackageSigningTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.packages = Path(tmp.name) / "packages"
        self.repo = Path(tmp.name) / "repo"
        self.packages.mkdir()
        (self.repo / "core").mkdir(parents=True)
        (self.repo / "core" / "module.py").write_text("X = 1\n")

    def _build(self, key=""):
        with mock.patch.dict(os.environ, {"HQ_SIGNING_PRIVATE_KEY": key}):
            return build_package.build_package_if_needed("9.9.9", self.packages, self.repo)

    def _zip_is_signed(self):
        with zipfile.ZipFile(self.packages / "cirqen_update_v9.9.9.zip") as zf:
            return "manifest.sig" in zf.namelist()

    def test_unsigned_package_is_rebuilt_once_a_key_is_set(self):
        self.assertTrue(self._build()["built"])
        self.assertFalse(self._zip_is_signed())

        result = self._build(_seed_b64())
        self.assertTrue(result["built"])
        self.assertTrue(self._zip_is_signed())
        meta = json.loads((self.packages / "cirqen_update_v9.9.9.json").read_text())
        self.assertTrue(meta["signed"])

    def test_signed_package_is_not_rebuilt(self):
        key = _seed_b64()
        self._build(key)
        self.assertFalse(self._build(key)["built"])

    def test_unsigned_package_is_kept_while_there_is_no_key(self):
        self._build()
        self.assertFalse(self._build()["built"])
