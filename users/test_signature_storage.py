"""Signatures live in the database, so a rebuild cannot lose them.

A rebuild or a new data directory leaves MEDIA_ROOT without the old signature
files while the rows still name them. These tests reproduce that by pointing
rows at files that do not exist.

    ./venv/bin/python manage.py test users.test_signature_storage --settings=Equiper.test_settings
"""
import base64
import importlib
import io
import os
import shutil
import tempfile
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from jobcard.views.helpers import get_or_create_user_signature
from jobcard.views.nurse_approval import _has_saved_signature
from users.models import UserProfile, UserSignature

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp(prefix="cirqen-signature-tests-")


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT, ignore_errors=True)


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGBA", (40, 20), (10, 20, 30, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def png_data_uri():
    return "data:image/png;base64," + base64.b64encode(png_bytes()).decode("ascii")


def decodes_as_png(data_uri):
    raw = base64.b64decode(data_uri.split("base64,", 1)[1])
    with Image.open(io.BytesIO(raw)) as img:
        return img.format == "PNG"


def signature_files_on_disk():
    folder = os.path.join(MEDIA_ROOT, "signatures")
    return os.listdir(folder) if os.path.isdir(folder) else []


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class SignatureStorageTestCase(TestCase):
    def setUp(self):
        self.hod = self.make_user("hod")

    def make_user(self, username):
        user = User.objects.create_user(
            username=username, password="pass12345", email=f"{username}@test.com",
            first_name="Grace", last_name="Wanjiru",
        )
        UserProfile.objects.update_or_create(user=user, defaults={"role": "HOD"})
        return user

    def login(self, finish_setup=True):
        if finish_setup:
            UserProfile.objects.filter(user=self.hod).update(
                must_change_password=False, has_uploaded_signature=True
            )
        self.client.login(username="hod", password="pass12345")

    def signature(self, user=None):
        return UserSignature.objects.get(user=user or self.hod)

    def simulate_rebuild(self, user=None, data=None):
        """The row keeps its values, but the media file it names is gone."""
        UserSignature.objects.filter(user=user or self.hod).update(
            signature_data=data,
            signature_image="signatures/lost_in_rebuild.png",
            is_user_drawn=True,
        )

    def assertStoredInDatabaseOnly(self, user=None):
        sig = self.signature(user)
        self.assertTrue(decodes_as_png(sig.signature_data))
        self.assertFalse(sig.signature_image)
        self.assertEqual(signature_files_on_disk(), [])
        return sig

    # --- writes -------------------------------------------------------------

    def test_drawn_signature_is_written_to_database_not_disk(self):
        self.signature().save_user_drawn_signature(ContentFile(png_bytes(), name="sig.png"))

        self.assertStoredInDatabaseOnly()
        self.assertTrue(UserProfile.objects.get(user=self.hod).has_uploaded_signature)

    def test_first_login_setup_stores_signature_in_database(self):
        self.login(finish_setup=False)
        response = self.client.post(reverse("force_setup"), {
            "new_password": "NewPassword123",
            "confirm_password": "NewPassword123",
            "signature_data": png_data_uri(),
        })

        self.assertEqual(response.status_code, 302)
        self.assertStoredInDatabaseOnly()

    def test_admin_upload_stores_signature_in_database(self):
        target = self.make_user("uploaded")
        self.login()
        response = self.client.post(reverse("api_update_user", args=[target.id]), {
            "signature": SimpleUploadedFile("sig.png", png_bytes(), content_type="image/png"),
        })

        self.assertEqual(response.status_code, 200, response.content)
        self.assertStoredInDatabaseOnly(target)

    def test_admin_upload_rejects_a_file_that_is_not_an_image(self):
        target = self.make_user("uploaded")
        self.login()
        response = self.client.post(reverse("api_update_user", args=[target.id]), {
            "signature": SimpleUploadedFile("sig.png", b"not an image", content_type="image/png"),
        })

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.signature(target).signature_data)

    def test_regenerate_writes_a_system_signature_to_database(self):
        self.login()
        response = self.client.post(reverse("api_regenerate_signature", args=[self.hod.id])).json()

        sig = self.assertStoredInDatabaseOnly()
        self.assertTrue(response["success"])
        self.assertEqual(response["signatureUrl"], sig.signature_data)
        self.assertFalse(sig.is_user_drawn)

    def test_user_without_a_signature_gets_a_system_one_in_database(self):
        result = get_or_create_user_signature(self.hod)

        sig = self.assertStoredInDatabaseOnly()
        self.assertEqual(result, sig.signature_data)
        self.assertFalse(sig.is_user_drawn)

    # --- reads after a rebuild ------------------------------------------------

    def test_signature_survives_a_rebuild_everywhere_it_is_read(self):
        data = png_data_uri()
        self.simulate_rebuild(data=data)
        self.login()

        api = self.client.get(reverse("api_get_user_signature", args=[self.hod.id])).json()
        self.assertTrue(api["hasSignature"])
        self.assertEqual(api["signatureUrl"], data)

        download = self.client.get(reverse("download_signature", args=[self.hod.id]))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, base64.b64decode(data.split("base64,", 1)[1]))

        self.assertTrue(_has_saved_signature(self.hod))
        self.assertEqual(get_or_create_user_signature(self.hod), data)
        # The drawn signature must not be replaced by a system one.
        self.assertTrue(self.signature().is_user_drawn)
        self.assertEqual(self.signature().signature_data, data)

    def test_row_with_only_a_missing_file_reports_no_signature(self):
        self.simulate_rebuild(data=None)

        self.assertFalse(self.signature().has_signature())
        self.assertFalse(_has_saved_signature(self.hod))

    # --- legacy files ---------------------------------------------------------

    def test_migration_copies_reachable_files_into_database(self):
        folder = os.path.join(MEDIA_ROOT, "signatures")
        os.makedirs(folder, exist_ok=True)
        self.addCleanup(shutil.rmtree, folder, True)
        with open(os.path.join(folder, "legacy.png"), "wb") as f:
            f.write(png_bytes())
        UserSignature.objects.filter(user=self.hod).update(
            signature_data=None, signature_image="signatures/legacy.png"
        )
        gone = self.make_user("gone")
        self.simulate_rebuild(user=gone, data=None)

        # Before migrating, the legacy file is still readable as a fallback.
        self.assertTrue(decodes_as_png(self.signature().get_signature_as_base64()))

        migration = importlib.import_module("users.migrations.0005_copy_signature_files_into_database")
        migration.copy_signature_files_into_database(apps, SimpleNamespace(connection=connection))

        self.assertTrue(decodes_as_png(self.signature().signature_data))
        self.assertIsNone(self.signature(gone).signature_data)
