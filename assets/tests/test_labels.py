"""Inventory > QR labels: new equipment, equipment whose label no longer
matches (above all a move to another department), specific machines, and
never deleted equipment."""
from django.urls import reverse
from django.utils import timezone

from Inventory.models import Equipment

from .test_assets import AssetsBase

URL = reverse("equipment_labels")


class LabelTests(AssetsBase):
    def setUp(self):
        super().setUp()
        # The base fixture's pump already has a current label, so each test
        # starts from one new machine: the ventilator.
        Equipment.objects.filter(pk=self.pump.pk).update(
            label_printed_at=timezone.now(), label_snapshot=self.pump.label_content())
        self.client.force_login(self.lead)

    def _print(self, *machines, mark=True):
        data = {"equipment": [m.pk for m in machines]}
        if not mark:
            data["mark_printed"] = ["0"]
        response = self.client.post(URL, data)
        self.assertEqual(response["Content-Type"], "application/pdf")
        return response

    def _listed(self, **params):
        return [m.pk for m in self.client.get(URL, params).context["page"].object_list]

    def test_new_equipment_is_offered_until_printed(self):
        self.assertEqual(self._listed(), [self.vent.pk])
        self._print(self.vent)
        self.vent.refresh_from_db()
        self.assertIsNotNone(self.vent.label_printed_at)
        self.assertEqual(self.vent.label_snapshot["department"], "ICU")
        self.assertEqual(self._listed(), [])
        self.assertContains(self.client.get(URL), "Every label is up to date")

    def test_a_move_to_another_department_puts_it_back_with_what_changed(self):
        self._print(self.vent)
        Equipment.objects.filter(pk=self.vent.pk).update(department=self.renal)
        page = self.client.get(URL)
        self.assertEqual([m.pk for m in page.context["page"].object_list], [self.vent.pk])
        self.assertContains(page, "Changed")
        self.assertContains(page, "<s>ICU</s> → <b>Renal</b>", html=False)
        self.assertEqual(self._listed(scope="new"), [])
        self.assertEqual(self._listed(scope="changed"), [self.vent.pk])

    def test_printing_without_marking_keeps_it_on_the_list(self):
        self._print(self.vent, mark=False)
        self.vent.refresh_from_db()
        self.assertIsNone(self.vent.label_printed_at)
        self.assertEqual(self._listed(), [self.vent.pk])

    def test_specific_equipment_is_found_whatever_its_label_state(self):
        self._print(self.vent)
        other = self._machine("PUMP-9", self.renal, self.vent_type)
        self.assertEqual(self._listed(q="vent-1"), [self.vent.pk])
        self.assertEqual(self._listed(q="pump-9"), [other.pk])

    def test_deleted_equipment_is_never_offered_or_printed(self):
        gone = self._machine("GONE-1", self.icu, self.vent_type)
        Equipment.objects.filter(pk=gone.pk).update(pending_delete=True, active_status=False)
        self.assertNotIn(gone.pk, self._listed(scope="all"))
        self.assertNotIn(gone.pk, self._listed(q="GONE"))
        response = self.client.post(URL, {"equipment": [gone.pk]})
        self.assertEqual(response.status_code, 302)
        gone.refresh_from_db()
        self.assertIsNone(gone.label_printed_at)

    def test_pending_delete_alone_is_enough_to_leave_it_out(self):
        half = self._machine("HALF-1", self.icu, self.vent_type)
        Equipment.objects.filter(pk=half.pk).update(pending_delete=True)
        self.assertNotIn(half.pk, self._listed(scope="all"))

    def test_marking_printed_moves_updated_at_so_it_syncs(self):
        before = Equipment.objects.get(pk=self.vent.pk).updated_at
        self._print(self.vent)
        self.assertGreater(Equipment.objects.get(pk=self.vent.pk).updated_at, before)

    def test_other_workshops_machines_are_not_printable(self):
        from workshop.models import Workshop
        from Inventory.models import Department
        elsewhere = Department.objects.create(name="Far", workshop=Workshop.objects.create(name="Other"))
        foreign = self._machine("FAR-1", elsewhere, self.vent_type)
        self.assertNotIn(foreign.pk, self._listed(scope="all"))
        self.assertEqual(self.client.post(URL, {"equipment": [foreign.pk]}).status_code, 302)

    def test_the_old_address_redirects_to_inventory(self):
        response = self.client.get(reverse("assets:labels") + "?scope=new")
        self.assertRedirects(response, URL + "?scope=new", fetch_redirect_response=False)

    def test_it_is_a_tab_of_inventory_and_the_machine_page_links_to_it(self):
        page = self.client.get(URL)
        self.assertEqual(page.context["module_label"], "Inventory")
        self.assertContains(self.client.get(reverse("inventory")), f'href="{URL}"')
        machine = self.client.get(reverse("assets:machine", args=[self.vent.pk]))
        self.assertContains(machine, f'{URL}?q=VENT-1')
