"""Sidebar and module tabs: each module is one sidebar entry, its pages are tabs.

Scheduling lives in PPM (maintenance) and Calibration Schedules (calibration
centers); Checklists in Work Orders, for technologists only; Warranties and
Suppliers in Inventory; Failure Risk in Machine Reports.
"""
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from Equiper.context_processors import module_tabs
from Inventory.models import Department
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class NavigationTests(TestCase):
    def setUp(self):
        self.maint = Workshop.objects.create(name="Biomed")
        self.cal = Workshop.objects.create(name="Cal lab", category="calibration_center")
        self.dept = Department.objects.create(name="ICU", workshop=self.maint)
        self.tech = self.user("tech", role="Tech", workshop=self.maint, level="Engineer Incharge")
        self.cal_tech = self.user("caltech", role="Tech", workshop=self.cal, level="Engineer Incharge")
        self.nurse = self.user("nurse", role="NIC", department=self.dept)
        self.hod = self.user("hod", role="HOD")

    def user(self, username, **profile):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "must_change_password": False, "has_uploaded_signature": True, **profile,
        })
        return user

    def tabs(self, user, url, session=None):
        request = RequestFactory().get(url)
        request.user = user
        request.session = SessionStore()
        request.session.update(session or {})
        request.resolver_match = resolve(url.split("?")[0])
        context = module_tabs(request)
        return context.get("module_label"), [t["label"] for t in context.get("module_tabs", [])], \
            [t["label"] for t in context.get("module_tabs", []) if t["active"]]

    # ── tabs ────────────────────────────────────────────────────────────────

    def test_checklists_are_a_work_order_tab_for_technologists_only(self):
        url = reverse("jobcard:create_job_card")
        self.assertIn("Checklists", self.tabs(self.tech, url)[1])
        self.assertNotIn("Checklists", self.tabs(self.nurse, url)[1])
        label, _, active = self.tabs(self.tech, reverse("jobcard:checklist_settings"))
        self.assertEqual((label, active), ("Work Orders", ["Checklists"]))
        self.assertEqual(self.tabs(self.nurse, reverse("jobcard:checklist_settings"))[1], [])

    def test_warranties_and_suppliers_are_inventory_tabs(self):
        label, tabs, active = self.tabs(self.tech, reverse("supplier_list"))
        self.assertEqual(label, "Inventory")
        self.assertEqual(tabs, ["Inventory List", "Summary View", "Analytics & Reports", "Warranties", "Suppliers"])
        self.assertEqual(active, ["Suppliers"])
        self.assertNotIn("Suppliers", self.tabs(self.nurse, reverse("warranty_list"))[1])

    def test_failure_risk_is_a_machine_reports_tab(self):
        label, tabs, active = self.tabs(self.hod, reverse("failure_risk"))
        self.assertEqual((label, tabs[0], active), ("Machine Reports", "Dashboard", ["Failure Risk"]))

    def test_scheduling_is_a_tab_of_ppm_or_calibration(self):
        url = reverse("scheduling:overview")
        label, tabs, active = self.tabs(self.tech, url, {"scheduling_program": "ppm"})
        self.assertEqual((label, tabs[-1], active), ("PPM", "Scheduling Plan", ["Scheduling Plan"]))
        label, tabs, active = self.tabs(self.cal_tech, url, {"scheduling_program": "calibration"})
        self.assertEqual((label, active), ("Calibration", ["Scheduling Plan"]))
        self.assertIn("Calibration Schedules", tabs)
        # HODs have neither page; they reach Scheduling from the sidebar.
        self.assertEqual(self.tabs(self.hod, url, {"scheduling_program": "ppm"})[1], [])

    def test_pages_with_their_own_section_tabs_get_none_added(self):
        for name in ("inventory", "ppm_dashboard", "equipment_dashboard"):
            label, tabs, _ = self.tabs(self.tech, reverse(name))
            self.assertTrue(label, name)
            self.assertEqual(tabs, [], name)

    # ── sidebar ─────────────────────────────────────────────────────────────

    def sidebar_links(self, user):
        self.client.force_login(user)
        html = self.client.get(reverse("warranty_list")).content.decode()
        start = html.index('id="equiperSidebar"')
        return html[start:html.index("</aside>", start)]

    def test_sidebar_lists_modules_not_their_pages(self):
        sidebar = self.sidebar_links(self.tech)
        for moved in ("warranty_list", "supplier_list", "failure_risk", "jobcard:checklist_settings",
                      "scheduling:overview", "jobcard:waiting_jobcards"):
            self.assertNotIn(f'href="{reverse(moved)}"', sidebar, moved)
        for kept in ("inventory", "equipment_dashboard", "jobcard:create_job_card", "ppm_dashboard"):
            self.assertIn(f'href="{reverse(kept)}"', sidebar, kept)

    def test_hods_keep_scheduling_in_the_sidebar(self):
        self.assertIn(f'href="{reverse("scheduling:overview")}"', self.sidebar_links(self.hod))
