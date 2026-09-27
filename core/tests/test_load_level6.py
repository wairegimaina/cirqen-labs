"""Level 6 load test: every page, for every role, at hospital volumes.

8 workshops, 200 departments, 12,000 machines, 36,000 work orders, 12,000 PPM
schedules and ~215 users. Every parameterless page is requested as the HOD, a
technologist and an in-charge. The test fails on any server error and on any
page slower than LOAD_TEST_MAX_SECONDS (default 2).

Opt-in (it takes minutes): runs nightly in CI on PostgreSQL, or by hand:

    CIRQEN_LOAD_TEST=1 python manage.py test core.tests.test_load_level6 \\
        --settings=Equiper.load_test_settings
"""
import datetime
import os
import random
import time
import unittest

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase, override_settings

from core.tests.test_route_smoke import LOGOUT_ROUTES, parameterless_routes
from Inventory.models import Department, Equipment, EquipmentDescription, Manufacturer
from jobcard.models import jobcard
from ppms.models import PPMSchedule
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()
WORKSHOPS, DEPTS, EQ_PER_DEPT, JC_PER_EQ = 8, 200, 60, 3
MAX_SECONDS = float(os.getenv("LOAD_TEST_MAX_SECONDS", "2"))
# Reports the pages build in the background (core.report_jobs) keep a worker
# busy, not a person waiting; they get their own, looser limit.
BACKGROUND_REPORT_ROUTES = {
    "machineReports/export-equipment-category-detailed-pdf/",
    "machineReports/export-manufacturer-pdf/",
    "machineReports/reports/manufacturer/pdf/",
}
BACKGROUND_MAX_SECONDS = float(os.getenv("LOAD_TEST_BACKGROUND_MAX_SECONDS", "60"))


@unittest.skipUnless(os.getenv("CIRQEN_LOAD_TEST") == "1", "load test: set CIRQEN_LOAD_TEST=1")
@override_settings(QUERY_BUDGET=1)
class Level6LoadTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        started = time.time()
        rnd = random.Random(1)
        workshops = [Workshop.objects.create(name=f"Workshop {i}") for i in range(WORKSHOPS)]
        makers = [Manufacturer.objects.create(name=f"Maker {i}") for i in range(25)]
        types = [EquipmentDescription.objects.create(name=f"Type {i}") for i in range(120)]
        departments = [Department.objects.create(name=f"Dept {i}", workshop=workshops[i % WORKSHOPS])
                       for i in range(DEPTS)]
        machines, n = [], 0
        for department in departments:
            for _ in range(EQ_PER_DEPT):
                n += 1
                machines.append(Equipment(
                    description=rnd.choice(types), manufacturer=rnd.choice(makers), model="M",
                    serial_number=f"SN-{n}", department=department, workshop=department.workshop,
                    status=rnd.choice(["Working"] * 8 + ["Under repair", "Not working"])))
        Equipment.objects.bulk_create(machines, batch_size=2000)
        machines = list(Equipment.objects.all())
        month = datetime.date.today().replace(day=1)
        PPMSchedule.objects.bulk_create(
            [PPMSchedule(equipment=e, workshop=e.workshop, scheduled_month=month, maintenance_period=6)
             for e in machines], batch_size=2000)
        jobcard.objects.bulk_create(
            [jobcard(department=e.department, equipment=e, workshop=e.workshop,
                     priority_level=rnd.choice(["Low", "Medium", "High", "Urgent"]),
                     action_taken=rnd.choice(["Repair", "PPM", "Calibration"]), job_description="x",
                     status=rnd.choice(["Approved"] * 6 + ["Waiting Approval", "Declined"]))
             for e in machines for _ in range(JC_PER_EQ)], batch_size=2000)

        def make(username, role, **extra):
            user = User.objects.create_user(username=username, password="pw12345!")
            UserProfile.objects.update_or_create(user=user, defaults={
                "role": role, "must_change_password": False, "has_uploaded_signature": True, **extra})
            return user

        cls.users = {
            "HOD": make("load_hod", "HOD"),
            "Tech": make("load_tech", "Tech", workshop=workshops[0], level="Engineer Incharge"),
            "NIC": make("load_nic", "NIC", department=departments[0]),
        }
        for i in range(1, 13):
            make(f"load_tech{i}", "Tech", workshop=workshops[i % WORKSHOPS], level="Engineer")
        for i in range(1, DEPTS):
            make(f"load_nic{i}", "NIC", department=departments[i])
        print(f"\nseeded {Equipment.objects.count()} machines, {jobcard.objects.count()} work orders, "
              f"{PPMSchedule.objects.count()} PPM rows, {User.objects.count()} users "
              f"in {time.time() - started:.0f}s")

    def test_every_page_is_fast_for_every_role(self):
        self.client.raise_request_exception = False
        rows = []
        for role, user in self.users.items():
            for route in parameterless_routes():
                if route in LOGOUT_ROUTES:
                    continue
                caches["default"].clear()  # cold cache: the worst case
                self.client.force_login(user)
                started = time.perf_counter()
                response = self.client.get("/" + route)
                rows.append((time.perf_counter() - started, role, route, response.status_code,
                             int(response.get("X-Query-Count", 0))))
        rows.sort(reverse=True)
        served = [r for r in rows if r[3] < 400]
        median = sorted(r[0] for r in served)[len(served) // 2] if served else 0
        print(f"\n{len(rows)} requests; median {median * 1000:.0f} ms; "
              f"over {MAX_SECONDS:g}s: {sum(1 for r in served if r[0] > MAX_SECONDS)}; "
              f"5xx: {sum(1 for r in rows if r[3] >= 500)}")
        for seconds, role, route, code, queries in rows[:20]:
            print(f"{seconds * 1000:8.0f} ms  {queries:4d} q  {code}  {role:5s} /{route}")

        errors = [f"{role} /{route} -> {code}" for _s, role, route, code, _q in rows if code >= 500]
        slow = [f"{role} /{route}: {s:.1f}s" for s, role, route, code, _q in served
                if s > (BACKGROUND_MAX_SECONDS if route in BACKGROUND_REPORT_ROUTES else MAX_SECONDS)]
        self.assertEqual(errors, [], "server errors at Level 6 volume")
        self.assertEqual(slow, [], f"pages slower than {MAX_SECONDS:g}s at Level 6 volume")
