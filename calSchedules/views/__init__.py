"""calSchedules views package (split from the former single views.py)."""

from .helpers import (
    get_current_month_year,
    check_overdue_schedules,
    check_group_waiting_status,
    _get_logic_change_context,
    get_user_access_context,
)
from .groups import (
    schedule_groups,
    regroup_schedules,
)
from .dashboard import (
    calibration_dashboard,
    calibration_by_department,
    pending_calibrations,
    clear_calibration_department_filter,
)
from .schedules import (
    mark_calibration_completed,
    push_calibration_schedule,
    edit_calibration_schedule,
    delete_calibration_schedule,
    schedule_calibration_equipment,
)
from .bulk import (
    bulk_mark_calibration_completed,
    bulk_delete_calibration_schedules,
    bulk_push_calibration_schedules,
    bulk_schedule_unscheduled_calibration,
)
from .exports import (
    export_calibration_pdf,
    export_department_calibration_pdf,
    export_calibration_excel,
)
from .tasks import (
    trigger_auto_advance_calibrations,
    trigger_normalize_schedules,
    trigger_smart_reorganize,
    trigger_calibration_initialization,
)
from .apis import (
    get_overdue_status,
    get_waiting_groups,
    get_group_status,
    get_calibration_task_status,
)
from .ajax import (
    _apply_ajax_common_filters,
    _ajax_schedule_to_dict,
    ajax_schedules,
    ajax_completed_schedules,
    ajax_unscheduled_equipment,
    ajax_schedule_stats,
)

__all__ = [
    "get_current_month_year",
    "check_overdue_schedules",
    "check_group_waiting_status",
    "_get_logic_change_context",
    "get_user_access_context",
    "calibration_dashboard",
    "calibration_by_department",
    "pending_calibrations",
    "clear_calibration_department_filter",
    "mark_calibration_completed",
    "push_calibration_schedule",
    "edit_calibration_schedule",
    "delete_calibration_schedule",
    "schedule_calibration_equipment",
    "bulk_mark_calibration_completed",
    "bulk_delete_calibration_schedules",
    "bulk_push_calibration_schedules",
    "bulk_schedule_unscheduled_calibration",
    "export_calibration_pdf",
    "export_department_calibration_pdf",
    "export_calibration_excel",
    "trigger_auto_advance_calibrations",
    "trigger_normalize_schedules",
    "trigger_smart_reorganize",
    "trigger_calibration_initialization",
    "get_overdue_status",
    "get_waiting_groups",
    "get_group_status",
    "get_calibration_task_status",
    "_apply_ajax_common_filters",
    "_ajax_schedule_to_dict",
    "ajax_schedules",
    "ajax_completed_schedules",
    "ajax_unscheduled_equipment",
    "ajax_schedule_stats",
]
