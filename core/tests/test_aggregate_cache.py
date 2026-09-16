"""Aggregate caching and its invalidation (IMPROVEMENT_PLAN.md 5.2)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase
from django.urls import reverse

from core import aggregate_cache
from Inventory.models import Department, Equipment, EquipmentDescription
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class GetOrComputeTests(TestCase):
    def setUp(self):
        caches["default"].clear()

    def test_second_call_is_served_from_cache(self):
        compute = mock.Mock(return_value={"n": 1})
        self.assertEqual(aggregate_cache.get_or_compute("dash", ["k"], compute), {"n": 1})
        self.assertEqual(aggregate_cache.get_or_compute("dash", ["k"], compute), {"n": 1})
        compute.assert_called_once()

    def test_invalidate_forces_a_recompute_in_that_namespace_only(self):
        dash = mock.Mock(return_value={"n": 1})
        inv = mock.Mock(return_value={"n": 2})
        aggregate_cache.get_or_compute("dash", ["k"], dash)
        aggregate_cache.get_or_compute("inv", ["k"], inv)

        aggregate_cache.invalidate("dash")
        aggregate_cache.get_or_compute("dash", ["k"], dash)
        aggregate_cache.get_or_compute("inv", ["k"], inv)

        self.assertEqual(dash.call_count, 2)
        self.assertEqual(inv.call_count, 1)

    def test_saving_a_model_invalidates_its_namespaces(self):
        compute = mock.Mock(return_value={"n": 1})
        aggregate_cache.get_or_compute("dash", ["k"], compute)
        Workshop.objects.create(name="Theatre")
        aggregate_cache.get_or_compute("dash", ["k"], compute)
        self.assertEqual(compute.call_count, 2)


class DashboardFreshnessTests(TestCase):
    """The cached dashboard must show a new device straight away, not after the TTL."""

    def setUp(self):
        caches["default"].clear()
        self.workshop = Workshop.objects.create(name="Radiology")
        self.department = Department.objects.create(name="CT", workshop=self.workshop)
        self.description = EquipmentDescription.objects.create(name="Scanner")
        tech = User.objects.create_user(username="tech_cache", password="pw12345!")
        UserProfile.objects.update_or_create(
            user=tech,
            defaults={"role": "Tech", "workshop": self.workshop, "level": "Engineer",
                      "must_change_password": False, "has_uploaded_signature": True},
        )
        self.client.force_login(tech)

    def _add_equipment(self, serial):
        Equipment.objects.create(
            description=self.description, model="X1", serial_number=serial,
            department=self.department, status="Working",
        )

    def _total_equipment(self):
        response = self.client.get(reverse("dashboard:dashboard-main"))
        self.assertEqual(response.status_code, 200)
        return response.context["analytics_data"]["total_equipment"]

    def test_counts_update_after_a_save(self):
        self._add_equipment("SN-1")
        self.assertEqual(self._total_equipment(), 1)
        self._add_equipment("SN-2")
        self.assertEqual(self._total_equipment(), 2)

    def test_repeat_visits_do_not_rerun_the_aggregate_queries(self):
        self._add_equipment("SN-1")
        self._total_equipment()
        with mock.patch("dashboard.views._equipment_analytics") as compute:
            self._total_equipment()
        compute.assert_not_called()
