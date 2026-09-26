from users.control import get_user_role


def _unread_notification_count(user):
    from CalSoft.models import CalibrationNotification

    return CalibrationNotification.objects.filter(
        recipient=user, is_read=False, active_status=True, pending_delete=False
    ).count()


def _pending_approval_count(role):
    """Mirrors CalSoft.view_modules.pending_sessions.sessions_pending_approval's
    own pending_count query exactly (global, not workshop-scoped) — only
    computed for Tech, the role that sees the Calibration sidebar block."""
    if role != "Tech":
        return 0
    from CalSoft.models import CalibrationSession

    return CalibrationSession.objects.filter(status="pending_review").count()


def _job_cards_waiting_count(user, role):
    """Mirrors jobcard.views.listing.waiting_jobcards' own scoping exactly:
    NIC sees their department's queue, Tech sees their workshop's."""
    if role not in ("Tech", "NIC"):
        return 0
    from jobcard.models import jobcard as JobCard

    profile = getattr(user, "userprofile", None)
    if not profile:
        return 0
    if role == "NIC":
        if not profile.department_id:
            return 0
        return JobCard.objects.filter(status="Waiting Approval", department=profile.department).count()
    if not profile.workshop_id:
        return 0
    return JobCard.objects.filter(status="Waiting Approval", workshop=profile.workshop).count()


def nav_context(request):
    """Expose role-based navigation flags and queue/notification counts to
    every template.

    Single source of truth for "what role is this" — built on the same
    get_user_role() the view-level hod_required/role_required decorators
    use (users/control.py), so the sidebar's visibility can never drift
    from what the views actually enforce.

    Deliberately does NOT resolve workshop category here — that already
    depends on a per-view `selected_workshop` override (e.g. Inventory's
    HOD workshop switcher) that isn't available at the context-processor
    stage, and templates already handle it correctly via
    `{% with ws=selected_workshop|default:user.userprofile.workshop %}`.
    """
    user = getattr(request, "user", None)
    is_authenticated = bool(user and user.is_authenticated)
    role = get_user_role(user) if is_authenticated else None

    return {
        "nav": {
            "role": role,
            "is_hod": role == "HOD",
            "is_tech": role == "Tech",
            "is_nic": role == "NIC",
            "unread_notification_count": _unread_notification_count(user) if is_authenticated else 0,
            "pending_approval_count": _pending_approval_count(role),
            "job_cards_waiting_count": _job_cards_waiting_count(user, role) if is_authenticated else 0,
        }
    }


# ── Module tabs ──────────────────────────────────────────────────────────────
#
# The sidebar lists modules; a module's pages are tabs inside it. PPM,
# Inventory and Machine Reports are single pages whose sections switch in
# place (their own .spa-nav), and their templates link out to the pages that
# belong with them: the scheduling plan, warranties and suppliers, failure
# risk. Those linked pages show the same tabs, with the host's sections
# reached through a #section hash (base.html opens it). Work orders and
# calibration are several ordinary pages that share one tab bar here.
# A page's module is found from its URL name, so views need no changes.

def _tab(label, icon, url, active=False, badge=0):
    return {"label": label, "icon": icon, "url": url, "active": active, "badge": badge}


def _scheduling_program(request):
    from scheduling.models import SchedulingPlan

    # The scheduling views settle the program (a workshop plans only one) and
    # store it before rendering, so the session is the truth, not the URL.
    program = request.session.get("scheduling_program") or request.GET.get("program")
    return program if program in dict(SchedulingPlan.PROGRAM_CHOICES) else None


# Pages that draw their own section tabs.
_SPA_HOSTS = {"ppm_dashboard", "ppm_by_department", "inventory", "inventory_for_hod",
              "inventory_by_department", "equipment_dashboard"}

_WARRANTY_PAGES = {"warranty_list", "warranty_create", "warranty_detail", "warranty_update",
                   "equipment_warranty"}
_SUPPLIER_PAGES = {"supplier_list", "supplier_create", "supplier_update"}
_WORK_ORDER_PAGES = {"jobcard:create_job_card", "jobcard:waiting_jobcards", "jobcard:approved_jobcards",
                     "jobcard:declined_jobcards", "jobcard:work_order_detail"}
_CALIBRATION_PAGES = {"schedule:calibration_dashboard", "schedule:calibration_by_department"}
_PROCEDURE_PAGES = {"calibration:procedure_list", "calibration:procedure_create", "calibration:procedure_detail",
                    "calibration:procedure_edit"}
_STANDARD_PAGES = {"calibration:StandardsParameters_lists", "calibration:standard_list",
                   "calibration:standard_create", "calibration:standard_edit",
                   "calibration:upload_standards_excel"}


def _module(request, role, name):
    from django.urls import reverse

    is_tech, is_hod = role == "Tech", role == "HOD"
    scheduling = name.startswith("scheduling:")
    program = _scheduling_program(request) if scheduling and is_tech else None

    # PPM (maintenance workshops): the PPM page's sections, then its plan.
    if program == "ppm":
        ppm = reverse("ppm_dashboard")
        return "PPM", [
            _tab("Schedules", "fa-calendar-check", ppm + "#schedules"),
            _tab("Summary", "fa-chart-pie", ppm + "#summary"),
            _tab("Analytics", "fa-chart-line", ppm + "#analytics"),
            _tab("Reports", "fa-file-alt", ppm + "#reports"),
            _tab("Scheduling Plan", "fa-calendar-alt", reverse("scheduling:overview") + "?program=ppm", True),
        ]

    # Calibration (calibration centers): schedules and the hospital plan.
    # Perform Calibration has its own entry in the sidebar's Calibration menu.
    if program == "calibration" or name in _CALIBRATION_PAGES:
        return "Calibration", [
            _tab("Calibration Schedules", "fa-calendar-check", reverse("schedule:calibration_dashboard"),
                 name in _CALIBRATION_PAGES),
            _tab("Scheduling Plan", "fa-calendar-alt",
                 reverse("scheduling:overview") + "?program=calibration", scheduling),
        ] if is_tech else []

    # Procedures and standards (calibration centers): creating one comes
    # first (the sidebar opens there), then the list.
    if name in _PROCEDURE_PAGES:
        return "Procedures", [
            _tab("Create Procedure", "fa-plus", reverse("calibration:procedure_create"),
                 name == "calibration:procedure_create"),
            _tab("View Procedures", "fa-list-ul", reverse("calibration:procedure_list"),
                 name != "calibration:procedure_create"),
        ]
    if name in _STANDARD_PAGES:
        return "Standards", [
            _tab("Create Standard", "fa-plus", reverse("calibration:standard_create"),
                 name in ("calibration:standard_create", "calibration:upload_standards_excel")),
            _tab("View Standards", "fa-list-ul", reverse("calibration:StandardsParameters_lists"),
                 name in ("calibration:StandardsParameters_lists", "calibration:standard_list")),
        ]

    # Work orders; technologists also keep their checklists here.
    checklist = is_tech and name.startswith("jobcard:checklist_")
    if checklist or (name in _WORK_ORDER_PAGES and role in ("Tech", "NIC")):
        tabs = [
            _tab("Create Work Order", "fa-plus-circle", reverse("jobcard:create_job_card"),
                 name == "jobcard:create_job_card"),
            _tab("Waiting", "fa-hourglass-half", reverse("jobcard:waiting_jobcards"),
                 name == "jobcard:waiting_jobcards", _job_cards_waiting_count(request.user, role)),
            _tab("Approved", "fa-check-circle", reverse("jobcard:approved_jobcards"),
                 name == "jobcard:approved_jobcards"),
            _tab("Declined", "fa-times-circle", reverse("jobcard:declined_jobcards"),
                 name == "jobcard:declined_jobcards"),
        ]
        if is_tech:
            tabs.append(_tab("Checklists", "fa-clipboard-check", reverse("jobcard:checklist_settings"),
                             checklist))
        return "Work Orders", tabs

    # Inventory: the inventory page's sections, then warranties and suppliers.
    if name in _WARRANTY_PAGES or name in _SUPPLIER_PAGES:
        inv = reverse("inventory")
        tabs = [
            _tab("Inventory List", "fa-list-ul", inv + "#inventory"),
            _tab("Summary View", "fa-chart-pie", inv + "#summary"),
            _tab("Analytics & Reports", "fa-chart-line", inv + "#analytics"),
            _tab("Warranties", "fa-shield-alt", reverse("warranty_list"), name in _WARRANTY_PAGES),
        ]
        if is_tech or is_hod:
            tabs.append(_tab("Suppliers", "fa-truck", reverse("supplier_list"), name in _SUPPLIER_PAGES))
        return "Inventory", tabs

    # Machine reports: its sections, then failure risk.
    if name == "failure_risk" and (is_tech or is_hod):
        mr = reverse("equipment_dashboard")
        return "Machine Reports", [
            _tab("Dashboard", "fa-chart-pie", mr + "#dashboard"),
            _tab("Equipment List", "fa-list-ul", mr + "#equipment"),
            _tab("Manufacturers", "fa-industry", mr + "#manufacturers"),
            _tab("Category Assignment", "fa-tasks", mr + "#assignments"),
            _tab("Failure Risk", "fa-heartbeat", reverse("failure_risk"), True),
        ]
    return None, []


def module_tabs(request):
    """The current page's module: its label for the sidebar, and its tabs.

    SPA host pages get the label only; they draw their own tabs.
    """
    user = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    if not (user and user.is_authenticated and match):
        return {}
    name = match.view_name
    if name in _SPA_HOSTS:
        return {"module_label": {"ppm_dashboard": "PPM", "ppm_by_department": "PPM",
                                 "equipment_dashboard": "Machine Reports"}.get(name, "Inventory")}
    label, tabs = _module(request, get_user_role(user), name)
    return {"module_label": label, "module_tabs": tabs} if label else {}
