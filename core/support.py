"""Settings > Support access (HOD): let Cirqen support into this hospital's
records for a few hours, see who did what, end it early.

The grant and the log live on the hospital's HQ (hq_server support_access.py);
this page talks to it with this PC's key, and only to an HQ that passed the
hospital check (core/hq_link).
"""
import requests
from django.contrib import messages
from django.shortcuts import redirect, render

from core import hq_link
from users.control import role_required

TIMEOUT = 15


def _call(method, path, **kwargs):
    """(json or None, error) from this hospital's HQ."""
    api_url, client_id = hq_link._api_url(), hq_link.get_client_id()
    if not api_url or not client_id:
        return None, "This computer is not connected to HQ."
    code = hq_link._hospital_code()
    if not hq_link._hq_confirmed(api_url, code):
        return None, "HQ has not proved it is this hospital's."
    from django.conf import settings

    headers = {"X-Client-ID": client_id, "X-Device-ID": client_id,
               "X-API-Key": hq_link.sync_key()}
    if code:
        headers["X-Cirqen-Hospital"] = code
    try:
        response = requests.request(method, f"{api_url}{path}", headers=headers, timeout=TIMEOUT, **kwargs)
    except (requests.RequestException, OSError):
        return None, "HQ could not be reached. Try again when this computer is online."
    if response.status_code != 200:
        try:
            detail = response.json().get("error", "")
        except ValueError:
            detail = ""
        return None, f"HQ refused ({response.status_code}){': ' + detail if detail else ''}."
    return response.json(), ""


@role_required("HOD")
def support_access(request):
    if request.method == "POST":
        if request.POST.get("action") == "end":
            data, error = _call("POST", "/support/grants/end", json={"ended_by": request.user.username})
            if data is not None:
                messages.success(request, "Support access ended.")
        else:
            try:
                hours = int(request.POST.get("hours", 0))
            except ValueError:
                hours = 0
            data, error = _call("POST", "/support/grants", json={
                "hours": hours, "reason": request.POST.get("reason", "").strip(),
                "granted_by": request.user.get_full_name() or request.user.username})
            if data is not None:
                # Shown once, here: HQ keeps only its hash.
                request.session["support_code"] = {"code": data["code"], "expires_at": data["expires_at"]}
        if error:
            messages.error(request, error)
        return redirect("core:support_access")

    data, error = _call("GET", "/support/log")
    new_code = request.session.pop("support_code", None)
    if new_code:
        new_code["until"] = _eat(new_code["expires_at"])
    return render(request, "core/support_access.html", {
        "active": [{**g, "until": _eat(g["expires_at"])} for g in (data or {}).get("active", [])],
        "log": [{**e, "when": _eat(e["at"]), "what": e["event"].replace("_", " ")} for e in (data or {}).get("log", [])],
        "error": error, "new_code": new_code})


def _eat(iso):
    from datetime import datetime

    from core.eat import fmt_eat

    try:
        return fmt_eat(datetime.fromisoformat(iso))
    except (TypeError, ValueError):
        return iso
