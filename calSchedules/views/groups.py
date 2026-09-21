"""The group view of the calibration schedule.

The schedules page lists rows: one line per device, per month. That is the right
shape for finding a particular device and the wrong shape for the question the
scheduler actually acts on, which is **"is this group finished?"** — because a
group rolls to the next period together, and one outstanding member holds the
whole group back.

This view shows the estate the way the scheduler sees it: groups, how far
through each one is, what is left, and where it rolls to when it finishes. It
also puts regrouping within reach, which previously required a global flip of
the planning logic for every pending schedule in the system.
"""

import logging
from datetime import date

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from calSchedules import grouping
from calSchedules.regroup import group_snapshot, preview_regroup, regroup

logger = logging.getLogger(__name__)

MONTH_WINDOW = 12          # months offered in the "move to" picker


def _requested_month(request):
    """The month being viewed, from ?month=YYYY-MM, defaulting to this one."""
    raw = (request.GET.get("month") or "").strip()
    if raw:
        try:
            return date.fromisoformat(f"{raw}-01" if len(raw) == 7 else raw).replace(day=1)
        except ValueError:
            logger.debug("Ignoring unparseable month filter %r", raw)
    return date.today().replace(day=1)


@login_required
@require_GET
def schedule_groups(request):
    """Planning groups for one month, with completion and regroup controls."""
    month = _requested_month(request)
    logic = grouping.canonical_logic(request.GET.get("logic") or "department")

    groups = group_snapshot(month, planning_logic=logic)

    total_devices = sum(g["total"] for g in groups)
    completed_devices = sum(g["completed"] for g in groups)
    finished_groups = [g for g in groups if g["all_completed"]]

    # Months offered as move targets, from this month forward.
    first = date.today().replace(day=1)
    month_options = [first + relativedelta(months=i) for i in range(MONTH_WINDOW)]
    if month not in month_options:
        month_options = sorted({month, *month_options})

    context = {
        "month": month,
        "logic": logic,
        "groups": groups,
        "month_options": month_options,
        "previous_month": month - relativedelta(months=1),
        "next_month": month + relativedelta(months=1),
        "summary": {
            "groups": len(groups),
            "finished_groups": len(finished_groups),
            "devices": total_devices,
            "completed_devices": completed_devices,
            "remaining_devices": total_devices - completed_devices,
            "percent": round(completed_devices / total_devices * 100) if total_devices else 0,
        },
        "show_sidebar": True,
    }
    return render(request, "Calibrition/schedule_groups.html", context)


@login_required
@require_POST
def regroup_schedules(request):
    """Move the selected schedules into another month.

    Supports a preview so the operator sees what would happen — including what
    will be refused and why — before anything is written.
    """
    ids = request.POST.getlist("schedule_ids")
    target = request.POST.get("target_month")
    dry_run = request.POST.get("preview") == "1"
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.POST.get("format") == "json"
    )

    if not ids:
        return _respond(request, wants_json, False, "Select at least one schedule to move.")
    if not target:
        return _respond(request, wants_json, False, "Choose a month to move them to.")

    try:
        runner = preview_regroup if dry_run else regroup
        result = (
            runner(ids, target)
            if dry_run
            else runner(ids, target, actor=request.user)
        )
    except ValueError as exc:
        return _respond(request, wants_json, False, str(exc))
    except Exception:
        logger.exception("regroup_schedules failed for %s -> %s", ids, target)
        return _respond(
            request, wants_json, False,
            "The schedules could not be moved. The error has been logged.",
        )

    moved, skipped = len(result["moved"]), len(result["skipped"])
    if dry_run:
        summary = f"{moved} schedule(s) would move; {skipped} would be left as they are."
    elif moved:
        summary = f"Moved {moved} schedule(s) to {result['target_month']:%B %Y}."
        if skipped:
            summary += f" {skipped} were left as they are."
    else:
        summary = "Nothing was moved."

    if wants_json:
        return JsonResponse({
            "success": True,
            "message": summary,
            "dry_run": dry_run,
            "target_month": result["target_month"].isoformat(),
            "moved": [
                {**m, "from": m["from"].isoformat(), "to": m["to"].isoformat()}
                for m in result["moved"]
            ],
            "skipped": result["skipped"],
        })

    if moved or not skipped:
        messages.success(request, summary)
    else:
        messages.warning(request, summary)
        for item in result["skipped"]:
            messages.info(request, f"{item['equipment']}: {item['reason']}")
    return redirect(request.META.get("HTTP_REFERER") or "schedule:schedule_groups")


def _respond(request, wants_json, ok, message):
    if wants_json:
        return JsonResponse({"success": ok, "message": message}, status=200 if ok else 400)
    messages.error(request, message)
    return redirect(request.META.get("HTTP_REFERER") or "schedule:schedule_groups")
