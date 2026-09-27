"""Start with Ansur from Perform Calibration: the work order, starting Ansur,
following the job, re-opening, cancelling and importing a record by hand."""
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from CalSoft.ansur import jobfile
from CalSoft.ansur.testing import AnsurFixture, record_xml
from CalSoft.models import AnsurJob

START = reverse("calibration:ansur_start")
STATUS = reverse("calibration:ansur_job_status")
ACTION = reverse("calibration:ansur_job_action")
UPLOAD = reverse("calibration:ansur_upload")


class StartWithAnsurTests(AnsurFixture, TestCase):
    def setUp(self):
        self.make_ansur_site()
        self.client.force_login(self.tech)
        popen = mock.patch("CalSoft.ansur.launcher.subprocess.Popen")
        self.popen = popen.start()
        self.addCleanup(popen.stop)
        pdf = mock.patch("CalSoft.ansur.launcher.make_pdf", return_value=None)
        pdf.start()
        self.addCleanup(pdf.stop)

    def _start(self, **overrides):
        data = {"equipment": self.equipment.pk, "procedure": self.procedure.pk,
                "actual_temperature": "23.1", "actual_humidity": "48", "notes": "Bench 2", **overrides}
        return self.client.post(START, data)

    def test_start_writes_the_work_order_and_opens_ansur(self):
        response = self._start()
        self.assertEqual(response.status_code, 200, response.content)
        job = AnsurJob.objects.get()
        self.assertEqual(job.status, AnsurJob.SENT)
        self.assertEqual(response.json()["job"]["job_number"], job.job_number)

        command = self.popen.call_args[0][0]
        self.assertEqual(command, [str(self.exe), job.job_file])
        root = ET.parse(job.job_file).getroot()
        self.assertEqual(root.attrib["Type"], "JobOrder")
        setup_el = root.find("JobOrder/Setup")
        self.assertEqual(setup_el.attrib["ReadOnly"], "True")
        self.assertEqual(setup_el.attrib["ResultFile"], jobfile.result_file_name(job))
        self.assertTrue(setup_el.attrib["Template"].endswith("Defibrillator.mtt"))
        items = {i.attrib["Name"]: i.text for i in setup_el.findall("DUT/Item")}
        self.assertEqual(items["Serial No"], "DF-44102")
        self.assertEqual(items["Cirqen Job"], job.job_number)
        self.assertEqual(root.find("JobOrder/OutputDir").text, str(self.base / "results"))

    def test_the_launch_arguments_come_from_settings(self):
        self.cfg.launch_arguments = '/job "{job}" /hide'
        self.cfg.save()
        self._start()
        job = AnsurJob.objects.get()
        self.assertEqual(self.popen.call_args[0][0], [str(self.exe), "/job", job.job_file, "/hide"])

    def test_pressing_start_again_reuses_the_open_job(self):
        self._start()
        self._start()
        self.assertEqual(AnsurJob.objects.count(), 1)
        self.assertEqual(self.popen.call_count, 2)

    def test_needs_the_room_conditions(self):
        response = self._start(actual_humidity="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("temperature and humidity", response.json()["error"])
        self.assertFalse(AnsurJob.objects.exists())

    def test_refused_when_the_procedure_is_not_set_up(self):
        self.energy.ansur_step = ""
        self.energy.save()
        response = self._start()
        self.assertEqual(response.status_code, 400)
        self.assertIn("not linked to an Ansur test step", response.json()["error"])

    def test_refused_when_switched_off(self):
        self.cfg.enabled = False
        self.cfg.save()
        self.assertEqual(self._start().status_code, 400)

    def test_ansur_failing_to_start_is_reported(self):
        self.popen.side_effect = OSError(2, "The system cannot find the file specified")
        response = self._start()
        self.assertEqual(response.status_code, 502)
        self.assertIn("Could not start Ansur", response.json()["error"])
        self.assertEqual(AnsurJob.objects.get().status, AnsurJob.PREPARED)

    def test_status_check_picks_up_the_saved_record(self):
        self._start()
        job = AnsurJob.objects.get()
        path = self.base / "results" / jobfile.result_file_name(job)
        path.write_bytes(record_xml(job=job.job_number))
        past = time.time() - 10
        os.utime(path, (past, past))

        data = self.client.get(STATUS, {"job": job.pk}).json()["job"]
        self.assertEqual(data["status"], AnsurJob.IMPORTED)
        self.assertTrue(data["session_url"])
        self.assertFalse(Path(job.job_file).exists(), "the work order is removed once imported")

    def test_reopen_and_cancel(self):
        self._start()
        job = AnsurJob.objects.get()
        self.assertTrue(self.client.post(ACTION, {"job": job.pk, "action": "reopen"}).json()["success"])
        self.assertEqual(self.popen.call_count, 2)
        data = self.client.post(ACTION, {"job": job.pk, "action": "cancel"}).json()
        self.assertEqual(data["job"]["status"], AnsurJob.CANCELLED)
        self.assertFalse(Path(job.job_file).exists())

    def test_someone_else_cannot_cancel_the_job(self):
        self._start()
        job = AnsurJob.objects.get()
        other = self._make_user("other_tech", "Tech", level="Engineer", workshop=self.workshop)
        self.client.force_login(other)
        self.assertEqual(self.client.post(ACTION, {"job": job.pk, "action": "cancel"}).status_code, 403)

    def test_import_a_record_by_hand(self):
        self._start()
        job = AnsurJob.objects.get()
        bad = SimpleUploadedFile("r.mtr", record_xml(job=job.job_number, serial="WRONG"))
        response = self.client.post(UPLOAD, {"job": job.pk, "record": bad})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Serial mismatch", response.json()["job"]["error"])

        good = SimpleUploadedFile("r.mtr", record_xml(job=job.job_number))
        data = self.client.post(UPLOAD, {"job": job.pk, "record": good}).json()
        self.assertEqual(data["job"]["status"], AnsurJob.IMPORTED)

    def test_perform_calibration_offers_ansur_for_set_up_procedures(self):
        page = self.client.get(reverse("calibration:perform_calibration"), {"equipment": self.equipment.pk})
        self.assertContains(page, 'id="ansurStartBtn"')
        self.assertContains(page, str(self.procedure.pk))
        self.assertContains(page, "ansur_start.js")

    def test_an_open_job_reopens_its_panel(self):
        self._start()
        page = self.client.get(reverse("calibration:perform_calibration"), {"equipment": self.equipment.pk})
        self.assertContains(page, AnsurJob.objects.get().job_number)
