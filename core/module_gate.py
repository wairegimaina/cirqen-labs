"""Switched-off modules are closed, not just hidden.

The hospital's profile from Cirqen Control (hospital_profile.py) can switch a
module off. Its pages and JSON under the module's URL prefixes then answer
404 with a plain explanation, so a typed address or an old bookmark cannot
reach it. The data stays in the database and keeps syncing; switching the
module back on shows everything again. No profile: every module is on.
"""
from django.conf import settings
from django.shortcuts import redirect, render

import hospital_profile


def modules() -> dict:
    return hospital_profile.module_states(hospital_profile.load(settings.DATA_PATH))


class ModuleGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/" and not modules()["machine_reports"]["on"]:
            # "/" normally opens Machine Reports; the dashboard is always on.
            return redirect("/dashboard/")
        key = hospital_profile.module_for_path(request.path)
        if key:
            state = modules()[key]
            if not state["on"]:
                return render(request, "core/module_off.html", {"module_label": state["label"]}, status=404)
        return self.get_response(request)
