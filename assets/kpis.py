"""Maintenance KPIs over a period, for any scope of machines.

Definitions (shown on the KPI page too):

* Failure: a Repair work order that was not declined.
* MTBF: machine-days in the period divided by failures ("one failure every
  N days per machine"). Blank when there were no failures.
* MTTR: average hours between a repair's start and finish times, for repairs
  with both recorded (a finish before the start is taken as past midnight).
* Uptime: 100% minus the recorded repair hours as a share of the machines'
  hours in the period. It counts only time spent repairing, not waiting for
  the repair, so it is an upper bound.
* PPM completion: completed PPM schedules as a share of those due in the
  period up to this month.
* Backlog: work orders waiting for approval, their average and oldest age.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db.models import Avg, Count, Min, Q
from django.utils import timezone

from jobcard.models import jobcard
from ppms.models import PPMSchedule


def repair_hours(started, completed):
    if not started or not completed:
        return None
    start = datetime.combine(datetime.min, started)
    end = datetime.combine(datetime.min, completed)
    if end < start:
        end += timedelta(days=1)
    return (end - start).total_seconds() / 3600


@dataclass
class Kpis:
    machines: int
    period_days: int
    failures: int
    mtbf_days: float = None
    mttr_hours: float = None
    uptime_percent: float = None
    ppm_due: int = 0
    ppm_completed: int = 0
    ppm_completion_percent: float = None
    backlog: int = 0
    backlog_avg_days: float = None
    backlog_oldest_days: int = None
    by_type: list = field(default_factory=list)


def compute(equipment_qs, months=12, today=None):
    today = today or timezone.localdate()
    start = today - timedelta(days=round(months * 30.44))
    period_days = (today - start).days or 1
    machines = equipment_qs.count()

    repairs = jobcard.objects.filter(equipment__in=equipment_qs, action_taken="Repair",
                                     date_issued__gte=start).exclude(status="Declined")
    failures = repairs.count()
    durations = [h for h in (repair_hours(s, c) for s, c in repairs.values_list("time_started", "time_completed"))
                 if h is not None]
    result = Kpis(machines=machines, period_days=period_days, failures=failures)
    if failures and machines:
        result.mtbf_days = round(machines * period_days / failures, 1)
    if durations:
        result.mttr_hours = round(sum(durations) / len(durations), 1)
    if machines:
        result.uptime_percent = round(100 - 100 * sum(durations) / (machines * period_days * 24), 2)

    this_month = today.replace(day=1)
    ppm = PPMSchedule.objects.filter(equipment__in=equipment_qs, scheduled_month__gte=start.replace(day=1),
                                     scheduled_month__lte=this_month, active_status=True)
    counts = ppm.aggregate(due=Count("id"), done=Count("id", filter=Q(status="completed")))
    result.ppm_due, result.ppm_completed = counts["due"], counts["done"]
    if counts["due"]:
        result.ppm_completion_percent = round(100 * counts["done"] / counts["due"], 1)

    waiting = jobcard.objects.filter(equipment__in=equipment_qs, status="Waiting Approval")
    backlog = waiting.aggregate(n=Count("id"), oldest=Min("date_issued"))
    result.backlog = backlog["n"]
    if backlog["n"]:
        ages = [(today - d).days for d in waiting.values_list("date_issued", flat=True) if d]
        result.backlog_avg_days = round(sum(ages) / len(ages), 1) if ages else None
        result.backlog_oldest_days = (today - backlog["oldest"]).days if backlog["oldest"] else None

    per_type = (repairs.values("equipment__description__name")
                .annotate(failures=Count("id"), machines=Count("equipment", distinct=True))
                .order_by("-failures")[:15])
    type_totals = dict(equipment_qs.values_list("description__name").annotate(n=Count("id")))
    for row in per_type:
        name = row["equipment__description__name"]
        fleet = type_totals.get(name, row["machines"]) or 1
        result.by_type.append({
            "type": name, "machines": fleet, "failures": row["failures"],
            "mtbf_days": round(fleet * period_days / row["failures"], 1),
        })
    return result
