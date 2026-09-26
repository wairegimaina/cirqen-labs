"""Check the schedule data before tightening it (phase 1 of the redesign).

    manage.py scheduling_audit                     # read-only report, both programmes
    manage.py scheduling_audit --program ppm --workshop "Biomed"
    manage.py scheduling_audit --retire-duplicates [--apply]
    manage.py scheduling_audit --floor-dates [--apply]

Without --apply the repairs only list what they would do. They touch open
schedules only, never completed ones, and never delete.
"""
from django.core.management.base import BaseCommand, CommandError

from scheduling import audit, engine, planner
from workshop.models import Workshop


class Command(BaseCommand):
    help = "Report (and optionally repair) duplicate open schedules and off-month dates"

    def add_arguments(self, parser):
        parser.add_argument("--program", choices=list(planner.PROGRAMS))
        parser.add_argument("--workshop", help="Workshop name")
        parser.add_argument("--retire-duplicates", action="store_true")
        parser.add_argument("--floor-dates", action="store_true")
        parser.add_argument("--apply", action="store_true", help="Write the repairs (default: list only)")
        parser.add_argument("--limit", type=int, default=20, help="Rows listed per section")

    def handle(self, *args, program=None, workshop=None, retire_duplicates=False, floor_dates=False,
               apply=False, limit=20, **_):
        ws = None
        if workshop:
            ws = Workshop.objects.filter(name__iexact=workshop).first()
            if not ws:
                raise CommandError(f"No workshop {workshop!r}")
        out = self.stdout.write
        for key in ([program] if program else list(planner.PROGRAMS)):
            if retire_duplicates:
                decisions = audit.retire_duplicates(key, apply=apply, workshop=ws)
                out(f"[{key}] {'Retired' if apply else 'Would retire'} extra open schedules for "
                    f"{len(decisions)} equipment")
                for serial, kept, retired in decisions[:limit]:
                    out(f"  {serial}: keep {engine.month_name(kept)}, retire "
                        f"{', '.join(engine.month_name(m) for m in retired)}")
            if floor_dates:
                moved, blocked = audit.floor_dates(key, apply=apply, workshop=ws)
                out(f"[{key}] {'Moved' if apply else 'Would move'} {len(moved)} open schedules to the 1st; "
                    f"{len(blocked)} blocked (the 1st is already taken)")
                for serial, old, new in moved[:limit]:
                    out(f"  {serial}: {old:%d %b %Y} -> {new:%d %b %Y}")
                for serial, old in blocked[:limit]:
                    out(f"  {serial}: {old:%d %b %Y} left as is")
            if not (retire_duplicates or floor_dates):
                self._report(audit.audit(key, workshop=ws), limit)

    def _report(self, r, limit):
        out = self.stdout.write
        out(f"== {r.program} ==")
        out(f"  Active equipment              {r.equipment}")
        out(f"  With an open schedule         {r.scheduled}")
        out(f"  History but nothing open      {r.history_only}   (broken chains)")
        out(f"  Never scheduled               {r.never_scheduled}")
        out(f"  More than one open schedule   {len(r.duplicates)}   (must be 0 before the constraint)")
        out(f"  Open, not dated the 1st       {r.off_first}")
        out(f"  Open, equipment inactive      {r.inactive_equipment_open}")
        out(f"  Open, no interval             {r.open_without_interval}")
        out(f"  Completed, no completion date {r.completed_without_date}")
        out(f"  Overdue                       {r.overdue}")
        out(f"  Workshops on a plan           {r.planned_workshops}")
        if r.by_month:
            out("  Open schedules by month:")
            for month in sorted(r.by_month)[:18]:
                out(f"    {engine.month_name(month)}  {r.by_month[month]}")
        for eq_id, serial, months in r.duplicates[:limit]:
            out(f"  duplicate: {serial}: {', '.join(engine.month_name(m) for m in months)}")
