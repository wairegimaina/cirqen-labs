"""The daily "what needs doing" email, built per user from their scope.

    Tech (maintenance)   PPMs due this month / overdue, their declined work
                         orders, warranties expiring, devices at high risk
    Tech (calibration)   calibrations due this month / overdue, their declined
                         work orders
    In-Charge            work orders waiting for their approval, PPMs due in
                         their department
    HOD                  per-workshop totals of all of the above

Each section is {'title', 'items' (lines), 'total'}; empty sections are
dropped and a user with nothing to do gets no email.
"""
from calendar import monthrange
from datetime import timedelta

from django.db.models import QuerySet
from django.utils.timezone import localdate

MAX_LINES = 25


def _month_end(today):
    return today.replace(day=monthrange(today.year, today.month)[1])


def _section(title, rows, line, total=None):
    """A digest section, or None when there is nothing in it."""
    shown = list(rows[:MAX_LINES])
    if not shown:
        return None
    if total is None:
        total = rows.count() if isinstance(rows, QuerySet) else len(rows)
    return {'title': title, 'items': [line(r) for r in shown], 'total': total}


def _device(e):
    return f"{e.description.name} SN {e.serial_number} ({e.department.name})"


def _ppm_due(today, **scope):
    from ppms.models import PPMSchedule

    return (PPMSchedule.objects.filter(active_status=True, scheduled_month__lte=_month_end(today), **scope)
            .exclude(status='completed')
            .select_related('equipment__description', 'equipment__department')
            .order_by('scheduled_month'))


def _calibration_due(today, **scope):
    from calSchedules.models import CalibrationSchedule

    return (CalibrationSchedule.objects.filter(active_status=True, scheduled_month__lte=_month_end(today), **scope)
            .exclude(status='completed')
            .select_related('equipment__description', 'equipment__department')
            .order_by('scheduled_month'))


def _warranty_expiring(today, **scope):
    """Warranties (Warranties module) running out within the "Expiring Soon" window."""
    from Inventory.models import Warranty

    return (Warranty.objects.filter(active_status=True, pending_delete=False, equipment__active_status=True,
                                    expiry_date__gte=today,
                                    expiry_date__lte=today + timedelta(days=_warranty_days()), **scope)
            .select_related('equipment__description', 'equipment__department', 'supplier')
            .order_by('expiry_date'))


def _warranty_days():
    from Inventory.models import Warranty

    return Warranty.expiring_soon_days()


def _schedule_line(s, today):
    overdue = s.scheduled_month < today.replace(day=1)
    return f"{'OVERDUE ' if overdue else ''}{s.scheduled_month:%b %Y}: {_device(s.equipment)}"


def _warranty_line(w):
    supplier = f", supplier {w.supplier.name}" + (f" ({w.supplier.phone})" if w.supplier.phone else "") \
        if w.supplier else ""
    return f"{w.expiry_date:%d %b %Y}: {_device(w.equipment)}{supplier}"


def _risk_section(equipment_qs):
    from machineReports.prediction import predict

    high = [p for p in predict(equipment_qs.filter(status='Working'), horizon_days=30) if p.risk_level == 'High']
    return _section(
        "Devices likely to need repair within 30 days", high,
        lambda p: f"{p.risk_percent}%: {_device(p.equipment)} — {'; '.join(p.reasons)}",
        total=len(high),
    )


def build(user, today=None):
    today = today or localdate()
    profile = getattr(user, 'userprofile', None)
    if profile is None:
        return []
    from Inventory.models import Equipment
    from jobcard.models import jobcard

    sections = []
    if profile.role == 'Tech' and profile.workshop:
        ws = profile.workshop
        if ws.category == 'calibration_center':
            sections.append(_section("Calibrations due or overdue", _calibration_due(today, workshop=ws),
                                     lambda s: _schedule_line(s, today)))
        else:
            sections.append(_section("PPMs due or overdue", _ppm_due(today, workshop=ws),
                                     lambda s: _schedule_line(s, today)))
            sections.append(_section(f"Warranties expiring in {_warranty_days()} days",
                                     _warranty_expiring(today, equipment__department__workshop=ws), _warranty_line))
            sections.append(_risk_section(Equipment.objects.filter(active_status=True, department__workshop=ws)))
        declined = (jobcard.objects.filter(performed_by=user, status='Declined', active_status=True,
                                           nurse_signed_date__date__gte=today - timedelta(days=7))
                    .select_related('equipment__description', 'equipment__department'))
        sections.append(_section("Your work orders declined this week", declined,
                                 lambda w: f"{_device(w.equipment)}: {w.decline_reason or 'no reason given'}"))

    elif profile.role == 'NIC' and profile.department:
        waiting = (jobcard.objects.filter(department=profile.department, status='Waiting Approval', active_status=True)
                   .select_related('equipment__description', 'equipment__department').order_by('date_issued'))
        sections.append(_section("Work orders waiting for your approval", waiting,
                                 lambda w: f"{w.date_issued:%d %b}: {w.action_taken} on {_device(w.equipment)}"))
        sections.append(_section("PPMs due in your department",
                                 _ppm_due(today, equipment__department=profile.department),
                                 lambda s: _schedule_line(s, today)))

    elif profile.role == 'HOD':
        sections.extend(_hod_sections(today))

    return [s for s in sections if s]


def _hod_sections(today):
    from calSchedules.models import CalibrationSchedule
    from Inventory.models import Equipment
    from jobcard.models import jobcard
    from ppms.models import PPMSchedule
    from workshop.models import Workshop

    month_start = today.replace(day=1)
    lines = []
    for ws in Workshop.objects.filter(active_status=True).order_by('name'):
        ppm = PPMSchedule.objects.filter(workshop=ws, active_status=True, scheduled_month__lt=month_start) \
            .exclude(status='completed').count()
        cal = CalibrationSchedule.objects.filter(workshop=ws, active_status=True, scheduled_month__lt=month_start) \
            .exclude(status='completed').count()
        waiting = jobcard.objects.filter(workshop=ws, status='Waiting Approval', active_status=True).count()
        down = Equipment.objects.filter(department__workshop=ws, active_status=True).exclude(status='Working').count()
        if ppm or cal or waiting or down:
            lines.append(f"{ws.name}: {ppm} overdue PPM, {cal} overdue calibration, "
                         f"{waiting} awaiting approval, {down} devices down")
    sections = [_section("Workshops needing attention", lines, str, total=len(lines))]
    sections.append(_section(f"Warranties expiring in {_warranty_days()} days", _warranty_expiring(today),
                             _warranty_line))
    sections.append(_risk_section(Equipment.objects.filter(active_status=True)))
    return sections


def any_due(sections):
    return any(s['total'] for s in sections)

