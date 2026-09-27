"""Work order checklists: templates per equipment type, completed per work order."""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from Inventory.models import Department, Equipment, EquipmentDescription
from jobcard.checklists import ChecklistIncomplete, completed_from_post, parse_template, summary
from jobcard.models import jobcard
from jobcard.modern_jobcard_pdf import ModernJobCardPDFGenerator, generate_jobcard_pdf
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()

SIGNATURE = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
ITEMS = ["Check alarms", "Electrical safety test", "Clean filters"]


class ChecklistParsingTests(SimpleTestCase):
    def test_one_item_per_line_trimmed_and_deduplicated(self):
        text = "  Check alarms \n\nElectrical   safety test\ncheck ALARMS\n"
        self.assertEqual(parse_template(text), ["Check alarms", "Electrical safety test"])

    def test_items_are_capped(self):
        self.assertEqual(len(parse_template("\n".join(f"Item {i}" for i in range(80)))), 50)

    def test_item_text_comes_from_the_template_not_the_form(self):
        post = {"checklist_result_0": "pass", "checklist_result_1": "fail", "checklist_note_1": " worn  seal ",
                "checklist_result_2": "na", "checklist_item_0": "forged text"}
        self.assertEqual(completed_from_post(post, ITEMS), [
            {"item": "Check alarms", "result": "pass", "note": ""},
            {"item": "Electrical safety test", "result": "fail", "note": "worn seal"},
            {"item": "Clean filters", "result": "na", "note": ""},
        ])

    def test_every_item_needs_a_result(self):
        with self.assertRaises(ChecklistIncomplete) as ctx:
            completed_from_post({"checklist_result_0": "pass", "checklist_result_1": "maybe"}, ITEMS)
        self.assertIn("Electrical safety test", str(ctx.exception))
        self.assertIn("Clean filters", str(ctx.exception))

    def test_summary_counts_results(self):
        self.assertEqual(summary([{"result": "pass"}, {"result": "fail"}, {"result": "pass"}]),
                         {"pass": 2, "fail": 1, "na": 0, "total": 3})


class ChecklistWorkOrderTests(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        self.department = Department.objects.create(name="ICU", workshop=self.workshop)
        self.description = EquipmentDescription.objects.create(name="Ventilator", checklist_template=ITEMS)
        self.equipment = Equipment.objects.create(description=self.description, model="V1", serial_number="VENT-1",
                                                  department=self.department, workshop=self.workshop,
                                                  status="Working")
        self.tech = self._user("cl_tech", "Tech", workshop=self.workshop, level="Engineer")
        self.lead = self._user("cl_lead", "Tech", workshop=self.workshop, level="Engineer Incharge")
        self.hod = self._user("cl_hod", "HOD")
        self.nic = self._user("cl_nic", "NIC", department=self.department)

    def _user(self, username, role, **profile):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile})
        return user

    def _raise(self, **checklist_fields):
        self.client.force_login(self.tech)
        return self.client.post(reverse("jobcard:create_job_card"), {
            "priority_level": "High", "department": self.department.id, "equipment": self.equipment.id,
            "job_description": "Service", "action_taken": "PPM", "time_started": "09:00",
            "time_completed": "10:00", "signature_data": SIGNATURE, "spare_parts_data": "[]",
            "labor_cost": "0.00", "additional_costs": "0.00", **checklist_fields,
        })

    def test_completed_checklist_is_saved_on_the_work_order(self):
        self._raise(checklist_result_0="pass", checklist_result_1="fail", checklist_note_1="leak current high",
                    checklist_result_2="na")
        card = jobcard.objects.get(equipment=self.equipment)
        self.assertEqual([e["result"] for e in card.checklist], ["pass", "fail", "na"])
        self.assertEqual(card.checklist[1]["note"], "leak current high")

    def test_incomplete_checklist_is_refused_and_answers_kept(self):
        response = self._raise(checklist_result_0="pass")
        self.assertFalse(jobcard.objects.filter(equipment=self.equipment).exists())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mark every checklist item")
        self.assertEqual(response.context["form_data"]["checklist_values"]["0"]["result"], "pass")

    def test_equipment_without_a_checklist_needs_none(self):
        self.description.checklist_template = []
        self.description.save()
        self._raise()
        self.assertEqual(jobcard.objects.get(equipment=self.equipment).checklist, [])

    def test_equipment_list_carries_the_checklist(self):
        self.client.force_login(self.tech)
        data = self.client.get(reverse("jobcard:load_equipment"), {"department_id": self.department.id}).json()
        self.assertEqual(data[0]["checklist"], ITEMS)

    def test_checklist_is_printed_on_the_pdf(self):
        self._raise(checklist_result_0="pass", checklist_result_1="pass", checklist_result_2="fail")
        card = jobcard.objects.get(equipment=self.equipment)
        block = ModernJobCardPDFGenerator(card)._create_checklist_block()
        table = block[2]
        self.assertEqual(len(table._cellvalues), 1 + len(ITEMS))
        self.assertEqual(table._cellvalues[3][2], "Fail")
        self.assertTrue(generate_jobcard_pdf(card))

    def test_engineer_in_charge_and_hod_edit_templates(self):
        for editor in (self.lead, self.hod):
            self.client.force_login(editor)
            self.client.post(reverse("jobcard:checklist_templates"),
                             {"description_id": self.description.id, "items": f"Item by {editor.username}\n"})
            self.description.refresh_from_db()
            self.assertEqual(self.description.checklist_template, [f"Item by {editor.username}"])

    def test_engineer_and_in_charge_cannot_edit_templates(self):
        for user in (self.tech, self.nic):
            self.client.force_login(user)
            self.client.post(reverse("jobcard:checklist_templates"),
                             {"description_id": self.description.id, "items": "Replaced"})
            self.description.refresh_from_db()
            self.assertEqual(self.description.checklist_template, ITEMS)

    def test_template_page_lists_equipment_types(self):
        self.client.force_login(self.tech)
        response = self.client.get(reverse("jobcard:checklist_templates"), {"edit": self.description.id})
        self.assertContains(response, "Ventilator")
        self.assertContains(response, "Electrical safety test")
