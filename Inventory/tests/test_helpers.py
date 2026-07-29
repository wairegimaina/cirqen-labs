"""Unit tests for the pure helpers extracted from the old monolithic views.

These run without a database (``SimpleTestCase``): the pagination helpers are
exercised against a real ``django.core.paginator.Paginator`` built over a plain
Python list, and ``serialize_equipment`` against a lightweight stand-in object.
This is exactly the payoff of the refactor — logic that used to be buried and
duplicated inside 300-line view functions is now independently verifiable.
"""
from types import SimpleNamespace

from django.core.paginator import Paginator
from django.test import SimpleTestCase

from Inventory.views.helpers import (
    parse_page_number,
    normalize_per_page,
    serialize_equipment,
    build_pagination_data,
    ALLOWED_PER_PAGE,
)


class ParsePageNumberTests(SimpleTestCase):
    def test_valid_number(self):
        self.assertEqual(parse_page_number("3"), 3)
        self.assertEqual(parse_page_number(5), 5)

    def test_below_one_clamped_to_one(self):
        self.assertEqual(parse_page_number(0), 1)
        self.assertEqual(parse_page_number("-4"), 1)

    def test_garbage_defaults_to_one(self):
        self.assertEqual(parse_page_number("abc"), 1)
        self.assertEqual(parse_page_number(None), 1)


class NormalizePerPageTests(SimpleTestCase):
    def test_allowed_values_pass_through(self):
        for value in ALLOWED_PER_PAGE:
            self.assertEqual(
                normalize_per_page(str(value), invalid_fallback=1, error_fallback=100),
                value,
            )

    def test_not_allowed_uses_invalid_fallback(self):
        # e.g. the main inventory() view: an out-of-range size collapses to 1
        self.assertEqual(
            normalize_per_page("7", invalid_fallback=1, error_fallback=100), 1
        )
        # the HOD view uses 10 for the same case
        self.assertEqual(
            normalize_per_page("7", invalid_fallback=10, error_fallback=10), 10
        )

    def test_unparseable_uses_error_fallback(self):
        self.assertEqual(
            normalize_per_page("xyz", invalid_fallback=1, error_fallback=100), 100
        )
        self.assertEqual(
            normalize_per_page(None, invalid_fallback=10, error_fallback=10), 10
        )


class SerializeEquipmentTests(SimpleTestCase):
    def _equipment(self, **overrides):
        workshop = SimpleNamespace(id="ws-uuid", name="Radiology")
        department = SimpleNamespace(id="dep-1", name="CT", workshop=workshop)
        base = dict(
            id="eq-1",
            description=SimpleNamespace(id="d-1", name="Scanner"),
            manufacturer=SimpleNamespace(id="m-1", name="Acme"),
            model="X100",
            serial_number="SN-1",
            department=department,
            status="Working",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_full_object_serialized(self):
        data = serialize_equipment(self._equipment())
        self.assertEqual(data["id"], "eq-1")
        self.assertEqual(data["description"], "Scanner")
        self.assertEqual(data["manufacturer"], "Acme")
        self.assertEqual(data["model"], "X100")
        self.assertEqual(data["serial"], "SN-1")
        self.assertEqual(data["department"], "CT")
        self.assertEqual(data["workshop"], "Radiology")
        self.assertEqual(data["workshop_id"], "ws-uuid")
        self.assertEqual(data["status"], "Working")

    def test_missing_manufacturer_and_model_become_empty_strings(self):
        data = serialize_equipment(self._equipment(manufacturer=None, model="", serial_number=""))
        self.assertEqual(data["manufacturer"], "")
        self.assertEqual(data["manufacturer_id"], "")
        self.assertEqual(data["model"], "")
        self.assertEqual(data["serial"], "")

    def test_department_without_workshop(self):
        dep = SimpleNamespace(id="dep-2", name="Store", workshop=None)
        data = serialize_equipment(self._equipment(department=dep))
        self.assertEqual(data["department"], "Store")
        self.assertEqual(data["workshop"], "")
        self.assertEqual(data["workshop_id"], "")


class BuildPaginationDataTests(SimpleTestCase):
    def test_non_empty_first_page(self):
        items = list(range(25))  # 3 pages at 10/page
        paginator = Paginator(items, 10)
        page_obj = paginator.get_page(1)

        data = build_pagination_data(page_obj, paginator, per_page=10, total_count=25)

        self.assertTrue(data["has_next"])
        self.assertFalse(data["has_previous"])
        self.assertEqual(data["next_page_number"], 2)
        self.assertIsNone(data["previous_page_number"])
        self.assertEqual(data["num_pages"], 3)
        self.assertEqual(data["current_page"], 1)
        self.assertEqual(data["start_index"], 1)
        self.assertEqual(data["end_index"], 10)
        self.assertEqual(data["total_count"], 25)
        self.assertEqual(data["per_page"], 10)
        self.assertTrue(data["has_other_pages"])
        self.assertIn(1, data["page_range"])

    def test_middle_page_has_both_neighbours(self):
        paginator = Paginator(list(range(25)), 10)
        page_obj = paginator.get_page(2)
        data = build_pagination_data(page_obj, paginator, per_page=10, total_count=25)
        self.assertTrue(data["has_next"])
        self.assertTrue(data["has_previous"])
        self.assertEqual(data["previous_page_number"], 1)
        self.assertEqual(data["next_page_number"], 3)

    def test_empty_result_uses_zeroed_block(self):
        paginator = Paginator([], 10)
        page_obj = paginator.get_page(1)
        data = build_pagination_data(page_obj, paginator, per_page=10, total_count=0)

        self.assertFalse(data["has_next"])
        self.assertFalse(data["has_previous"])
        self.assertIsNone(data["next_page_number"])
        self.assertEqual(data["total_count"], 0)
        self.assertEqual(data["start_index"], 0)
        self.assertEqual(data["end_index"], 0)
        self.assertEqual(data["page_range"], [1])

    def test_single_page_range_is_just_one(self):
        paginator = Paginator(list(range(5)), 10)
        page_obj = paginator.get_page(1)
        data = build_pagination_data(page_obj, paginator, per_page=10, total_count=5)
        self.assertEqual(data["page_range"], [1])
        self.assertFalse(data["has_other_pages"])
