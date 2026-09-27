"""Checklist templates per equipment type (jobcard/checklists.py).

Every technician can read them; the HOD and engineers-in-charge edit them,
since a checklist is a department-wide standard for that kind of equipment.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from Inventory.models import EquipmentDescription
from users.control import get_user_role

from ..checklists import MAX_ITEMS, parse_template


def _can_edit(user):
    role = get_user_role(user)
    if role == "HOD":
        return True
    profile = getattr(user, "userprofile", None)
    return role == "Tech" and getattr(profile, "level", "") == "Engineer Incharge"


@login_required
def checklist_templates(request):
    role = get_user_role(request.user)
    if role not in ("HOD", "Tech"):
        messages.error(request, "Checklists are managed by the engineering team.", extra_tags="jobcard")
        return redirect("jobcard:create_job_card")
    can_edit = _can_edit(request.user)

    if request.method == "POST":
        if not can_edit:
            messages.error(request, "Only the head of department or an engineer-in-charge can edit checklists.",
                           extra_tags="jobcard")
            return redirect("jobcard:checklist_templates")
        description = get_object_or_404(EquipmentDescription, pk=request.POST.get("description_id"))
        items = parse_template(request.POST.get("items", ""))
        description.checklist_template = items
        description.save()
        messages.success(
            request,
            f"Checklist for {description.name} saved ({len(items)} item{'s' if len(items) != 1 else ''}).",
            extra_tags="jobcard",
        )
        return redirect(f"{reverse('jobcard:checklist_templates')}?edit={description.pk}")

    query = request.GET.get("q", "").strip()
    descriptions = EquipmentDescription.objects.filter(active_status=True).order_by("name")
    if query:
        descriptions = descriptions.filter(name__icontains=query)
    page = Paginator(descriptions, 30).get_page(request.GET.get("page"))

    editing = None
    if request.GET.get("edit"):
        editing = EquipmentDescription.objects.filter(pk=request.GET["edit"]).first()

    return render(request, "jobcard/checklists.html", {
        "show_sidebar": True,
        "page": page,
        "query": query,
        "editing": editing,
        "editing_text": "\n".join(editing.checklist_template or []) if editing else "",
        "can_edit": can_edit,
        "max_items": MAX_ITEMS,
    })
