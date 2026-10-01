"""An in-app update only changes code that is (a) shipped in the update
package and (b) loaded from loose .py files by the installed app. A frozen
module always wins over a file on disk, so 1.6.1 reached PCs as files the app
never imported. These keep the build and the update package in step."""
import importlib.util
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UpdatePackageContentsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        import build

        cls.build = build
        cls.package = _load("build_package_under_test", ROOT / "hq_server" / "build_package.py")

    def test_every_app_package_is_loose_source_and_shipped_in_updates(self):
        modes = self.build.app_source_modules()
        packages = [name for name in modes if (ROOT / name).is_dir()]
        self.assertIn("sync", packages)
        self.assertTrue(all(mode == "py" for mode in modes.values()))
        missing = sorted(set(packages) - set(self.package.INCLUDE_DIRS))
        self.assertEqual(missing, [], "add these to INCLUDE_DIRS in hq_server/build_package.py")

    def test_top_level_modules_are_loose_source_and_shipped_in_updates(self):
        for name in self.build.APP_TOP_LEVEL_MODULES:
            self.assertTrue((ROOT / f"{name}.py").is_file(), name)
            self.assertIn(name, self.build.app_source_modules())
            self.assertIn(f"{name}.py", self.package.INCLUDE_FILES)

    def test_update_package_names_only_real_folders(self):
        gone = [d for d in self.package.INCLUDE_DIRS if not (ROOT / d).is_dir()]
        self.assertEqual(gone, [])

    def test_the_spec_template_uses_the_collection_mode(self):
        source = (ROOT / "build.py").read_text()
        self.assertIn("module_collection_mode=module_collection_mode", source)
