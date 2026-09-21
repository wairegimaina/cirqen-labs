"""Users cannot reach another workshop's records by ID (IMPROVEMENT_PLAN.md 4.2).

Two workshops, A and B, get the same set of records; B's carry a marker string.
A technician and an in-charge from A request every URL that takes a record ID,
pointed at B's records, with GET and with POST. Each request must not return
B's data and must not change any of B's records. Mutations that need real form
data (edit, add, transfer) are exercised separately with valid bodies.

Calibration schedule routes (calSchedules/, calibration/) are excluded on
purpose: the calibration centre serves every workshop and those views are global.
"""
import datetime
import re

from django.contrib.auth import get_user_model
from django.forms.models import model_to_dict
from django.test import TestCase
from django.urls import URLResolver, get_resolver, reverse

from Inventory.models import Department, Equipment, EquipmentDescription
from jobcard.models import jobcard
from parts_tools.models import Accessories, Tools
from ppms.models import PPMSchedule
from users.models import UserProfile, UserSignature
from workshop.models import Workshop

User = get_user_model()

MARKER = "ZX9Q"  # appears only in workshop B's records
GLOBAL_BY_DESIGN = ("calSchedules/", "calibration/")


def uuid_routes():
    def walk(patterns, prefix=""):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
            else:
                route = prefix + str(pattern.pattern)
                if "<uuid:" in route and not route.startswith(GLOBAL_BY_DESIGN):
                    yield route
    return list(dict.fromkeys(walk(get_resolver().url_patterns)))


class CrossWorkshopAccessTest(TestCase):
    def setUp(self):
        self.description = EquipmentDescription.objects.create(name="Pump")
        month = datetime.date.today().replace(day=1)
        self.records = {}
        for tag in "AB":
            mark = MARKER if tag == "B" else "OWN1"
            ws = Workshop.objects.create(name=f"WS{tag}")
            dept = Department.objects.create(name=f"DEPT{tag}", workshop=ws)
            eq = Equipment.objects.create(
                description=self.description, model=f"MOD-{mark}", serial_number=f"SER{tag}-{mark}",
                department=dept, workshop=ws, status="Working",
            )
            tech = User.objects.create_user(
                username=f"tech{tag.lower()}", password="pw12345!", first_name=f"Tech{mark}",
            )
            UserProfile.objects.update_or_create(user=tech, defaults={
                "role": "Tech", "workshop": ws, "level": "Engineer",
                "must_change_password": False, "has_uploaded_signature": True,
            })
            nic = User.objects.create_user(username=f"nic{tag.lower()}", password="pw12345!")
            UserProfile.objects.update_or_create(user=nic, defaults={
                "role": "NIC", "department": dept,
                "must_change_password": False, "has_uploaded_signature": True,
            })
            self.records[tag] = {
                "ws": ws, "dept": dept, "eq": eq, "tech": tech, "nic": nic,
                "ppm": PPMSchedule.objects.create(equipment=eq, workshop=ws, scheduled_month=month),
                "jc": jobcard.objects.create(
                    department=dept, equipment=eq, workshop=ws, priority_level="Low",
                    action_taken="Repair", job_description=f"JC-{mark}", status="Approved",
                ),
                "acc": Accessories.objects.create(
                    equipment_description=self.description, workshop=ws, note=f"ACC-{mark}",
                ),
                "tool": Tools.objects.create(workshop=ws, serial_number=f"TOOL{tag}-{mark}"),
            }
        self.client.raise_request_exception = False

    # ── helpers ────────────────────────────────────────────────────────────
    def _b_id_for(self, route, param):
        b = self.records["B"]
        return {
            "workshop_id": b["ws"].id, "dept_id": b["dept"].id, "jobcard_id": b["jc"].id,
            "user_id": b["tech"].id, "equipment_id": b["eq"].id, "schedule_id": b["ppm"].id,
            "pk": (b["eq"].id if route.startswith("Inventory")
                   else b["tool"].id if route.startswith("accessories") and "tool" in route
                   else b["acc"].id if route.startswith("accessories") else None),
        }.get(param)

    def _snapshot(self):
        state = {}
        for key, obj in self.records["B"].items():
            fresh = type(obj).objects.filter(pk=obj.pk).first()
            state[key] = None if fresh is None else {
                field: str(value) for field, value in model_to_dict(fresh).items()
                if field not in ("updated_at", "last_login")
            }
        return state

    def _body(self, response):
        return b"" if getattr(response, "streaming", False) else response.content

    # ── tests ──────────────────────────────────────────────────────────────
    def test_no_route_leaks_or_changes_another_workshops_records(self):
        problems = []
        for who in ("tech", "nic"):
            user = self.records["A"][who]
            for route in uuid_routes():
                params = re.findall(r"<uuid:(\w+)>", route)
                ids = [self._b_id_for(route, p) for p in params]
                if None in ids:
                    continue
                url = "/" + route
                for param, value in zip(params, ids):
                    url = url.replace(f"<uuid:{param}>", str(value))
                for method in ("get", "post"):
                    before = self._snapshot()
                    self.client.force_login(user)
                    response = getattr(self.client, method)(url)
                    changed = [k for k, v in self._snapshot().items() if v != before[k]]
                    leaked = response.status_code == 200 and MARKER.encode() in self._body(response)
                    if leaked or changed:
                        problems.append(f"{who} {method.upper()} {url} -> {response.status_code} "
                                        f"leaked={leaked} changed={changed}")
        if problems:
            self.fail("Cross-workshop access:\n  " + "\n  ".join(problems))

    def test_cannot_edit_another_workshops_equipment(self):
        a, b = self.records["A"], self.records["B"]
        self.client.force_login(a["tech"])
        response = self.client.post(reverse("edit_inventory", args=[b["eq"].id]), {
            "description": self.description.id, "department": b["dept"].id,
            "model": "HIJACKED", "serial_number": b["eq"].serial_number, "status": "Not working",
        })
        self.assertEqual(response.status_code, 404)
        b["eq"].refresh_from_db()
        self.assertEqual(b["eq"].model, f"MOD-{MARKER}")

    def test_cannot_move_own_equipment_into_another_workshops_department(self):
        a, b = self.records["A"], self.records["B"]
        self.client.force_login(a["tech"])
        self.client.post(reverse("edit_inventory", args=[a["eq"].id]), {
            "description": self.description.id, "department": b["dept"].id,
            "model": "X", "serial_number": a["eq"].serial_number, "status": "Working",
        })
        a["eq"].refresh_from_db()
        self.assertEqual(a["eq"].department_id, a["dept"].id)

    def test_cannot_add_equipment_to_another_workshops_department(self):
        a, b = self.records["A"], self.records["B"]
        self.client.force_login(a["tech"])
        self.client.post(reverse("add_inventory"), {
            "description": self.description.id, "department": b["dept"].id,
            "model": "NEW", "serial_number": "INJECTED-1", "status": "Working",
        })
        self.assertFalse(Equipment.objects.filter(serial_number="INJECTED-1").exists())

    def test_cannot_transfer_another_workshops_equipment(self):
        a, b = self.records["A"], self.records["B"]
        self.client.force_login(a["tech"])
        response = self.client.post(
            reverse("transfer_equipment", args=[b["eq"].id]), {"target_department_id": a["dept"].id},
        )
        self.assertIn(response.status_code, (403, 404))
        b["eq"].refresh_from_db()
        self.assertEqual(b["eq"].department_id, b["dept"].id)

    def test_signatures_are_downloadable_only_by_owner_or_hod(self):
        b_tech = self.records["B"]["tech"]
        UserSignature.objects.get_or_create(user=b_tech)
        self.client.force_login(self.records["A"]["tech"])
        response = self.client.get(reverse("download_signature", args=[b_tech.id]))
        self.assertEqual(response.status_code, 403)
