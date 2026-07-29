"""calSchedules.tasks package (split from the former single tasks.py)."""

from .helpers import (
    _planning_year,
    _get_group_key,
    _get_group_members,
    _basic_group_completion_check,
    _find_optimal_month,
    _is_protected,
    _apply_schedule_update,
    _log_wrap,
)
from .advance import (
    auto_advance_completed_calibrations,
    lock_and_reschedule_completed,
    daily_lock_and_reschedule,
    update_calibration_statuses,
)
from .normalize import (
    normalize_existing_schedules,
    smart_reorganize_on_logic_change,
)
from .initialize import (
    initialize_calibration_schedule_with_logic,
)
from .maintenance import (
    push_overdue_schedules,
    cleanup_orphaned_calibration_schedules,
    validate_calibration_schedules,
    remove_inactive_equipment_schedules,
    bulk_update_calibration_periods,
    generate_monthly_calibration_report,
    auto_schedule_unscheduled_equipment,
)
from .reconciliation import (
    run_calibration_reconciliation,
    auto_reschedule_completed_task,
    ensure_grouping_consistency_task,
    diagnose_calibration_schedules,
)

__all__ = [
    "_planning_year",
    "_get_group_key",
    "_get_group_members",
    "_basic_group_completion_check",
    "_find_optimal_month",
    "_is_protected",
    "_apply_schedule_update",
    "_log_wrap",
    "auto_advance_completed_calibrations",
    "lock_and_reschedule_completed",
    "daily_lock_and_reschedule",
    "update_calibration_statuses",
    "normalize_existing_schedules",
    "smart_reorganize_on_logic_change",
    "initialize_calibration_schedule_with_logic",
    "push_overdue_schedules",
    "cleanup_orphaned_calibration_schedules",
    "validate_calibration_schedules",
    "remove_inactive_equipment_schedules",
    "bulk_update_calibration_periods",
    "generate_monthly_calibration_report",
    "auto_schedule_unscheduled_equipment",
    "run_calibration_reconciliation",
    "auto_reschedule_completed_task",
    "ensure_grouping_consistency_task",
    "diagnose_calibration_schedules",
]
