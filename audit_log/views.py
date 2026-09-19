from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render

from users.control import hod_required

from .models import AuditLog
from .timeline import (
    MERGE_WINDOW,
    SOURCE_CHOICES,
    build_timeline,
    counts,
)


@login_required
@hod_required
def audit_log_list(request):
    """Read-only audit trail viewer — HOD only.

    Shows both trails the system writes. ``AuditLog`` records row-level changes
    from the Inventory flows and the sync agents; ``CalibrationAuditLog``
    records what people did in the calibration module — who approved a session,
    completed a schedule, edited a procedure. The second was being written from
    five places and displayed nowhere, so this page showed table names and row
    ids while the readable half of the audit trail sat unread.
    """
    source = request.GET.get("source", "").strip()
    table_name = request.GET.get("table_name", "").strip()
    operation = request.GET.get("operation", "").strip()
    search = request.GET.get("q", "").strip()

    entries = build_timeline(
        source=source, table_name=table_name, operation=operation, search=search
    )

    paginator = Paginator(entries, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "audit_log/audit_log_list.html",
        {
            "page_obj": page_obj,
            "operation_choices": AuditLog.OPERATION_CHOICES,
            "source_choices": SOURCE_CHOICES,
            "selected_source": source,
            "selected_table_name": table_name,
            "selected_operation": operation,
            "search": search,
            "totals": counts(),
            "merge_window": MERGE_WINDOW,
        },
    )
