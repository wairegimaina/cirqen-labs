"""
Saved filters — generic, per-view-name query-string bookmarks.

Deliberately page-agnostic: filter_params is just the raw query string a
list page was filtered with (e.g. "status=Waiting+Approval&department=3"),
keyed by a view_name the page chooses for itself. This means the same
model/views/UI work on any GET-filtered list page (Inventory, Job Cards,
PPM, ...) without a schema or view change per page — a page just needs to
include includes/saved_filters.html with its own view_name.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods

from ..models import SavedFilter


def _active_qs(user, view_name):
    return SavedFilter.objects.filter(
        user=user, view_name=view_name, active_status=True, pending_delete=False
    )


@login_required
@require_GET
def saved_filters_list_api(request):
    view_name = request.GET.get("view_name", "").strip()
    if not view_name:
        return JsonResponse({"success": False, "error": "view_name is required"}, status=400)

    filters = _active_qs(request.user, view_name).order_by("-created_at")
    return JsonResponse({
        "success": True,
        "filters": [
            {"id": str(f.id), "name": f.name, "filter_params": f.filter_params}
            for f in filters
        ],
    })


@login_required
@require_http_methods(["POST"])
def saved_filter_create_ajax(request):
    view_name = (request.POST.get("view_name") or "").strip()
    name = (request.POST.get("name") or "").strip()
    query_string = request.POST.get("query_string") or ""

    if not view_name or not name:
        return JsonResponse({"success": False, "error": "view_name and name are required"}, status=400)

    from urllib.parse import parse_qs
    params = {k: v[0] for k, v in parse_qs(query_string).items()}

    saved = SavedFilter.objects.create(
        user=request.user, view_name=view_name, name=name, filter_params=params
    )
    return JsonResponse({"success": True, "id": str(saved.id), "name": saved.name})


@login_required
@require_http_methods(["POST", "DELETE"])
def saved_filter_delete_ajax(request, pk):
    saved = get_object_or_404(SavedFilter, pk=pk, user=request.user)
    saved.active_status = False
    saved.pending_delete = True
    saved.save(update_fields=["active_status", "pending_delete", "updated_at"])
    return JsonResponse({"success": True})
