"""Full-app updates for a release on another runtime (runtime_id.py,
bulider_tools/full_update.py): the runtime id, preparing the new app next to
the old one, and the swap script, including going back when it doesn't start."""
import base64
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from unittest import TestCase, skipUnless

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import runtime_id
from bulider_tools import full_update

ROOT = Path(__file__).resolve().parents[2]
SIGNER = Ed25519PrivateKey.generate()


def fake_app_archive(runtime: str, version: str, start_script: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text, mode in (("Cirqen/_internal/runtime_id.txt", runtime + "\n", 0o644),
                                 ("Cirqen/_internal/version.txt", version, 0o644),
                                 ("Cirqen/start_cirqen.sh", start_script, 0o755)):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def offer_for(archive: bytes, runtime="rt-new", key=SIGNER):
    package = {"version": "2.0.0", "platform": "linux", "runtime_id": runtime, "file": "Cirqen_linux_v2.0.0.tar.gz",
               "sha256": hashlib.sha256(archive).hexdigest(), "size": len(archive)}
    document = json.dumps(package, sort_keys=True, separators=(",", ":"))
    return {"document": document, "download_url": "https://control.example/api/updates/full/2.0.0/linux/",
            "signature": base64.b64encode(key.sign(full_update.CONTEXT + document.encode())).decode()}


class RuntimeIdTests(TestCase):
    def test_it_changes_with_what_a_code_update_cannot_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runtime.json").write_text('{"python": "3.14", "postgres": "16.1", "_comment": "x"}')
            (root / "requirements.txt").write_text("django==5.2\n")
            (root / "main.py").write_text("print('hi')\n")
            first = runtime_id.fingerprint(root)
            (root / "runtime.json").write_text('{"python": "3.14", "postgres": "16.1", "_comment": "changed"}')
            self.assertEqual(runtime_id.fingerprint(root), first)            # comments don't count
            (root / "requirements.txt").write_text("django==5.3\n")
            self.assertNotEqual(runtime_id.fingerprint(root), first)
            (root / "_internal").mkdir()
            (root / "_internal" / runtime_id.STAMP_FILE).write_text("abc\n")
            self.assertEqual(runtime_id.installed(root), "abc")

    def test_the_repo_declares_the_databases_the_pcs_run(self):
        # Pilot PCs' databases are PostgreSQL 18; another major can't open them.
        declared = runtime_id.declared(ROOT)
        self.assertEqual((declared["postgres"], declared["redis"]), ("18", "8"))


class PrepareTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = self.root / "Cirqen"
        self.app.mkdir()
        (self.app / "provisioning.json").write_text('{"sync": {"hospital_code": "CH0001"}}')
        self.archive = fake_app_archive("rt-new", "2.0.0", "#!/bin/bash\n")
        self.downloads = 0

    def download(self, payload):
        def fetch(url, dest, progress):
            self.downloads += 1
            Path(dest).write_bytes(payload)
        return fetch

    def prepare(self, offer, payload=None):
        return full_update.prepare(offer, app_path=self.app, staging=self.root / "staging",
                                   public_key=SIGNER.public_key(), download=self.download(payload or self.archive))

    def test_the_new_app_is_unpacked_next_to_the_old_with_this_install_s_files(self):
        package = self.prepare(offer_for(self.archive))
        new_dir = Path(package["new_dir"])
        self.assertEqual(new_dir, self.root / "Cirqen.new")
        self.assertEqual((new_dir / "_internal" / "runtime_id.txt").read_text().strip(), "rt-new")
        self.assertIn("CH0001", (new_dir / "provisioning.json").read_text())
        self.assertTrue((new_dir / "start_cirqen.sh").stat().st_mode & 0o100)       # still executable
        self.assertTrue((self.app / "provisioning.json").exists())                    # old app untouched

    def test_an_offer_control_did_not_sign_is_refused(self):
        with self.assertRaisesRegex(full_update.FullUpdateError, "not signed"):
            self.prepare(offer_for(self.archive, key=Ed25519PrivateKey.generate()))
        self.assertEqual(self.downloads, 0)

    def test_a_tampered_download_is_refused(self):
        with self.assertRaisesRegex(full_update.FullUpdateError, "checksum"):
            self.prepare(offer_for(self.archive), payload=self.archive + b"x")
        self.assertFalse((self.root / "Cirqen.new").exists())

    def test_an_app_built_for_another_runtime_is_refused(self):
        other = fake_app_archive("rt-other", "2.0.0", "#!/bin/bash\n")
        with self.assertRaisesRegex(full_update.FullUpdateError, "runtime"):
            self.prepare(offer_for(other, runtime="rt-new"), payload=other)
        self.assertFalse((self.root / "Cirqen.new").exists())


@skipUnless(full_update.supported(), "the swap script is for Linux")
class SwapScriptTests(TestCase):
    """The real script, with bash: swap, start, and go back if it doesn't start."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()

    def make_app(self, path: Path, label: str, confirms: bool):
        (path / "_internal").mkdir(parents=True)
        (path / "label").write_text(label)
        confirm = f'echo -n 2.0.0 > "{self.data}/full_update_ok"\n' if confirms else ""
        script = path / "start_cirqen.sh"
        script.write_text(f'#!/bin/bash\necho {label} >> "{self.data}/started"\n{confirm}')
        script.chmod(0o755)

    def run_swap(self, new_confirms: bool, wait: int):
        app, new = self.root / "Cirqen", self.root / "Cirqen.new"
        self.make_app(app, "old", confirms=False)
        self.make_app(new, "new", confirms=new_confirms)
        closing = subprocess.Popen(["sleep", "1"])                  # "Cirqen", about to quit
        full_update.start_swap(app_path=app, new_dir=new, version="2.0.0", data_path=self.data,
                               pid=closing.pid, wait=wait)
        closing.wait()
        deadline = time.time() + wait + 20
        while time.time() < deadline:
            log = (self.data / "logs" / "full_update.log")
            if log.exists() and ("done" in log.read_text() or "back on the previous app" in log.read_text()):
                break
            time.sleep(0.5)
        time.sleep(1)
        return app

    def test_the_new_app_replaces_the_old_once_it_starts(self):
        app = self.run_swap(new_confirms=True, wait=20)
        self.assertEqual((app / "label").read_text(), "new")
        self.assertEqual((self.root / "Cirqen.previous" / "label").read_text(), "old")
        self.assertFalse((self.root / "Cirqen.new").exists())
        self.assertIn("2.0.0 started; done", (self.data / "logs" / "full_update.log").read_text())

    def test_if_the_new_app_does_not_start_the_old_one_comes_back(self):
        app = self.run_swap(new_confirms=False, wait=3)
        self.assertEqual((app / "label").read_text(), "old")
        self.assertEqual((self.root / "Cirqen.failed" / "label").read_text(), "new")
        failed = json.loads((self.data / "sync_state" / "update_failed.json").read_text())
        self.assertEqual(failed["version"], "2.0.0")                 # not offered again in a loop
        self.assertEqual((self.data / "started").read_text().split(), ["new", "old"])


class UpdateServiceTests(TestCase):
    """AppUpdateService end to end against a fake Control: full_required ->
    download, check, unpack -> Restart starts the swap and quits."""

    def setUp(self):
        from unittest import mock

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data, self.app = root / "data", root / "Cirqen"
        (self.app / "_internal").mkdir(parents=True)
        (self.app / "_internal" / "runtime_id.txt").write_text("rt-old\n")
        (self.app / "_internal" / "version.txt").write_text("1.9.0")
        self.archive = fake_app_archive("rt-new", "2.0.0", "#!/bin/bash\n")
        self.offer = offer_for(self.archive)
        self.requests_seen = []

        def get(url, params=None, headers=None, timeout=None, stream=False):
            self.requests_seen.append((url, params, headers))
            if url.endswith("/api/updates/latest/"):
                return mock.Mock(status_code=200, raise_for_status=lambda: None, json=lambda: {
                    "update_available": True, "version": "2.0.0", "full_required": True,
                    "full_package": self.offer, "changes": "", "critical": False})
            body = self.archive
            response = mock.MagicMock(headers={"Content-Length": str(len(body))})
            response.__enter__.return_value = response
            response.raise_for_status = lambda: None
            response.iter_content = lambda chunk_size: [body]
            return response

        from bulider_tools import app_updates

        for target, value in ((app_updates.requests, "get"), ):
            patcher = mock.patch.object(target, value, get)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("endpoint_sync._public_key", return_value=SIGNER.public_key())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = app_updates.AppUpdateService(self.data, self.app)
        self.service._api_key = "k"
        self.service._hq_url = "https://control.example"

    def test_a_pc_on_another_runtime_prepares_the_full_app_then_swaps_on_restart(self):
        from unittest import mock

        self.service._run_check_cycle()
        url, params, headers = self.requests_seen[0]
        self.assertEqual((params["runtime_id"], params["platform"]), ("rt-old", "linux"))
        status = self.service.get_status()
        self.assertEqual((status["change_type"], status["update_ready"], status["new_version"]), ("full", True, "2.0.0"))
        self.assertTrue((Path(status["staged_path"]) / "start_cirqen.sh").is_file())
        self.assertEqual(self.requests_seen[1][2]["X-Api-Key"], "k")          # the download carries the key

        with mock.patch.object(full_update, "start_swap") as swap:
            self.service.apply_and_restart("2.0.0")
        self.assertEqual(swap.call_args.kwargs["new_dir"], Path(status["staged_path"]))
        self.assertFalse(self.service.get_status()["update_ready"])

    def test_until_it_is_published_nothing_is_downloaded(self):
        self.offer = None
        self.service._run_check_cycle()
        status = self.service.get_status()
        self.assertEqual((status["change_type"], status["update_ready"]), ("full", False))
        self.assertEqual(len(self.requests_seen), 1)
