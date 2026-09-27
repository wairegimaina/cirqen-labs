"""Browser end-to-end tests of the journeys that carry compliance evidence.

Real pages, real JavaScript, a real browser (Playwright + Chromium):

1. Work order: a technologist signs in, opens a machine's page (as from its QR
   label), starts a work order, fills it in, signs it with the mouse and
   submits; the department's in-charge signs in, approves it on the phone
   page with a finger signature; the approved work order's PDF downloads.
2. Calibration certificate: an approved session with its number opens as a
   certificate PDF with a verification QR, and the stored copy is reused.

Opt-in (it needs Chromium): CIRQEN_E2E=1 python manage.py test e2e
    --settings=Equiper.test_settings. CI runs it in the e2e job.
Set CIRQEN_E2E_BROWSER to a Chromium binary if Playwright's own is not installed.
"""
import os
import secrets
import unittest

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from Inventory.models import Department, Equipment, EquipmentDescription
from jobcard.models import jobcard
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()
E2E = os.getenv("CIRQEN_E2E") == "1"
PASSWORD = secrets.token_urlsafe(16)  # throwaway test users only


@unittest.skipUnless(E2E, "browser tests: set CIRQEN_E2E=1")
@override_settings(DEBUG=True, SESSION_IDLE_SECONDS=0)
class BrowserJourneys(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        # Playwright's sync API runs an event loop on this thread; Django would
        # otherwise refuse ORM calls from the test as "async" (Django docs,
        # "Async safety", on using Playwright in tests).
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls._pw = sync_playwright().start()
        options = {"args": ["--no-proxy-server"]}
        if os.getenv("CIRQEN_E2E_BROWSER"):
            options["executable_path"] = os.environ["CIRQEN_E2E_BROWSER"]
        cls.browser = cls._pw.chromium.launch(**options)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        super().tearDownClass()
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)

    def setUp(self):
        self.dialogs = []
        self.workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        self.icu = Department.objects.create(name="ICU", workshop=self.workshop)
        self.vent = Equipment.objects.create(
            description=EquipmentDescription.objects.create(name="Ventilator", checklist_template=["Check alarms"]),
            model="V60", serial_number="VENT-E2E", department=self.icu, workshop=self.workshop, status="Working")
        self.tech = self._user("e2e_tech", "Tech", "Tech", workshop=self.workshop, level="Engineer")
        self.nic = self._user("e2e_nic", "Nurse", "Incharge", role="NIC", department=self.icu)

    def _user(self, username, first, last, role="Tech", **profile):
        user = User.objects.create_user(username=username, password=PASSWORD, first_name=first, last_name=last)
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile})
        return user

    def _page(self, width=1280, height=900):
        context = self.browser.new_context(viewport={"width": width, "height": height}, accept_downloads=True)
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(15000)
        # An alert means the page refused something: fail with its text.
        page.on("dialog", lambda dialog: (self.dialogs.append(dialog.message), dialog.dismiss()))
        return page

    def _sign_in(self, page, username):
        page.goto(self.live_server_url + "/")
        page.fill("input[name=username]", username)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit].login-btn")
        page.wait_for_load_state("networkidle")

    @staticmethod
    def _draw(page, selector):
        page.locator(selector).scroll_into_view_if_needed()
        box = page.locator(selector).bounding_box()
        page.mouse.move(box["x"] + 20, box["y"] + 30)
        page.mouse.down()
        for step in range(1, 12):
            page.mouse.move(box["x"] + 20 + step * 15, box["y"] + 30 + (step % 3) * 12)
        page.mouse.up()

    def test_work_order_raised_signed_approved_and_printed(self):
        page = self._page()
        self._sign_in(page, "e2e_tech")
        page.goto(f"{self.live_server_url}/assets/machine/{self.vent.pk}/")
        page.click("text=Start work order")
        page.wait_for_function("id => document.querySelector('#equipment')?.value === id", arg=str(self.vent.pk))
        page.evaluate("""() => {
            $('#priority_level').val('High').trigger('change');
            $('#action_taken').val('Repair').trigger('change');
            document.querySelector('#time_started')._flatpickr.setDate('09:00', true);
            document.querySelector('#time_completed')._flatpickr
              ? document.querySelector('#time_completed')._flatpickr.setDate('10:30', true)
              : (document.querySelector('#time_completed').value = '10:30');
        }""")
        page.fill("#job_description", "Replaced the flow sensor; alarm test passed.")
        page.check("input[name=checklist_result_0][value=pass]")
        self._draw(page, "#signature-pad-tech")
        page.click("button[name=submit]")
        page.wait_for_load_state("networkidle")
        self.assertEqual(self.dialogs, [])
        card = jobcard.objects.get(equipment=self.vent)
        self.assertEqual(card.status, "Waiting Approval")
        self.assertTrue(card.tech_signature)
        self.assertEqual(card.checklist[0]["result"], "pass")

        phone = self._page(390, 844)
        self._sign_in(phone, "e2e_nic")
        phone.goto(self.live_server_url + "/jobcard/approvals/")
        phone.click("summary")
        self._draw(phone, ".ap-pad")
        phone.click("button[name=approve]")
        phone.wait_for_load_state("networkidle")
        card.refresh_from_db()
        self.assertEqual(card.status, "Approved")
        self.assertTrue(card.verified_signature)

        with page.expect_download() as download:
            page.evaluate("url => { const a = document.createElement('a'); a.href = url; a.download = ''; "
                          "document.body.appendChild(a); a.click(); }",
                          f"/jobcard/download/pdf/{card.pk}/")
        with open(download.value.path(), "rb") as pdf:
            self.assertEqual(pdf.read(5), b"%PDF-")


@unittest.skipUnless(E2E, "browser tests: set CIRQEN_E2E=1")
@override_settings(DEBUG=True, SESSION_IDLE_SECONDS=0)
class CalibrationJourney(BrowserJourneys):
    """Calibrate in the browser, have it reviewed, print the certificate."""

    def test_work_order_raised_signed_approved_and_printed(self):
        """Covered by BrowserJourneys; not repeated here."""

    def setUp(self):
        super().setUp()
        import datetime
        import tempfile

        from django.utils import timezone

        from CalSoft.models import CalibrationParameter, CalibrationProcedure, SetValue, Standard

        media = tempfile.mkdtemp()
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.centre = Workshop.objects.create(name="Calibration Centre", category="calibration_center")
        self.cal_tech = self._user("e2e_cal", "Cal", "Tech", workshop=self.centre, level="Engineer")
        self.reviewer = self._user("e2e_rev", "Review", "Lead", workshop=self.centre, level="Engineer Incharge")
        self.procedure = CalibrationProcedure.objects.create(name="Ventilator flow", created_by=self.reviewer)
        self.parameter = CalibrationParameter.objects.create(
            procedure=self.procedure, name="Flow", unit="L/min", tolerance=2, num_readings=3,
            standard_reference="FLOW-STD-1")
        for value in ("10", "30"):
            SetValue.objects.create(parameter=self.parameter, value=value)
        today = timezone.localdate()
        Standard.objects.create(name="Flow analyser", model_number="FA", serial_number="FLOW-STD-1",
                                manufacturer="Acme", certificate_number="REF-9",
                                calibration_date=today - datetime.timedelta(days=100),
                                calibration_due_date=today + datetime.timedelta(days=200),
                                created_by=self.reviewer)

    def test_calibrated_reviewed_and_certificate_printed(self):
        from CalSoft.models import CalibrationSession, IssuedCertificate

        page = self._page()
        self._sign_in(page, "e2e_cal")
        page.goto(f"{self.live_server_url}/calibration/perform/?equipment={self.vent.pk}")
        page.select_option("#procedure", str(self.procedure.pk))
        page.wait_for_selector("input[name^=reading_]")
        page.fill("#actual_temperature", "23")
        page.fill("#actual_humidity", "50")
        page.fill("#actual_pressure", "101.3")
        page.fill(f"#resolution_{self.parameter.pk}", "0.1")
        from CalSoft.models import SetValue

        points = {str(sv.pk): float(sv.value) for sv in SetValue.objects.filter(parameter=self.parameter)}
        for field in page.locator("input[name^=reading_]").all():
            # reading_<parameter>_<sub>_<set value>_<n>: 0.5 above the set value,
            # inside the 2 L/min tolerance.
            field.fill(str(points[field.get_attribute("name").split("_")[3]] + 0.5))
        page.wait_for_function("() => !document.querySelector('#submitBtn').disabled")
        page.click("#submitBtn")
        # The page shows its "Processing calibration" steps before it submits.
        page.wait_for_url(lambda url: "/calibration/perform/" not in url, timeout=30000)
        self.assertEqual(self.dialogs, [])
        session = CalibrationSession.objects.get(device_serial="VENT-E2E")
        self.assertEqual(session.status, "pending_review")
        self.assertEqual(len(session.readings.first().get_readings_list()), 3)

        review = self._page()
        self._sign_in(review, "e2e_rev")
        review.goto(self.live_server_url + "/calibration/sessions/pending-approval/")
        result = review.evaluate("""async id => {
            const token = document.querySelector('[name=csrfmiddlewaretoken]')?.value
                || document.querySelector('meta[name=csrf-token]')?.content;
            const r = await fetch(`/calibration/sessions/${id}/approve/`, {method: 'POST',
                headers: {'X-CSRFToken': token, 'Accept': 'application/json'}, body: new FormData()});
            return {status: r.status, body: await r.json()};
        }""", str(session.pk))
        self.assertEqual(result["status"], 200, result)
        session.refresh_from_db()
        self.assertEqual(session.status, "approved_pending_certificate")

        # HQ allocates the number (hq_server); simulate that step.
        CalibrationSession.objects.filter(pk=session.pk).update(status="approved", certificate_number="BNH-0500")
        review.goto(f"{self.live_server_url}/calibration/sessions/{session.pk}/")
        with review.expect_download() as download:
            review.click(f"a[href$='/sessions/{session.pk}/certificate/comprehensive/']")
        with open(download.value.path(), "rb") as pdf:
            first = pdf.read()
        self.assertTrue(first.startswith(b"%PDF-"))
        stored = IssuedCertificate.objects.get(session=session)
        self.assertEqual(stored.certificate_number, "BNH-0500")
        with review.expect_download() as again:
            review.click(f"a[href$='/sessions/{session.pk}/certificate/comprehensive/']")
        with open(again.value.path(), "rb") as pdf:
            self.assertEqual(pdf.read(), first)  # the stored copy, byte for byte
