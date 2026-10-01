"""Reminders to the Nurse In-Charge about work orders left unreviewed.

A work order still "Waiting Approval" 48 hours after it was raised is
emailed to its department's In-Charge, one email per department listing all
of them, then once a day until each is approved or declined. Once any has
waited 96 hours the HOD (who runs biomedical engineering) is copied. This email goes
out even when per-work-order email is off (notifications.events): it is the
nudge for work that has been sitting, not a copy of every submission.
"""
from datetime import timedelta

from django.utils import timezone

from .mailer import queue
from .recipients import department_in_charges

# Hours a work order may wait for review before the In-Charge is reminded.
OVERDUE_HOURS = 48
# Hours after which the HOD is copied on the reminder.
ESCALATE_HOURS = 96
# Work orders named in one email; the rest are counted.
LISTED = 20


def remind_overdue_approvals(now=None):
    """Queue today's reminder for each department with overdue work orders.
    Returns how many were queued."""
    from jobcard.models import jobcard

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    overdue = (jobcard.objects.filter(status='Waiting Approval', active_status=True,
                                      created_at__lte=now - timedelta(hours=OVERDUE_HOURS))
               .select_related('department', 'equipment__description', 'performed_by')
               .order_by('department__name', 'created_at'))
    by_department = {}
    for wo in overdue:
        by_department.setdefault(wo.department, []).append(wo)

    queued = 0
    for department, orders in by_department.items():
        in_charges = department_in_charges(department)
        if not in_charges:
            continue
        count = len(orders)
        # Oldest first, so the first one decides whether the HOD is copied.
        escalate = orders[0].created_at <= now - timedelta(hours=ESCALATE_HOURS)
        lines = [_line(wo, now) for wo in orders[:LISTED]]
        if count > LISTED:
            lines.append(f"  ...and {count - LISTED} more")
        noun = "work order has" if count == 1 else "work orders have"
        message = (f"{count} {noun} been waiting for your review in {department.name} "
                   f"for more than {OVERDUE_HOURS} hours:\n\n" + "\n".join(lines)
                   + "\n\nOpen Work Orders > Waiting Work Orders to approve or decline them.")
        if queue(
            # Its own key, so the day a work order crosses 96 hours the HOD
            # still hears even if that morning's reminder already went.
            kind='work_order_overdue',
            dedupe_key=f"wo-overdue:{department.pk}:{today.isoformat()}{':hod' if escalate else ''}",
            to_users=in_charges, copy_rule=escalate,
            subject=f"Reminder: {count} work order{'s' if count != 1 else ''} waiting for your review "
                    f"({department.name})",
            template='simple', context={'message': message, 'link': ''},
            in_app_message=f"{count} work order{'s' if count != 1 else ''} in {department.name} "
                           f"waiting more than {OVERDUE_HOURS} hours for your review.",
        ):
            queued += 1
    return queued


def _line(wo, now):
    days = (now - wo.created_at).days
    by = (wo.performed_by.get_full_name() or wo.performed_by.username) if wo.performed_by else '-'
    return (f"  - {str(wo.id)[:8].upper()}: {wo.equipment.description.name} (SN {wo.equipment.serial_number}), "
            f"{wo.action_taken} by {by}, waiting {days} day{'s' if days != 1 else ''}")
