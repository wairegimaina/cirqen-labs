from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render

from users.control import hod_required

from .models import AuditLog


@login_required
@hod_required
def audit_log_list(request):
    """Read-only audit trail viewer — HOD only.

    AuditLog rows are written by Inventory transfer/reactivation flows and
    the sync agents; this is the first UI surface for that data.
    """
    entries = AuditLog.objects.all()

    table_name = request.GET.get("table_name", "").strip()
    if table_name:
        entries = entries.filter(table_name__icontains=table_name)

    operation = request.GET.get("operation", "").strip()
    if operation:
        entries = entries.filter(operation=operation)

    paginator = Paginator(entries, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "audit_log/audit_log_list.html",
        {
            "page_obj": page_obj,
            "operation_choices": AuditLog.OPERATION_CHOICES,
            "selected_table_name": table_name,
            "selected_operation": operation,
        },
    )
