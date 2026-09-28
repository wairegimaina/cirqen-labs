"""The Ansur connection page: finding Ansur, the work folders, linking
procedures to templates, and refusing to switch on until it all works."""
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from CalSoft.ansur import setup
from CalSoft.models import AnsurSettings, AnsurTemplateMap, CalibrationProcedure
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()
URL = reverse("calibration:ansur_settings")


class AnsurSettingsPageTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.base = str(self.tmp / "CirqenAnsur")
        self.exe = self.tmp / "Fluke" / "Ansur" / "Ansur.exe"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"MZ")
        windows = mock.patch.object(setup, "is_windows", return_value=True)
        windows.start()
        self.addCleanup(windows.stop)

        self.hod = self._user("ansur_hod", "HOD")
        self.client.force_login(self.hod)
        self.procedure = CalibrationProcedure.objects.create(name="Defibrillator", created_by=self.hod)

    def _user(self, username, role, **extra):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **extra})
        return user

    def _save(self, **overrides):
        data = {"action": "save", "program_path": str(self.exe), "base_folder": self.base,
                "delete_job_files": "on", "archive_years": "10",
                "launch_arguments": '"{job}"', "pdf_arguments": '/f "{record}" /h', **overrides}
        return self.client.post(URL, data)

    def _ready_setup(self):
        self._save()
        self.client.post(URL, {"action": "create_folders"})
        (Path(self.base) / "templates" / "Defibrillator.mtt").write_text("<METRONFile/>")
        self.client.post(URL, {"action": "add_map", "procedure": self.procedure.pk,
                               "template_file": "Defibrillator.mtt", "service_event": "PM"})

    def test_only_the_hod_can_open_it(self):
        self.client.force_login(self._user("ansur_tech", "Tech", level="Engineer",
                                             workshop=Workshop.objects.create(name="Biomed")))
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_a_fresh_install_says_what_to_do(self):
        page = self.client.get(URL)
        self.assertContains(page, "Not ready yet")
        self.assertContains(page, "Press Find Ansur")
        self.assertContains(page, "Press Create folders.")
        self.assertContains(page, "Link at least one procedure below.")

    def test_find_ansur_fills_in_the_program(self):
        with mock.patch.dict("os.environ", {"ProgramFiles": str(self.tmp)}, clear=False):
            self.client.post(URL, {"action": "detect"})
        self.assertEqual(AnsurSettings.load().program_path, str(self.exe))

    def test_find_ansur_says_so_when_it_is_not_there(self):
        with mock.patch.object(setup, "detect_program", return_value=""):
            page = self.client.post(URL, {"action": "detect"}, follow=True)
        self.assertContains(page, "Could not find Ansur")
        self.assertEqual(AnsurSettings.load().program_path, "")

    def test_create_folders_makes_all_five(self):
        self._save()
        self.client.post(URL, {"action": "create_folders"})
        for name in setup.FOLDERS:
            self.assertTrue((Path(self.base) / name).is_dir(), name)

    def test_bad_paths_are_refused_with_a_reason(self):
        page = self._save(program_path=str(self.tmp / "notes.txt"), base_folder="CirqenAnsur")
        self.assertContains(page, "it ends in .exe")
        self.assertContains(page, "full path starting with the drive")
        page = self._save(base_folder=r"\\server\share\ansur")
        self.assertContains(page, "not a network share")

    def test_it_cannot_be_switched_on_until_ready(self):
        page = self._save(enabled="on")
        self.assertContains(page, "Fix these before switching it on")
        self.assertFalse(AnsurSettings.load().enabled)

    def test_switching_on_when_everything_checks_out(self):
        self._ready_setup()
        self.assertTrue(setup.is_ready(AnsurSettings.load(), AnsurTemplateMap.objects.all()))
        self._save(enabled="on")
        self.assertTrue(AnsurSettings.load().enabled)
        self.assertContains(self.client.get(URL), "Ready. Technicians see Start with Ansur")

    def test_a_linked_template_missing_from_the_folder_is_flagged(self):
        self._save()
        self.client.post(URL, {"action": "create_folders"})
        self.client.post(URL, {"action": "add_map", "procedure": self.procedure.pk,
                               "template_file": "Infusion.mtt", "service_event": "PM"})
        page = self.client.get(URL)
        self.assertContains(page, "Not in the templates folder: Infusion.mtt.")
        self.assertContains(page, "missing")

    def test_template_must_be_a_bare_mtt_file_name(self):
        for name, reason in [(r"C:\x\Defib.mtt", "file name only"), ("Defib.txt", "end in .mtt")]:
            page = self.client.post(URL, {"action": "add_map", "procedure": self.procedure.pk,
                                          "template_file": name, "service_event": "PM"})
            self.assertContains(page, reason)
        self.assertFalse(AnsurTemplateMap.objects.exists())

    def test_a_procedure_links_to_one_template_only(self):
        self._ready_setup()
        form_page = self.client.get(URL)
        self.assertNotContains(form_page, f'<option value="{self.procedure.pk}">')

    def test_removing_the_last_link_switches_it_off(self):
        self._ready_setup()
        self._save(enabled="on")
        link = AnsurTemplateMap.objects.get()
        self.client.post(URL, {"action": "remove_map", "map_id": link.pk})
        self.assertFalse(AnsurTemplateMap.objects.exists())
        self.assertFalse(AnsurSettings.load().enabled)

    def test_it_is_never_ready_off_windows(self):
        self._ready_setup()
        with mock.patch.object(setup, "is_windows", return_value=False):
            self.assertFalse(setup.is_ready(AnsurSettings.load(), AnsurTemplateMap.objects.all()))
