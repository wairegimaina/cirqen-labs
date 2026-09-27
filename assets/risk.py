"""A replacement risk score per machine (0-100), with the reasons behind it.

The score adds up five weighted parts, each 0 to 1:

    repairs in the last 12 months   35   (0, 1, 2, 3, 4+ -> 0, .25, .5, .75, 1)
    age against expected life       25   (age / expected life, capped at 1;
                                          0 when either date is missing)
    current status                  20   (Not working 1, Under repair .6)
    last calibration                15   (failed 1, overdue .5)
    no warranty and no contract      5

It is meant to rank machines for review, not to decide: every row lists the
parts that contributed, so a person can see why a machine is near the top.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from CalSoft.models import CalibrationSession

WEIGHTS = {"repairs": 35, "age": 25, "status": 20, "calibration": 15, "cover": 5}
STATUS_FACTOR = {"Not working": 1.0, "Under repair": 0.6}


def _latest_sessions(serials):
    latest = {}
    rows = (CalibrationSession.objects.filter(device_serial__in=serials, active_status=True)
            .exclude(status="rejected")
            .order_by("device_serial", "-timestamp")
            .values_list("device_serial", "overall_pass", "next_calibration_due"))
    for serial, passed, due in rows:
        latest.setdefault(serial, (passed, due))
    return latest


def score_machines(equipment_qs, limit=50, today=None):
    today = today or timezone.localdate()
    since = today - timedelta(days=365)
    machines = list(
        equipment_qs.select_related("description", "department")
        .annotate(repairs=Count("jobcard", filter=Q(jobcard__action_taken="Repair",
                                                    jobcard__date_issued__gte=since)
                                & ~Q(jobcard__status="Declined")),
                  contracts=Count("service_contracts", filter=Q(service_contracts__start_date__lte=today,
                                                                service_contracts__end_date__gte=today,
                                                                service_contracts__active_status=True))))
    sessions = _latest_sessions([m.serial_number for m in machines])
    rows = []
    for m in machines:
        parts = {}
        reasons = []
        parts["repairs"] = min(m.repairs, 4) / 4
        if m.repairs:
            reasons.append(f"{m.repairs} repair{'s' if m.repairs != 1 else ''} in 12 months")
        if m.purchase_date and m.expected_life_years:
            age_years = (today - m.purchase_date).days / 365.25
            parts["age"] = min(age_years / m.expected_life_years, 1.0)
            if parts["age"] >= 0.8:
                reasons.append(f"{age_years:.1f} of {m.expected_life_years} years' expected life")
        else:
            parts["age"] = 0.0
        parts["status"] = STATUS_FACTOR.get(m.status, 0.0)
        if parts["status"]:
            reasons.append(m.status)
        passed, due = sessions.get(m.serial_number, (None, None))
        parts["calibration"] = 1.0 if passed is False else (0.5 if due and due < today else 0.0)
        if passed is False:
            reasons.append("failed its last calibration")
        elif parts["calibration"]:
            reasons.append("calibration overdue")
        # Only for machines whose records are kept (a purchase or warranty date),
        # so a machine nobody has filled in is not marked down for it.
        uncovered = not m.contracts and not (m.warranty_end and m.warranty_end >= today)
        parts["cover"] = 1.0 if uncovered and (m.warranty_end or m.purchase_date) else 0.0
        if parts["cover"]:
            reasons.append("warranty ended, no service contract" if m.warranty_end
                           else "no warranty or service contract on record")
        score = round(sum(WEIGHTS[k] * v for k, v in parts.items()))
        if score:
            rows.append({"equipment": m, "score": score, "reasons": reasons})
    rows.sort(key=lambda r: (-r["score"], r["equipment"].serial_number))
    return rows[:limit]
