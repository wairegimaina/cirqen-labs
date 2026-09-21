"""Static URLs change when the file changes (IMPROVEMENT_PLAN.md 5.4)."""
import os
import tempfile
from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase, override_settings


class VersionedStaticUrlTests(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.file = Path(self.dir.name) / "app.js"
        self.file.write_text("console.log(1);")

    def render(self):
        with override_settings(STATICFILES_DIRS=[self.dir.name]):
            from django.contrib.staticfiles import finders

            finders.get_finder.cache_clear()
            return Template('{% load static %}{% static "app.js" %}').render(Context())

    def test_url_carries_a_content_hash(self):
        self.assertRegex(self.render(), r"^/static/app\.js\?v=[0-9a-f]{12}$")

    def test_url_changes_when_the_file_is_updated(self):
        before = self.render()
        self.file.write_text("console.log(2);")
        stat = self.file.stat()
        os.utime(self.file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        self.assertNotEqual(before, self.render())

    def test_unknown_file_gets_a_plain_url(self):
        with override_settings(STATICFILES_DIRS=[self.dir.name]):
            url = Template('{% load static %}{% static "missing.js" %}').render(Context())
        self.assertEqual(url, "/static/missing.js")
