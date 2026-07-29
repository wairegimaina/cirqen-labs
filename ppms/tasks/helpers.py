"""ppms.tasks.helpers — pure PPM group helpers."""
from celery import shared_task
from Inventory.models import Equipment, Department, EquipmentDescription
from ..models import PPMSchedule
from workshop.models import Workshop
from django.utils.timezone import now
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import logging
logger = logging.getLogger(__name__)


def _get_ppm_group_key(schedule, planning_logic):
    """Generate a unique group key for PPM schedules based on planning logic."""
    if planning_logic == 'description' or planning_logic == 'description_based':
        group_id = schedule.equipment.description_id if schedule.equipment and schedule.equipment.description else 'no_description'
    else:
        group_id = schedule.equipment.department_id if schedule.equipment and schedule.equipment.department else 'no_department'

    month_key = schedule.scheduled_month.strftime('%Y-%m') if schedule.scheduled_month else 'unknown'
    return f"{planning_logic}_{group_id}_{month_key}"


def _get_group_key_ppm(schedule, planning_logic):
    """Generate a unique group key for PPM schedules."""
    if planning_logic == 'description' or planning_logic == 'description_based':
        group_id = schedule.equipment.description_id if schedule.equipment and schedule.equipment.description else 'no_description'
    else:
        group_id = schedule.equipment.department_id if schedule.equipment and schedule.equipment.department else 'no_department'

    month_key = schedule.scheduled_month.strftime('%Y-%m') if schedule.scheduled_month else 'unknown'
    return f"{planning_logic}_{group_id}_{month_key}"


def _get_ppm_group_members(schedule, planning_logic):
    """Get all members of the same PPM group for a given schedule."""
    if planning_logic == 'description' or planning_logic == 'description_based':
        group_id = schedule.equipment.description_id if schedule.equipment else None
        members = PPMSchedule.objects.filter(
            equipment__description_id=group_id,
            scheduled_month=schedule.scheduled_month,
            active_status=True
        ).select_related('equipment')
    else:
        group_id = schedule.equipment.department_id if schedule.equipment else None
        members = PPMSchedule.objects.filter(
            equipment__department_id=group_id,
            scheduled_month=schedule.scheduled_month,
            active_status=True
        ).select_related('equipment')
    return members
