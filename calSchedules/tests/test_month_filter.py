"""The schedules pages show one month, the current one by default, and every
count on them is of what the filters leave, not of the whole estate."""
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from calSchedules.models import CalibrationSchedule
from core.eat import today_eat
from Inventory.models import Department, Equipment, EquipmentDescription
from ppms.models import PPMSchedule
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class MonthFilterBase(TestCase):
    category = "calibration_center"

    def setUp(self):
        self.this_month = today_eat().replace(day=1)
        self.next_month = self.this_month + relativedelta(months=1)
        self.workshop = Workshop.objects.create(name="W", category=self.category)
        self.lab = Department.objects.create(name="Lab", workshop=self.workshop)
        self.ward = Department.objects.create(name="Ward", workshop=self.workshop)
        self.desc = EquipmentDescription.objects.create(name="Scale")
        user = User.objects.create_user(username="tech", password="pass")
        UserProfile.objects.update_or_create(
            user=user, defaults={"role": "Tech", "level": "Engineer Incharge", "workshop": self.workshop},
        )
        self.client.login(username="tech", password="pass")
        self.serial = 0

    def equipment(self, dept):
        self.serial += 1
        return Equipment.objects.create(
            department=dept, workshop=self.workshop, description=self.desc,
            model="M1", serial_number=f"SN{self.serial}", status="Working",
        )


class CalibrationMonthFilterTest(MonthFilterBase):
    def setUp(self):
        super().setUp()
        for dept, month, status in [
            (self.lab, self.this_month, "pending"),
            (self.lab, self.this_month, "completed"),
            (self.ward, self.this_month, "pushed"),
            (self.lab, self.next_month, "pending"),
        ]:
            CalibrationSchedule.objects.create(
                equipment=self.equipment(dept), workshop=self.workshop,
                scheduled_month=month, status=status, calibration_period=12,
            )
        # Adding equipment lets the planner schedule it; keep only these.
        CalibrationSchedule.objects.exclude(generation_source="manual").delete()

    def test_defaults_to_current_month(self):
        r = self.client.get(reverse("schedule:calibration_dashboard"))
        self.assertEqual(r.context["schedules"].paginator.count, 2)  # open, this month
        self.assertEqual(r.context["completed_schedules"].paginator.count, 1)
        self.assertEqual(r.context["stats"]["pending"], 2)
        self.assertEqual(r.context["stats"]["completed"], 1)
        self.assertFalse(r.context["period_all"])

    def test_other_month_and_all(self):
        url = reverse("schedule:calibration_dashboard")
        r = self.client.get(url, {"month": self.next_month.month, "year": self.next_month.year})
        self.assertEqual(r.context["stats"]["pending"], 1)
        self.assertEqual(r.context["stats"]["completed"], 0)
        r = self.client.get(url, {"period": "all"})
        self.assertEqual(r.context["stats"]["pending"], 3)
        self.assertEqual(r.context["stats"]["completed"], 1)

    def test_department_counts(self):
        r = self.client.get(reverse("schedule:calibration_by_department", args=[self.ward.id]))
        self.assertEqual(r.context["stats"]["pending"], 1)
        self.assertEqual(r.context["stats"]["completed"], 0)
        self.assertIn(f"department={self.ward.id}", r.context["filter_query"])

    def test_ajax_tabs_follow_the_filters(self):
        pending = self.client.get(reverse("schedule:ajax_schedules")).json()["meta"]["count"]
        completed = self.client.get(reverse("schedule:ajax_completed_schedules")).json()["meta"]["count"]
        self.assertEqual((pending, completed), (2, 1))
        lab = self.client.get(reverse("schedule:ajax_schedules"), {"department": self.lab.id}).json()
        self.assertEqual(lab["meta"]["count"], 1)
        every = self.client.get(reverse("schedule:ajax_schedules"), {"period": "all"}).json()
        self.assertEqual(every["meta"]["count"], 3)


class PPMMonthFilterTest(MonthFilterBase):
    category = "maintenance"

    def setUp(self):
        super().setUp()
        for dept, month, status in [
            (self.lab, self.this_month, "pending"),
            (self.lab, self.this_month, "completed"),
            (self.ward, self.this_month, "pushed"),
            (self.lab, self.next_month, "pending"),
        ]:
            PPMSchedule.objects.create(
                equipment=self.equipment(dept), workshop=self.workshop,
                scheduled_month=month, status=status,
            )
        self.equipment(self.ward)  # never scheduled
        # Adding equipment lets the planner schedule it; keep only these.
        PPMSchedule.objects.exclude(generation_source="manual").delete()

    def test_summary_counts_are_the_months(self):
        r = self.client.get(reverse("ppm_dashboard"))
        stats = r.context["statistics"]
        self.assertEqual(stats["total_scheduled"], 3)
        self.assertEqual(stats["pending_count"], 1)
        self.assertEqual(stats["completed_count"], 1)
        self.assertEqual(stats["unscheduled"], 1)
        rows = {row["name"]: row for row in r.context["department_breakdown"]}
        self.assertEqual(rows["Lab"]["scheduled"], 2)
        self.assertEqual(rows["Ward"]["scheduled"], 1)

    def test_department_filter_on_dashboard(self):
        r = self.client.get(reverse("ppm_dashboard"), {"department": self.ward.id})
        self.assertEqual(r.context["statistics"]["total_scheduled"], 1)
        self.assertEqual(r.context["statistics"]["unscheduled"], 1)

    def test_department_page_counts(self):
        r = self.client.get(reverse("ppm_by_department", args=[self.lab.id]))
        self.assertEqual(r.context["statistics"]["total_scheduled"], 2)
        r = self.client.get(reverse("ppm_by_department", args=[self.lab.id]),
                            {"month": self.next_month.month, "year": self.next_month.year})
        self.assertEqual(r.context["statistics"]["total_scheduled"], 1)
