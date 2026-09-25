"""Emails triggered by work order changes.

These run on the desktop where the change was made. Rows that arrive from
other machines through sync are written with SQL, not the ORM, so no signal
fires for them and each event is mailed exactly once.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from jobcard.models import jobcard

from .mailer import queue
from .recipients import department_in_charges

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=jobcard, dispatch_uid="notifications.jobcard_old_status")
def remember_old_status(sender, instance, **kwargs):
    instance._notifications_old_status = (
        sender.objects.filter(pk=instance.pk).values_list('status', flat=True).first()
        if instance.pk else None
    )


@receiver(post_save, sender=jobcard, dispatch_uid="notifications.jobcard_changed")
def work_order_changed(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    old = getattr(instance, '_notifications_old_status', None)
    if created or old is None:
        transaction.on_commit(lambda: _safe(work_order_submitted, instance.pk))
    elif old != instance.status and instance.status in ('Approved', 'Declined'):
        transaction.on_commit(lambda: _safe(work_order_decided, instance.pk))


def _safe(fn, pk):
    # Mail must never break saving a work order.
    try:
        fn(pk)
    except Exception:
        logger.exception("notifications: %s failed for work order %s", fn.__name__, pk)


def _context(wo):
    entries = list(wo.checklist_entries.filter(active_status=True))
    return {
        'wo': wo,
        'ref': str(wo.id)[:8].upper(),
        'equipment': wo.equipment,
        'technician': wo.performed_by.get_full_name() if wo.performed_by else '-',
        'decided_by': wo.nurse_name or (wo.verified_by_nurse.get_full_name() if wo.verified_by_nurse else '-'),
        'checklist_total': len(entries),
        'checklist_problems': [e for e in entries if e.is_problem],
    }


def work_order_submitted(pk):
    wo = jobcard.objects.select_related('equipment__description', 'department', 'workshop', 'performed_by').get(pk=pk)
    ctx = _context(wo)
    subject = (f"[Action] Work order {ctx['ref']} waiting for your approval — "
               f"{wo.equipment.description.name} ({wo.department.name})")
    queue(
        kind='work_order_submitted',
        dedupe_key=f"wo-submitted:{wo.pk}",
        to_users=department_in_charges(wo.department),
        subject=subject,
        template='work_order_submitted',
        context=ctx,
        in_app_message=f"{wo.action_taken} on {wo.equipment.serial_number} by {ctx['technician']} needs approval.",
    )


def work_order_decided(pk):
    wo = jobcard.objects.select_related('equipment__description', 'department', 'workshop', 'performed_by',
                                        'verified_by_nurse').get(pk=pk)
    if not wo.performed_by:
        return
    ctx = _context(wo)
    subject = f"Work order {ctx['ref']} {wo.status.lower()} — {wo.equipment.description.name} ({wo.department.name})"
    queue(
        kind='work_order_decided',
        dedupe_key=f"wo-decided:{wo.pk}:{wo.status}:{wo.nurse_signed_date.isoformat() if wo.nurse_signed_date else ''}",
        to_users=[wo.performed_by],
        subject=subject,
        template='work_order_decided',
        context=ctx,
        in_app_message=(f"{wo.status}: {wo.action_taken} on {wo.equipment.serial_number}"
                        + (f" — {wo.decline_reason}" if wo.status == 'Declined' and wo.decline_reason else "")),
    )
