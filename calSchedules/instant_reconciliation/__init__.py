"""calSchedules.instant_reconciliation package (split from the former single instant_reconciliation.py)."""

from .helpers import (
    lock_schedule_immediately,
    lock_all_completed_schedules,
    get_group_scheduled_month,
    get_next_group_month,
    find_group_members,
    check_group_completion_status,
)
from .signals import (
    instant_reschedule_on_completion,
    auto_complete_on_certificate,
    instant_grouping_alignment,
)
from .alignment import (
    initialize_schedule_for_equipment,
    diagnose_group_alignment,
    fix_group_alignment,
    check_instant_reconciliation_status,
)

# startup side effects (run once, as in the original module)
import logging
logger = logging.getLogger(__name__)
logger.info('=' * 80)
logger.info('⚡ INSTANT CALIBRATION RECONCILIATION LOADED (IMMEDIATE LOCKING)')
logger.info('=' * 80)
logger.info('✅ instant_reschedule_on_completion (GROUP-AWARE + IMMEDIATE LOCK)')
logger.info('✅ instant_grouping_alignment (STRICT - NO JUMPING)')
logger.info('✅ auto_complete_on_certificate (CERTIFICATE INTEGRATION + LOCK)')
logger.info('✅ lock_all_completed_schedules (IMMEDIATE CHECKER)')
logger.info('')
logger.info('🔧 KEY FEATURES:')
logger.info('   - Groups stay together in same month')
logger.info('   - No equipment jumping between months')
logger.info('   - Entire group moves together when all complete')
logger.info('   - New equipment aligns to existing group')
logger.info('   - 🔒 IMMEDIATE locking on completion (no waiting)')
logger.info('   - Periodic checker locks ALL completed schedules')
logger.info('=' * 80)

__all__ = [
    "lock_schedule_immediately",
    "lock_all_completed_schedules",
    "get_group_scheduled_month",
    "get_next_group_month",
    "find_group_members",
    "check_group_completion_status",
    "instant_reschedule_on_completion",
    "auto_complete_on_certificate",
    "instant_grouping_alignment",
    "initialize_schedule_for_equipment",
    "diagnose_group_alignment",
    "fix_group_alignment",
    "check_instant_reconciliation_status",
]
