"""Settings > Computers (HOD): this hospital's PCs, approve the ones waiting,
remove one that should no longer sync. Talks to the hospital's HQ like
Settings > Support access (core/support._call)."""
from django.contrib import messages
from django.shortcuts import redirect, render

from core.support import _call, _eat
from users.control import role_required


@role_required("HOD")
def computers(request):
    if request.method == "POST":
        action = request.POST.get("action")
        device_id = request.POST.get("device_id", "")
        if action in ("approve", "remove") and device_id:
            data, error = _call("POST", f"/devices/{action}",
                                json={"device_id": device_id, "by": request.user.get_full_name() or request.user.username})
            if error:
                messages.error(request, error)
            else:
                messages.success(request, "Approved: it starts syncing within a few minutes."
                                 if action == "approve" else "Removed: it can no longer sync.")
        return redirect("core:computers")
    data, error = _call("GET", "/devices")
    devices = [{**d, "joined": _eat(d.get("joined_at"))} for d in (data or {}).get("devices", [])]
    from core import hq_link

    return render(request, "core/computers.html", {
        "waiting": [d for d in devices if d["pending_approval"]],
        "approved": [d for d in devices if not d["pending_approval"]],
        "old_key": [{**d, "seen": _eat(d.get("last_used"))} for d in (data or {}).get("on_shared_key", [])],
        "this_pc": hq_link.get_client_id(), "error": error})
