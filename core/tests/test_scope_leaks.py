"""No page shows a user equipment from outside their scope.

Every parameterless route is requested as a department in-charge (NIC) and as
a workshop technician. Each department's equipment carries a distinctive
serial number; a page that prints a serial the user may not see fails the
test. This caught the machine-reports exports that gave an in-charge the whole
hospital. Compressed downloads (PDF, xlsx) cannot be searched this way, so the
exports have their own tests in machineReports/test_scoping.py.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.tests.test_route_smoke import LOGOUT_ROUTES, parameterless_routes
from Inventory.models import Department, Equipment, EquipmentDescription, Manufacturer
from jobcard.models import jobcard
from ppms.models import PPMSchedule
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()

# Calibration planning is hospital-wide by design: one calibration centre
# serves every workshop (see core/scoping.py), so workshop-level users see all
# equipment on these pages.
GLOBAL_FOR_WORKSHOP_USERS = {"calSchedules/", "calSchedules/ajax/unscheduled/"}

COMPRESSED = ("pdf", "spreadsheet", "zip", "octet-stream")


class ScopeLeakTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        renal_ws = Workshop.objects.create(name="Renal Workshop")
        icu_ws = Workshop.objects.create(name="ICU Workshop")
        renal = Department.objects.create(name="Renal Unit", workshop=renal_ws)
        dialysis = Department.objects.create(name="Dialysis Bay", workshop=renal_ws)
        icu = Department.objects.create(name="ICU", workshop=icu_ws)
        desc = EquipmentDescription.objects.create(name="Monitor")
        mfr = Manufacturer.objects.create(name="Acme")
        month = datetime.date.today().replace(day=1)
        for dept, serial in ((renal, "SCOPE-RENAL"), (dialysis, "SCOPE-DIALYSIS"), (icu, "SCOPE-ICU")):
            eq = Equipment.objects.create(description=desc, manufacturer=mfr, model="M", serial_number=serial,
                                          department=dept, workshop=dept.workshop, status="Working")
            PPMSchedule.objects.create(equipment=eq, workshop=dept.workshop, scheduled_month=month)
            jobcard.objects.create(department=dept, equipment=eq, workshop=dept.workshop, priority_level="Low",
                                   action_taken="Repair", job_description=f"Job on {serial}",
                                   status="Waiting Approval")

        def user(username, role, **profile):
            u = User.objects.create_user(username=username, password="pw12345!")
            UserProfile.objects.update_or_create(user=u, defaults={
                "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile})
            return u

        # (user, serials they must never see, routes exempt for them)
        cls.cases = [
            (user("scope_nic", "NIC", department=renal), {"SCOPE-DIALYSIS", "SCOPE-ICU"}, set()),
            (user("scope_tech", "Tech", workshop=renal_ws, level="Engineer Incharge"), {"SCOPE-ICU"},
             GLOBAL_FOR_WORKSHOP_USERS),
        ]

    def test_no_page_shows_out_of_scope_equipment(self):
        self.client.raise_request_exception = False
        leaks = []
        for account, forbidden, exempt in self.cases:
            for route in parameterless_routes():
                if route in LOGOUT_ROUTES or route in exempt:
                    continue
                self.client.force_login(account)
                response = self.client.get("/" + route)
                if any(kind in response.get("Content-Type", "") for kind in COMPRESSED):
                    continue
                body = (b"".join(response.streaming_content) if response.streaming else response.content)
                text = body.decode("utf-8", "replace")
                seen = sorted(serial for serial in forbidden if serial in text)
                if seen:
                    leaks.append(f"{account.username} /{route} shows {seen}")
        self.assertFalse(leaks, "Out-of-scope equipment on:\n  " + "\n  ".join(leaks))
