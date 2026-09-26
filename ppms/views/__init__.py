"""ppms.views package (split from the former single views.py)."""

from .helpers import (
    get_user_access_context,
    get_current_month_year,
    calculate_ppm_statistics,
)
from .dashboard import (
    ppm_dashboard,
    clear_department_filter,
)
from .departments import (
    ppm_by_department,
)
from .analytics import (
    get_analytics_data,
    get_ppm_summary_api,
)
from .schedules import (
    push_schedule,
    mark_completed,
    edit_schedule,
    delete_schedule,
    schedule_equipment,
)
from .bulk import (
    bulk_delete_schedules,
    bulk_mark_completed,
    bulk_push_schedules,
    bulk_schedule_unscheduled,
)
from .exports import (
    export_ppm_excel,
    export_ppm_pdf,
    export_department_ppm_pdf,
)
from .tasks import (
    trigger_initialization,
    trigger_smart_reorganize_ppm,
    _normalize_ppm_helper,
    trigger_sync_initialization,
    trigger_normalize_ppm,
)
from .diagnostics import (
    check_task_status,
    view_logs,
)

__all__ = [
    "get_user_access_context",
    "get_current_month_year",
    "calculate_ppm_statistics",
    "ppm_dashboard",
    "clear_department_filter",
    "ppm_by_department",
    "get_analytics_data",
    "get_ppm_summary_api",
    "push_schedule",
    "mark_completed",
    "edit_schedule",
    "delete_schedule",
    "schedule_equipment",
    "bulk_delete_schedules",
    "bulk_mark_completed",
    "bulk_push_schedules",
    "bulk_schedule_unscheduled",
    "export_ppm_excel",
    "export_ppm_pdf",
    "export_department_ppm_pdf",
    "trigger_initialization",
    "trigger_smart_reorganize_ppm",
    "_normalize_ppm_helper",
    "trigger_sync_initialization",
    "trigger_normalize_ppm",
    "check_task_status",
    "view_logs",
]
