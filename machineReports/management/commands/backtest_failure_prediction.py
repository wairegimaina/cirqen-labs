"""Replay the failure prediction on past dates and report how well it ranked
the repairs that actually followed.

    python manage.py backtest_failure_prediction --months 12 --horizon 90

For each month-start in the range, the model is run using only work orders up
to that date; the devices repaired in the next ``horizon`` days are the
answer key. "Lift" above 1 means the top of the ranking fails more often than
a device picked at random.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand
from django.utils.timezone import localdate

from Inventory.models import Equipment
from machineReports.prediction import backtest


class Command(BaseCommand):
    help = "Backtest the corrective-maintenance prediction against past repair work orders."

    def add_arguments(self, parser):
        parser.add_argument('--months', type=int, default=12)
        parser.add_argument('--horizon', type=int, default=90)
        parser.add_argument('--top', type=float, default=0.2, help="Fraction of devices counted as 'flagged'")

    def handle(self, *args, **opts):
        horizon = opts['horizon']
        last = localdate() - timedelta(days=horizon)
        first = (last - relativedelta(months=opts['months'])).replace(day=1)
        equipment = Equipment.objects.filter(active_status=True)

        rows = []
        as_of = first
        while as_of <= last:
            result = backtest(equipment, as_of, horizon_days=horizon, top_fraction=opts['top'])
            if result:
                rows.append(result)
            as_of += relativedelta(months=1)

        if not rows:
            self.stdout.write("No equipment to backtest.")
            return
        self.stdout.write(f"{'as of':<12}{'devices':>8}{'failed':>8}{'hits':>6}{'recall':>8}{'lift':>7}")
        for r in rows:
            recall = f"{r['recall_at_top']:.0%}" if r['recall_at_top'] is not None else '-'
            lift = f"{r['lift']:.1f}" if r['lift'] is not None else '-'
            self.stdout.write(f"{r['as_of'].isoformat():<12}{r['devices']:>8}{r['failed_in_horizon']:>8}"
                              f"{r['hits_in_top']:>6}{recall:>8}{lift:>7}")
        scored = [r for r in rows if r['failed_in_horizon']]
        if scored:
            hits = sum(r['hits_in_top'] for r in scored)
            failed = sum(r['failed_in_horizon'] for r in scored)
            self.stdout.write(self.style.SUCCESS(
                f"Overall: the top {opts['top']:.0%} flagged {hits} of {failed} devices that needed repair "
                f"({hits / failed:.0%})."
            ))
