"""
Backfill CalibrationReading statistics that were never computed because of the
_calculate_stats() bug in view_modules/calibration.py (fixed: that helper only
ever wrote the uncertainty fields, never mean/standard_deviation/error/
passes_tolerance, so those fields are stuck at their model defaults on every
reading saved before the fix).

This command:
  1. Finds every CalibrationReading with mean IS NULL that actually has raw
     reading_1..reading_10 values saved, and calls reading.calculate_statistics()
     on it (the correct, existing model method — this is exactly what
     _calculate_stats() should have been calling all along).
  2. Recomputes overall_pass on every CalibrationSession that had at least one
     reading change, since overall_pass was locked in at submission time using
     the never-set (always-False-by-default) passes_tolerance.
  3. Backfills HistoricalCalibration rows for the now-fixed readings, since
     _store_historical_data() correctly skipped them at the time (they had no
     mean to store) — this is why drift analysis has nothing for old sessions.
     Skips creating a duplicate if a matching row already exists.

Usage:
    python manage.py backfill_calibration_stats            # apply changes
    python manage.py backfill_calibration_stats --dry-run   # preview only
    python manage.py backfill_calibration_stats --no-historical  # skip step 3
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from CalSoft.models import CalibrationReading, CalibrationSession, HistoricalCalibration


class Command(BaseCommand):
    help = (
        "Backfill mean/std_dev/error/uncertainties/passes_tolerance for "
        "CalibrationReading rows saved before the _calculate_stats() bug was "
        "fixed, recompute affected sessions' overall_pass, and backfill "
        "HistoricalCalibration rows for drift analysis."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without saving anything.",
        )
        parser.add_argument(
            "--no-historical",
            action="store_true",
            help="Skip backfilling HistoricalCalibration rows (step 3).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        skip_historical = options["no_historical"]

        broken_readings = CalibrationReading.objects.filter(
            mean__isnull=True, active_status=True
        ).select_related("session", "parameter", "sub_parameter", "set_value")

        total = broken_readings.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to backfill — no readings with mean=NULL."))
            return

        self.stdout.write(f"Found {total} reading(s) with mean=NULL. {'(dry run) ' if dry_run else ''}Processing...")

        fixed = 0
        insufficient = 0
        errors = 0
        affected_sessions = set()
        historical_created = 0
        historical_skipped = 0

        for reading in broken_readings:
            raw_values = reading.get_readings_list()
            if len(raw_values) < 2:
                insufficient += 1
                continue

            try:
                with transaction.atomic():
                    ok = reading.calculate_statistics()  # saves the reading itself
                    if not ok:
                        errors += 1
                        continue

                    fixed += 1
                    affected_sessions.add(reading.session_id)

                    if not skip_historical and reading.mean is not None and reading.session.device_serial:
                        session = reading.session
                        exists = HistoricalCalibration.objects.filter(
                            device_serial=session.device_serial,
                            parameter_name=reading.parameter.name,
                            sub_parameter_name=reading.sub_parameter.name if reading.sub_parameter else "",
                            set_value=reading.set_value.value,
                            calibration_date=session.timestamp,
                        ).exists()

                        if exists:
                            historical_skipped += 1
                        else:
                            HistoricalCalibration.objects.create(
                                device_serial=session.device_serial,
                                parameter_name=reading.parameter.name,
                                sub_parameter_name=reading.sub_parameter.name if reading.sub_parameter else "",
                                set_value=reading.set_value.value,
                                measured_value=reading.mean,
                                error=reading.error,
                                uncertainty=reading.expanded_uncertainty,
                                calibration_date=session.timestamp,
                            )
                            historical_created += 1

                    if dry_run:
                        # calculate_statistics() already saved inside the atomic
                        # block above; roll it back since this is a preview.
                        raise _DryRunRollback()

            except _DryRunRollback:
                pass
            except Exception as e:
                errors += 1
                self.stderr.write(self.style.ERROR(f"Error fixing reading {reading.id}: {e}"))

        # Recompute overall_pass for every affected session.
        sessions_updated = 0
        if affected_sessions:
            for session in CalibrationSession.objects.filter(id__in=affected_sessions):
                readings = session.readings.filter(active_status=True)
                if not readings.exists():
                    continue
                new_overall_pass = not readings.filter(passes_tolerance=False).exists()
                if session.overall_pass != new_overall_pass:
                    sessions_updated += 1
                    if not dry_run:
                        session.overall_pass = new_overall_pass
                        session.save(update_fields=["overall_pass"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Readings fixed:              {fixed}"))
        self.stdout.write(f"Readings skipped (<2 values): {insufficient}")
        self.stdout.write(f"Readings errored:             {errors}")
        self.stdout.write(f"Sessions with overall_pass changed: {sessions_updated}")
        if not skip_historical:
            self.stdout.write(f"HistoricalCalibration created: {historical_created}")
            self.stdout.write(f"HistoricalCalibration skipped (already existed): {historical_skipped}")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes were saved."))


class _DryRunRollback(Exception):
    """Internal sentinel to unwind a transaction.atomic() block during --dry-run."""
    pass
