"""Predict which devices are likely to need corrective maintenance soon.

The data a CMMS actually has is a history of repair work orders, so the model
is deliberately simple and explainable rather than a black box:

1. **Failure rate per device** (repairs per year) from its own repair work
   orders, shrunk towards the rate of its equipment description (the "fleet").
   A device with two years of clean history and one of 40 infusion pumps that
   fail twice a year should not be scored as "never fails"; a device with five
   repairs in a year should not be averaged away. This is the standard
   Poisson-Gamma (empirical Bayes) estimate:

       rate = (repairs + prior_strength * fleet_rate) / (exposure_years + prior_strength)

   Repairs and exposure are both weighted by age (half-life one year), so a
   device that has started failing more often is scored on how it behaves
   now, not on a three-year average.

2. **Probability of a failure within the horizon**, treating failures as a
   Poisson process: ``p = 1 - exp(-rate * horizon_years)``.

3. **Adjustments** for condition signals the CMMS records, each named in the
   result's ``reasons`` so a technician can see *why* a device is flagged:
   overdue PPMs, failed checklist steps, and a repair in the last 30 days
   (repeat failures cluster).

The adjustment weights are starting values, not fitted ones. The
``backtest_failure_prediction`` management command replays history to show
how well the ranking would have predicted the repairs that actually happened;
re-tune the weights once a hospital has a year or more of work orders.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Q
from django.utils.timezone import localdate

LOOKBACK_YEARS = 3          # repairs older than this don't count
PRIOR_STRENGTH_YEARS = 0.5  # how many (weighted) device-years of fleet history the prior is worth
HALF_LIFE_DAYS = 365        # a repair this old counts half as much as one today
DEFAULT_FLEET_RATE = 0.5    # repairs/year when a description has no history at all
MIN_EXPOSURE_YEARS = 0.1

OVERDUE_PPM_WEIGHT = 0.25   # per overdue PPM, up to 4
CHECKLIST_FAIL_WEIGHT = 0.30  # per failed/skipped step in the last 90 days, up to 3
RECENT_REPAIR_FACTOR = 1.3  # repaired in the last 30 days

HIGH, MEDIUM = 0.5, 0.25


@dataclass
class Prediction:
    equipment_id: object
    probability: float                 # of at least one failure within the horizon
    rate_per_year: float
    expected_days_between_failures: int | None
    predicted_next_failure: date | None
    repairs_in_window: int
    last_repair: date | None
    reasons: list[str] = field(default_factory=list)
    equipment: object = None

    @property
    def risk_level(self):
        if self.probability >= HIGH:
            return 'High'
        if self.probability >= MEDIUM:
            return 'Medium'
        return 'Low'

    @property
    def risk_percent(self):
        return round(self.probability * 100)


def _weight(age_days):
    return 0.5 ** (max(age_days, 0) / HALF_LIFE_DAYS)


def _weighted_exposure_years(age_days):
    """Integral of the age weight over the time a device has been in service."""
    age_days = max(age_days, 0)
    return HALF_LIFE_DAYS / math.log(2) * (1 - _weight(age_days)) / 365.0


def _in_service_date(equipment, window_start):
    """When the device went into service: its first warranty's start date, else when it was registered."""
    starts = [w.start_date for w in equipment.warranties.all() if w.active_status]
    start = min(starts) if starts else (equipment.created_at.date() if equipment.created_at else window_start)
    return max(start, window_start)


def _repair_dates(equipment_ids, since, until):
    """{equipment_id: sorted repair dates} from non-declined repair work orders."""
    from jobcard.models import jobcard

    rows = (
        jobcard.objects.filter(
            equipment_id__in=equipment_ids,
            action_taken='Repair',
            active_status=True,
            date_issued__gte=since,
            date_issued__lte=until,
        )
        .exclude(status='Declined')
        .values_list('equipment_id', 'date_issued')
    )
    dates = defaultdict(list)
    for equipment_id, issued in rows:
        dates[equipment_id].append(issued)
    for values in dates.values():
        values.sort()
    return dates


def _overdue_ppm_counts(equipment_ids, today):
    from ppms.models import PPMSchedule

    month_start = today.replace(day=1)
    counts = defaultdict(int)
    for equipment_id in (
        PPMSchedule.objects.filter(
            equipment_id__in=equipment_ids, active_status=True, scheduled_month__lt=month_start,
        ).exclude(status='completed').values_list('equipment_id', flat=True)
    ):
        counts[equipment_id] += 1
    return counts


def _checklist_problem_counts(equipment_ids, since):
    from jobcard.models import WorkOrderChecklistEntry

    counts = defaultdict(int)
    for equipment_id in (
        WorkOrderChecklistEntry.objects.filter(
            job_card__equipment_id__in=equipment_ids,
            job_card__date_issued__gte=since,
            active_status=True,
        ).filter(Q(result='fail') | Q(result='not_done')).values_list('job_card__equipment_id', flat=True)
    ):
        counts[equipment_id] += 1
    return counts


def predict(equipment_qs, horizon_days=90, today=None, include_signals=True):
    """Score every device in ``equipment_qs``; highest risk first.

    ``today`` lets the backtest replay the model as of a past date: only work
    orders on or before it are used. ``include_signals`` turns off the PPM and
    checklist adjustments, whose history can't be replayed (they only record
    current state).
    """
    today = today or localdate()
    window_start = today - timedelta(days=365 * LOOKBACK_YEARS)
    equipment = list(equipment_qs.select_related('description', 'department', 'department__workshop')
                     .prefetch_related('warranties'))
    if not equipment:
        return []
    ids = [e.id for e in equipment]

    repairs = _repair_dates(ids, window_start, today)
    overdue = _overdue_ppm_counts(ids, today) if include_signals else {}
    problems = _checklist_problem_counts(ids, today - timedelta(days=90)) if include_signals else {}

    # Fleet rate per description: all repairs / all exposure, over the same window.
    fleet_repairs, fleet_exposure = defaultdict(int), defaultdict(float)
    exposure = {}
    weighted_repairs = {}
    for e in equipment:
        years = max(_weighted_exposure_years((today - _in_service_date(e, window_start)).days), MIN_EXPOSURE_YEARS)
        exposure[e.id] = years
        weighted_repairs[e.id] = sum(_weight((today - d).days) for d in repairs.get(e.id, ()))
        fleet_repairs[e.description_id] += weighted_repairs[e.id]
        fleet_exposure[e.description_id] += years

    horizon_years = horizon_days / 365.0
    results = []
    for e in equipment:
        dates = repairs.get(e.id, [])
        n = len(dates)
        fleet_rate = (fleet_repairs[e.description_id] / fleet_exposure[e.description_id]
                      if fleet_repairs[e.description_id] else DEFAULT_FLEET_RATE)
        rate = (weighted_repairs[e.id] + PRIOR_STRENGTH_YEARS * fleet_rate) / (exposure[e.id] + PRIOR_STRENGTH_YEARS)

        reasons = []
        if n:
            reasons.append(f"{n} repair{'s' if n != 1 else ''} in the last {LOOKBACK_YEARS} years")
        multiplier = 1.0
        last = dates[-1] if dates else None
        if last and (today - last).days <= 30:
            multiplier *= RECENT_REPAIR_FACTOR
            reasons.append(f"repaired {(today - last).days} days ago")
        if overdue.get(e.id):
            k = min(overdue[e.id], 4)
            multiplier *= 1 + OVERDUE_PPM_WEIGHT * k
            reasons.append(f"{overdue[e.id]} overdue PPM{'s' if overdue[e.id] != 1 else ''}")
        if problems.get(e.id):
            k = min(problems[e.id], 3)
            multiplier *= 1 + CHECKLIST_FAIL_WEIGHT * k
            reasons.append(f"{problems[e.id]} failed checklist step{'s' if problems[e.id] != 1 else ''} in 90 days")
        if n == 0:
            reasons.append(f"no repairs; based on {e.description.name} fleet rate")

        adjusted = rate * multiplier
        probability = 1 - math.exp(-adjusted * horizon_years)
        mtbf_days = round(365 / adjusted) if adjusted > 0 else None
        anchor = last or _in_service_date(e, window_start)
        next_failure = max(today, anchor + timedelta(days=mtbf_days)) if mtbf_days else None

        results.append(Prediction(
            equipment_id=e.id,
            probability=probability,
            rate_per_year=round(adjusted, 2),
            expected_days_between_failures=mtbf_days,
            predicted_next_failure=next_failure,
            repairs_in_window=n,
            last_repair=last,
            reasons=reasons,
            equipment=e,
        ))

    results.sort(key=lambda p: p.probability, reverse=True)
    return results


def backtest(equipment_qs, as_of, horizon_days=90, top_fraction=0.2):
    """How well a prediction made on ``as_of`` ranked the repairs that followed.

    Returns the share of devices that actually failed in the horizon which were
    in the top ``top_fraction`` of the ranking (recall@top), the failure rate in
    that top group against the base rate (lift), and the counts behind them.
    """
    equipment_qs = equipment_qs.filter(created_at__date__lte=as_of)
    predictions = predict(equipment_qs, horizon_days=horizon_days, today=as_of, include_signals=False)
    if not predictions:
        return None
    after = _repair_dates([p.equipment_id for p in predictions],
                          as_of + timedelta(days=1), as_of + timedelta(days=horizon_days))
    failed = {eid for eid, dates in after.items() if dates}
    top_n = max(1, round(len(predictions) * top_fraction))
    top = {p.equipment_id for p in predictions[:top_n]}
    hits = len(failed & top)
    base_rate = len(failed) / len(predictions)
    top_rate = hits / top_n
    return {
        'as_of': as_of,
        'devices': len(predictions),
        'failed_in_horizon': len(failed),
        'top_n': top_n,
        'hits_in_top': hits,
        'recall_at_top': (hits / len(failed)) if failed else None,
        'base_rate': base_rate,
        'top_rate': top_rate,
        'lift': (top_rate / base_rate) if base_rate else None,
    }
