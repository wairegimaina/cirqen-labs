"""ppms.tasks package (split from the former single tasks.py)."""

from .helpers import (
    _get_ppm_group_key,
    _get_group_key_ppm,
    _get_ppm_group_members,
)
from .initialize import (
    initialize_ppm_schedule_with_logic,
)
from .scheduling import (
    check_and_push_overdue_ppms,
    auto_schedule_unscheduled_equipment,
    generate_unscheduled_equipment_report,
)
from .normalize import (
    smart_reorganize_ppm_schedules,
    normalize_ppm_schedules,
)
from .maintenance import (
    validate_ppm_schedules,
    periodic_cleanup_inactive_schedules,
    cleanup_orphaned_ppm_schedules,
)

__all__ = [
    "_get_ppm_group_key",
    "_get_group_key_ppm",
    "_get_ppm_group_members",
    "initialize_ppm_schedule_with_logic",
    "check_and_push_overdue_ppms",
    "auto_schedule_unscheduled_equipment",
    "generate_unscheduled_equipment_report",
    "smart_reorganize_ppm_schedules",
    "normalize_ppm_schedules",
    "validate_ppm_schedules",
    "periodic_cleanup_inactive_schedules",
    "cleanup_orphaned_ppm_schedules",
]
