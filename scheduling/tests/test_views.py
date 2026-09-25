"""The scheduling screens: who may see and change what, every page renders,
and a plan can be drafted, edited, previewed and activated through the UI."""
from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from Inventory.models import Department, Equipment, EquipmentDescription
from calSchedules.models import CalibrationSchedule
from ppms.models import PPMSchedule
from scheduling import planner
from scheduling.models import SchedulingPlan
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class ViewTestBase(TestCase):
    def setUp(self):
        self.ws = Workshop.objects.create(name="Biomed")
        self.other_ws = Workshop.objects.create(name="Other")
        self.cal_ws = Workshop.objects.create(name="Calibration", category="calibration_center")
        self.icu = Department.objects.create(name="ICU", workshop=self.ws)
        self.monitor = EquipmentDescription.objects.create(name="Patient Monitor")
        self.equipment = []
        for i in range(3):
            eq = Equipment.objects.create(description=self.monitor, department=self.icu, workshop=self.ws,
                                          model="M", serial_number=f"V-{i}", status="Working")
            PPMSchedule.objects.filter(equipment=eq).delete()
            CalibrationSchedule.objects.filter(equipment=eq).delete()
            self.equipment.append(eq)
        # Tests set up their own plans: drop the ones given automatically.
        SchedulingPlan.objects.filter(workshop__in=[self.ws, self.cal_ws]).delete()
        self.hod = self.user("hod", role="HOD")
        self.incharge = self.user("incharge", role="Tech", workshop=self.ws, level="Engineer Incharge")
        self.tech = self.user("tech", role="Tech", workshop=self.ws, level="Engineer")
        self.outsider = self.user("outsider", role="Tech", workshop=self.other_ws, level="Engineer Incharge")
        self.nic = self.user("nic", role="NIC", department=self.icu)

    def user(self, username, **profile):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "must_change_password": False, "has_uploaded_signature": True, **profile,
        })
        return user

    def get(self, user, name, *args, query=None):
        self.client.force_login(user)
        url = reverse(f"scheduling:{name}", args=args)
        q = query if query is not None else f"?workshop={self.ws.id}&program=ppm"
        return self.client.get(url + q)

    def active_plan(self, months=(2, 8)):
        plan = SchedulingPlan.objects.create(workshop=self.ws, program="ppm", logic="description",
                                             version=1, state="active", default_interval_months=6)
        rule = plan.rules.model(plan=plan)
        rule.months = months
        rule.description = self.monitor
        rule.save()
        planner.schedule(plan)
        return plan


class AccessTests(ViewTestBase):
    def test_nurse_in_charge_is_refused(self):
        self.assertEqual(self.get(self.nic, "overview").status_code, 403)

    def test_technician_can_view_but_not_change(self):
        self.assertEqual(self.get(self.tech, "overview").status_code, 200)
        self.client.force_login(self.tech)
        response = self.client.post(reverse("scheduling:plan_new") + f"?workshop={self.ws.id}&program=ppm",
                                    {"logic": "description", "source": "blank"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SchedulingPlan.objects.exists())

    def test_other_workshops_plans_are_hidden(self):
        plan = self.active_plan()
        self.assertEqual(self.get(self.outsider, "plan", plan.id).status_code, 404)
        self.assertEqual(self.get(self.outsider, "equipment", self.equipment[0].id).status_code, 404)

    def test_technician_cannot_pick_another_workshop(self):
        response = self.get(self.outsider, "overview")
        self.assertEqual(response.context["scope"].workshop, self.other_ws)

    def test_a_maintenance_workshop_has_no_calibration_scheduling(self):
        response = self.get(self.incharge, "overview",
                            query=f"?workshop={self.ws.id}&program=calibration")
        scope = response.context["scope"]
        self.assertEqual((scope.workshop, scope.program), (self.ws, "ppm"))
        self.assertEqual([k for k, _ in scope.programs], ["ppm"])

    def test_the_calibration_center_schedules_calibration_for_the_whole_hospital(self):
        cal_tech = self.user("caltech", role="Tech", workshop=self.cal_ws, level="Engineer Incharge")
        response = self.get(cal_tech, "overview", query=f"?workshop={self.cal_ws.id}&program=ppm")
        scope = response.context["scope"]
        self.assertEqual((scope.workshop, scope.program), (self.cal_ws, "calibration"))
        self.assertEqual(response.context["figures"]["total"], 3)   # Biomed's devices
        # Its device pages show calibration only.
        page = self.get(cal_tech, "equipment", self.equipment[0].id, query="")
        self.assertEqual([p["key"] for p in page.context["programs"]], ["calibration"])

    def test_the_hod_toggle_goes_to_the_workshop_that_plans_it(self):
        response = self.get(self.hod, "overview", query=f"?workshop={self.ws.id}&program=calibration")
        scope = response.context["scope"]
        self.assertEqual((scope.workshop, scope.program), (self.cal_ws, "calibration"))


class PageTests(ViewTestBase):
    def test_every_page_renders_without_a_plan(self):
        for name, args in [("overview", ()), ("months", ()), ("month", (2027, 2)),
                           ("unscheduled", ()), ("plans", ()), ("equipment", (self.equipment[0].id,))]:
            with self.subTest(name):
                self.assertEqual(self.get(self.hod, name, *args).status_code, 200)
        response = self.get(self.hod, "unscheduled")
        self.assertContains(response, "No plan yet")

    def test_every_page_renders_with_a_plan(self):
        plan = self.active_plan()
        month = PPMSchedule.objects.first().scheduled_month
        for name, args in [("overview", ()), ("months", ()), ("month", (month.year, month.month)),
                           ("unscheduled", ()), ("plans", ()), ("plan", (plan.id,)),
                           ("equipment", (self.equipment[0].id,))]:
            with self.subTest(name):
                self.assertEqual(self.get(self.hod, name, *args).status_code, 200)

        response = self.get(self.hod, "month", month.year, month.month)
        self.assertContains(response, "first visit")
        self.assertContains(response, "V-0")
        response = self.get(self.hod, "equipment", self.equipment[0].id)
        self.assertContains(response, month.strftime("%B %Y"))

    def test_overview_counts(self):
        plan = self.active_plan()
        # Report a group the plan has no months for (rather than adopt it).
        plan.auto_new_groups = False
        plan.save(update_fields=["auto_new_groups"])
        extra = Equipment.objects.create(description=EquipmentDescription.objects.create(name="Unruled"),
                                         department=self.icu, workshop=self.ws, model="M",
                                         serial_number="V-X", status="Working")
        response = self.get(self.hod, "overview")
        figures = response.context["figures"]
        self.assertEqual(figures["total"], 4)
        self.assertEqual(figures["scheduled"], 3)
        self.assertEqual(figures["unscheduled"], 1)
        self.assertEqual(figures["no_months"], 1)
        self.assertEqual(figures["conflicts"], 0)
        response = self.get(self.hod, "unscheduled")
        self.assertContains(response, extra.serial_number)
        self.assertContains(response, "Unruled has no months")

    def test_overview_query_count_does_not_grow_with_equipment(self):
        self.active_plan()
        self.get(self.hod, "overview")  # warm per-process caches (session, lookups)
        with CaptureQueriesContext(connection) as few:
            self.get(self.hod, "overview")
        Equipment.objects.bulk_create([
            Equipment(description=self.monitor, department=self.icu, workshop=self.ws, model="M",
                      serial_number=f"BULK-{i}", status="Working") for i in range(300)])
        planner.schedule(planner.active_plan(self.ws.id, "ppm"))
        with CaptureQueriesContext(connection) as many:
            self.get(self.hod, "overview")
        self.assertEqual(len(few), len(many))


class PlanFlowTests(ViewTestBase):
    def test_draft_edit_preview_activate(self):
        self.client.force_login(self.incharge)
        query = f"?workshop={self.ws.id}&program=ppm"
        response = self.client.post(reverse("scheduling:plan_new") + query,
                                    {"logic": "description", "source": "blank", "default_interval": "6"})
        plan = SchedulingPlan.objects.get()
        self.assertRedirects(response, reverse("scheduling:plan", args=[plan.id]))
        self.assertEqual(plan.state, "draft")

        # Tick February and August for patient monitors, then preview.
        response = self.client.post(reverse("scheduling:plan", args=[plan.id]), {
            "group": [str(self.monitor.id)],
            f"months_{self.monitor.id}": ["2", "8"],
            "interval_description": [str(self.monitor.id)],
            f"interval_{self.monitor.id}": "6",
            "default_interval": "6", "notes": "first plan", "then": "preview",
        })
        self.assertRedirects(response, reverse("scheduling:plan_preview", args=[plan.id]))
        rule = plan.rules.get()
        self.assertEqual(rule.months, [2, 8])
        self.assertEqual(plan.intervals.get().interval_months, 6)

        response = self.client.get(reverse("scheduling:plan_preview", args=[plan.id]))
        self.assertEqual(len(response.context["new_placements"]), 3)
        self.assertFalse(PPMSchedule.objects.exists())

        # Activation needs the confirmation box.
        self.client.post(reverse("scheduling:plan_activate", args=[plan.id]))
        plan.refresh_from_db()
        self.assertEqual(plan.state, "draft")

        self.client.post(reverse("scheduling:plan_activate", args=[plan.id]), {"confirm": "yes"})
        plan.refresh_from_db()
        self.assertEqual(plan.state, "active")
        self.assertEqual(plan.activated_by, self.incharge)
        self.assertEqual(PPMSchedule.open_schedules().count(), 3)
        self.assertEqual({s.scheduled_month.month for s in PPMSchedule.objects.all()} - {2, 8}, set())

        # The active version is read only.
        response = self.client.post(reverse("scheduling:plan", args=[plan.id]), {"group": []})
        self.assertEqual(response.status_code, 403)

    def test_unticking_all_months_removes_the_rule(self):
        plan = SchedulingPlan.objects.create(workshop=self.ws, program="ppm", logic="description", version=1)
        rule = plan.rules.model(plan=plan, description=self.monitor)
        rule.months = [3]
        rule.save()
        self.client.force_login(self.incharge)
        self.client.post(reverse("scheduling:plan", args=[plan.id]), {"group": [str(self.monitor.id)]})
        self.assertFalse(plan.rules.exists())

    def test_new_draft_copies_the_active_plan(self):
        active = self.active_plan(months=(3, 9))
        self.client.force_login(self.incharge)
        self.client.post(reverse("scheduling:plan_new") + f"?workshop={self.ws.id}&program=ppm",
                         {"logic": "description", "source": "copy"})
        draft = SchedulingPlan.objects.get(state="draft")
        self.assertEqual(draft.version, active.version + 1)
        self.assertEqual(draft.rules.get().months, [3, 9])

    def test_only_one_draft_at_a_time(self):
        self.client.force_login(self.incharge)
        url = reverse("scheduling:plan_new") + f"?workshop={self.ws.id}&program=ppm"
        self.client.post(url, {"logic": "description", "source": "blank"})
        self.client.post(url, {"logic": "department", "source": "blank"})
        self.assertEqual(SchedulingPlan.objects.count(), 1)

    def test_discard_draft(self):
        plan = SchedulingPlan.objects.create(workshop=self.ws, program="ppm", logic="description", version=1)
        self.client.force_login(self.incharge)
        self.client.post(reverse("scheduling:plan_delete", args=[plan.id]))
        self.assertFalse(SchedulingPlan.objects.exists())

    def test_month_detail_filters(self):
        self.active_plan()
        month = PPMSchedule.objects.first().scheduled_month
        response = self.get(self.hod, "month", month.year, month.month,
                            query=f"?workshop={self.ws.id}&program=ppm&status=completed")
        self.assertEqual(response.context["count"], 0)
        self.assertEqual(date(month.year, month.month, 1), response.context["month"])
