"""Route smoke test: every parameterless GET route answers under 500 for every role.

Walks the project URLconf, requests each route as each kind of user, and
fails with the full list of routes that raised or returned a 5xx. It exists
so a missing template, a bad ``reverse()`` or an unguarded ``None`` in a view
is caught in CI instead of by a user (IMPROVEMENT_PLAN.md section 3.2).

Routes with URL parameters cannot be walked without real objects, so two
static checks cover what they reference instead: every URL name passed to
``redirect``/``reverse``/``{% url %}`` must resolve, and every template name a
view renders must exist.
"""
import re
import sys
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.signals import got_request_exception
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import URLResolver, get_resolver

from Inventory.models import Department
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()

# Prefixes that are not application pages.
SKIP_PREFIXES = ("admin/", "__debug__/", "static/", "media/")

# Routes whose GET handler ends the session; requesting them mid-walk would
# only log the client out. They are still requested, just last.
LOGOUT_ROUTES = {"login/logout/", "dashboard/log-out/"}

# The only routes an anonymous visitor may load successfully.
PUBLIC_ROUTES = {"login/", "login/forgot-password/", "health/"}


def parameterless_routes():
    """Yield every distinct route in the URLconf that takes no arguments."""
    seen = set()

    def walk(patterns, prefix):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns, route)
                continue
            if "<" in route or "(?P" in route or route.startswith(SKIP_PREFIXES):
                continue
            if route not in seen:
                seen.add(route)
                yield route

    routes = list(walk(get_resolver().url_patterns, ""))
    return sorted(routes, key=lambda r: r in LOGOUT_ROUTES)


@override_settings(DEBUG=False, DEBUG_PROPAGATE_EXCEPTIONS=False)
class RouteSmokeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(name="Radiology")
        cls.department = Department.objects.create(name="CT", workshop=cls.workshop)
        cls.users = {
            "HOD": cls._make_user("smoke_hod", "HOD"),
            "NIC": cls._make_user("smoke_nic", "NIC", department=cls.department),
            "NIC without department": cls._make_user(
                "smoke_nic_nodept", "NIC", department=cls.department
            ),
            "Tech": cls._make_user(
                "smoke_tech", "Tech", workshop=cls.workshop, level="Engineer"
            ),
        }
        # save() refuses an NIC with no department, but deleting the department
        # (on_delete=SET_NULL) leaves exactly that behind, so views must cope.
        UserProfile.objects.filter(user=cls.users["NIC without department"]).update(
            department=None
        )

    @classmethod
    def _make_user(cls, username, role, **profile):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                "role": role,
                "must_change_password": False,
                "has_uploaded_signature": True,
                **profile,
            },
        )
        return user

    def _walk(self, user):
        """Request every route; return a list of 'route -> problem' strings.

        For an anonymous client a 200 on a non-public route is also a problem:
        it means the view is missing its login check."""
        failures = []
        errors = []

        def record(sender, request=None, **kwargs):
            errors.append(sys.exc_info()[1])

        self.client.raise_request_exception = False
        got_request_exception.connect(record)
        try:
            for route in parameterless_routes():
                if user is not None:
                    self.client.force_login(user)
                errors.clear()
                response = self.client.get("/" + route)
                if response.status_code >= 500:
                    detail = f"{type(errors[0]).__name__}: {errors[0]}" if errors else ""
                    failures.append(f"/{route} -> {response.status_code} {detail}")
                elif user is None and response.status_code == 200 and route not in PUBLIC_ROUTES:
                    failures.append(f"/{route} -> 200 for an anonymous user")
        finally:
            got_request_exception.disconnect(record)
        return failures

    def test_routes_under_500_for_every_role(self):
        # Nothing in a smoke test may reach HQ or another host.
        with mock.patch("requests.sessions.Session.request",
                        side_effect=ConnectionError("network disabled in tests")):
            for label, user in self.users.items():
                with self.subTest(role=label):
                    failures = self._walk(user)
                    self._assert_no_failures(label, failures)

    def test_anonymous_gets_no_errors_and_no_private_pages(self):
        with mock.patch("requests.sessions.Session.request",
                        side_effect=ConnectionError("network disabled in tests")):
            failures = self._walk(None)
        self._assert_no_failures("anonymous", failures)

    def _assert_no_failures(self, label, failures):
        if failures:
            self.fail(f"{label}: {len(failures)} failing routes\n  " + "\n  ".join(failures))


# Application source roots scanned by the static checks.
APP_DIRS = [
    "Inventory", "ppms", "parts_tools", "users", "dashboard", "jobcard", "reporthub",
    "workshop", "CalSoft", "calSchedules", "updates", "machineReports", "audit_log",
    "accounts", "core", "Equiper", "templates",
]
SKIP_DIRS = {"migrations", "__pycache__", "tests"}

URL_NAME_IN_PY = re.compile(r"""(?:redirect|reverse|reverse_lazy)\(\s*['"]([\w:\-]+)['"]""")
URL_NAME_IN_TEMPLATE = re.compile(r"""{%\s*url\s+['"]([\w:\-]+)['"]""")
TEMPLATE_IN_PY = re.compile(r"""['"]([\w\-/ &]+\.html)['"]""")
TEMPLATE_IN_TEMPLATE = re.compile(r"""{%\s*(?:include|extends)\s+['"]([^'"]+)['"]""")

# Template names that appear in Python but are not rendered by a view.
# Helper code in CalSoft that is not wired to any route yet.
UNROUTED_TEMPLATE_REFERENCES = {
    "calibration/certificate_template.html",
    "calibration/uncertainty_budget_template.html",
    "calibration/widgets/uncertainty_budget.html",
    "calibration/partials/uncertainty_component.html",
    "calibration/partials/reading_row.html",
}


def _source_lines():
    base = Path(settings.BASE_DIR)
    for app in APP_DIRS:
        for path in sorted((base / app).rglob("*")):
            if path.suffix not in (".py", ".html") or SKIP_DIRS & set(path.parts):
                continue
            if path.name.startswith("test"):
                continue
            for lineno, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                yield path.relative_to(base), path.suffix, lineno, line


class StaticReferenceTest(SimpleTestCase):
    def test_every_referenced_url_name_exists(self):
        known = set()

        def walk(patterns, namespace):
            for pattern in patterns:
                if isinstance(pattern, URLResolver):
                    inner = pattern.namespace
                    walk(pattern.url_patterns,
                         f"{namespace}:{inner}" if namespace and inner else inner or namespace)
                elif pattern.name:
                    known.add(f"{namespace}:{pattern.name}" if namespace else pattern.name)

        walk(get_resolver().url_patterns, "")

        missing = []
        for path, suffix, lineno, line in _source_lines():
            regex = URL_NAME_IN_PY if suffix == ".py" else URL_NAME_IN_TEMPLATE
            for match in regex.finditer(line):
                if match.group(1) not in known:
                    missing.append(f"{path}:{lineno}: {match.group(1)}")
        if missing:
            self.fail("URL names that do not exist:\n  " + "\n  ".join(missing))

    def test_every_referenced_template_exists(self):
        missing = []
        for path, suffix, lineno, line in _source_lines():
            regex = TEMPLATE_IN_PY if suffix == ".py" else TEMPLATE_IN_TEMPLATE
            for match in regex.finditer(line):
                name = match.group(1)
                if name in UNROUTED_TEMPLATE_REFERENCES:
                    continue
                try:
                    get_template(name)
                except TemplateDoesNotExist:
                    missing.append(f"{path}:{lineno}: {name}")
        if missing:
            self.fail("Templates that do not exist:\n  " + "\n  ".join(missing))
