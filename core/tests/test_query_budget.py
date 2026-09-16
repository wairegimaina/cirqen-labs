"""N+1 detection: page query counts must not grow with the amount of data
(IMPROVEMENT_PLAN.md 5.5).

Every parameterless route is requested as HOD, Tech and NIC against a small
dataset, the dataset is tripled, and the routes are requested again. A route
whose query count grows with the number of rows is running a query per row.
"""
import datetime

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

# Extra queries tolerated when the data triples (pagination edges, one-off lookups).
GROWTH_ALLOWANCE = 5
# Absolute ceiling for any page on the small dataset.
QUERY_BUDGET = 100

# Known to scale with rows today; each entry needs fixing, then removing from here.
KNOWN_N_PLUS_ONE = {
    "ppms/api/analytics/",  # per-month / per-department loops; cached (core.aggregate_cache)
}


@override_settings(QUERY_BUDGET=1)  # installs the counter; the header carries the count
class QueryGrowthTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(name="Main")
        cls.manufacturer = Manufacturer.objects.create(name="Acme")
        cls.descriptions = [EquipmentDescription.objects.create(name=f"Type{i}") for i in range(4)]
        cls.departments = [Department.objects.create(name=f"D{i}", workshop=cls.workshop) for i in range(2)]
        cls.serial = 0
        cls.users = {}
        for name, role, extra in [
            ("hod", "HOD", {}),
            ("tech", "Tech", {"workshop": cls.workshop, "level": "Engineer Incharge"}),
            ("nic", "NIC", {"department": cls.departments[0]}),
        ]:
            user = User.objects.create_user(username=f"qb_{name}", password="pw12345!")
            UserProfile.objects.update_or_create(
                user=user,
                defaults={"role": role, "must_change_password": False,
                          "has_uploaded_signature": True, **extra},
            )
            cls.users[name] = user

    def _add_rows(self, per_department):
        month = datetime.date.today().replace(day=1)
        for dept in self.departments:
            for i in range(per_department):
                type(self).serial += 1
                eq = Equipment.objects.create(
                    description=self.descriptions[i % len(self.descriptions)],
                    manufacturer=self.manufacturer, model="M", serial_number=f"QB-{self.serial}",
                    department=dept, workshop=self.workshop, status="Working",
                )
                PPMSchedule.objects.create(equipment=eq, workshop=self.workshop, scheduled_month=month)
                jobcard.objects.create(
                    department=dept, equipment=eq, workshop=self.workshop, priority_level="Low",
                    action_taken="Repair", job_description="x", status="Approved",
                )

    def _measure(self):
        counts = {}
        self.client.raise_request_exception = False
        for label, user in self.users.items():
            for route in parameterless_routes():
                if route in LOGOUT_ROUTES:
                    continue
                caches["default"].clear()  # measure the uncached path
                self.client.force_login(user)
                response = self.client.get("/" + route)
                counts[(label, route)] = int(response.get("X-Query-Count", 0))
        return counts

    def test_query_counts_do_not_grow_with_data(self):
        self._add_rows(per_department=4)
        small = self._measure()
        self._add_rows(per_department=8)  # now three times as many rows
        large = self._measure()

        over_budget = [f"{label} /{route}: {n}" for (label, route), n in small.items()
                       if n > QUERY_BUDGET and route not in KNOWN_N_PLUS_ONE]
        growing = [
            f"{label} /{route}: {small[key]} -> {large[key]} queries"
            for key in small
            for label, route in [key]
            if large[key] - small[key] > GROWTH_ALLOWANCE and route not in KNOWN_N_PLUS_ONE
        ]
        problems = over_budget + growing
        if problems:
            self.fail("Query count grows with data (N+1):\n  " + "\n  ".join(problems))
