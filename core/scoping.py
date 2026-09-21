"""One place that decides which records a user may see (IMPROVEMENT_PLAN.md 4.2).

    HOD   everything
    Tech  their workshop
    NIC   their department (and, for workshop-level records, that department's workshop)
    other nothing

``for_user(queryset, user)`` narrows any queryset of a registered model, and
``get_for_user_or_404(model, user, **lookup)`` replaces a bare
``get_object_or_404`` wherever an ID comes from the URL, so a record outside the
user's scope is indistinguishable from one that does not exist.

Calibration schedules and sessions are deliberately not registered: the
calibration centre serves every workshop, and those views are global by design.
"""
from django.apps import apps
from django.db.models import QuerySet
from django.http import Http404

from users.control import get_user_role

# model label -> (path to its Workshop, path to its Department or None)
SCOPES = {
    "workshop.Workshop": ("pk", None),
    "Inventory.Department": ("workshop", "pk"),
    "Inventory.Equipment": ("department__workshop", "department"),
    "ppms.PPMSchedule": ("workshop", "equipment__department"),
    "jobcard.jobcard": ("workshop", "department"),
    "parts_tools.Tools": ("workshop", None),
    "parts_tools.Accessories": ("workshop", None),
}


def _profile(user):
    return getattr(user, "userprofile", None) if user and user.is_authenticated else None


def for_user(queryset, user):
    """Narrow ``queryset`` to the records ``user`` is allowed to see."""
    label = queryset.model._meta.label
    if label not in SCOPES:
        raise LookupError(f"{label} has no scoping rule; add one to core.scoping.SCOPES")
    workshop_path, department_path = SCOPES[label]
    role = get_user_role(user)
    profile = _profile(user)

    if role == "HOD":
        return queryset
    if role == "Tech" and profile.workshop_id:
        return queryset.filter(**{workshop_path: profile.workshop_id})
    if role == "NIC" and profile.department_id:
        if department_path:
            return queryset.filter(**{department_path: profile.department_id})
        # Workshop-level records (tools, accessories): the department's workshop.
        return queryset.filter(**{workshop_path: profile.department.workshop_id})
    return queryset.none()


def get_for_user_or_404(model, user, **lookup):
    if isinstance(model, str):
        model = apps.get_model(model)
    queryset = model if isinstance(model, QuerySet) else model._default_manager.all()
    try:
        return for_user(queryset, user).get(**lookup)
    except queryset.model.DoesNotExist:
        raise Http404(f"No {queryset.model._meta.verbose_name} matches the given query.")
