from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import hq_settings


@staff_member_required
def hq_connection(request):
    """Show where this desktop looks for HQ, and let an administrator change it.

    Staff-only, like the update pages: the same person who may install code may
    say which server it comes from.
    """
    cfg = hq_settings.load_config()
    rows = hq_settings.describe(cfg)
    groups: list[tuple[str, list]] = []
    for row in rows:
        if not groups or groups[-1][0] != row["group"]:
            groups.append((row["group"], []))
        groups[-1][1].append(row)

    ok, errors = cfg.validate_config()
    return render(request, "core/hq_connection.html", {
        "groups": groups,
        "rows": rows,
        "remote": hq_settings.remote_state(cfg),
        "config_errors": [e for e in errors if any(f["key"] in e for f in hq_settings.FIELDS)],
        "any_overridden": any(not row["is_default"] for row in rows),
        "data_path": str(cfg.config_file),
    })


@staff_member_required
@require_POST
def hq_connection_save(request):
    cfg = hq_settings.load_config()
    changed, errors = hq_settings.apply_changes(cfg, request.POST)

    if errors:
        for error in errors:
            messages.error(request, error)
        return redirect("core:hq_connection")

    if not changed:
        messages.info(request, "No changes — the settings already say that.")
        return redirect("core:hq_connection")

    messages.success(
        request,
        f"Saved {len(changed)} setting(s). Restart the application for the sync agent "
        "and the update checker to use the new address.",
    )
    return redirect("core:hq_connection")


@staff_member_required
@require_POST
def hq_connection_reset(request):
    """Drop every local override so this machine follows the shipped default
    again — and therefore follows future releases."""
    cfg = hq_settings.load_config()
    released = []
    for field in hq_settings.FIELDS:
        key = field["key"]
        if cfg.endpoint_sources.get(key) == "config.json":
            cfg.set(key, hq_settings.HQ_ENDPOINT_DEFAULTS[key])
            released.append(key)

    if released:
        messages.success(
            request,
            f"Released {len(released)} setting(s) back to the shipped default. "
            "Restart the application to use them.",
        )
    else:
        messages.info(request, "Nothing to reset — no setting is pinned on this machine.")
    return redirect("core:hq_connection")


@staff_member_required
@require_POST
def hq_connection_test(request):
    """Reachability of whatever is configured right now. Called from the page."""
    cfg = hq_settings.load_config()
    return JsonResponse({"results": hq_settings.probe_all(cfg)})
