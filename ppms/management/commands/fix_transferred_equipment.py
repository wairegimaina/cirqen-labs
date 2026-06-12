"""
fix_transferred_equipment.py
-----------------------------
One-time repair command for equipment that was transferred before the
workshop-field bug was patched.

WHAT THE BUG DID:
  When equipment was transferred to a new department/workshop, the
  `workshop_id` column on the Equipment row was never updated — it kept
  pointing to the OLD workshop.  Because PPM initialisation queries
  Equipment.objects.filter(workshop_id=<new_workshop>), those items were
  invisible to the scheduler and never got PPM schedules.

WHAT THIS COMMAND DOES:
  1. Finds every active Equipment row where workshop_id ≠ department.workshop
     (these are the "stuck" transferred items).
  2. Fixes workshop_id so it matches the department's workshop.
  3. Creates a PPM schedule for each fixed item that doesn't already have one,
     using the same scheduling logic as initialize_ppm_schedule_with_logic.

USAGE:
  # Preview — show what would be fixed without changing anything
  python manage.py fix_transferred_equipment --dry-run

  # Fix a specific workshop only
  python manage.py fix_transferred_equipment --workshop-id <UUID>

  # Fix all workshops
  python manage.py fix_transferred_equipment

  # Fix and also create missing PPM schedules
  python manage.py fix_transferred_equipment --create-ppms

  # Full run: fix + schedule, scoped to one workshop
  python manage.py fix_transferred_equipment --workshop-id <UUID> --create-ppms

OPTIONS:
  --dry-run          Show what would change — no DB writes
  --workshop-id      Limit repair to a single workshop UUID
  --create-ppms      After fixing workshop_id, create missing PPM schedules
  --maintenance-period  Months between PPM visits (default 6)
  --base-month       Month number to start scheduling from (default 1)
  --base-year        Year to start scheduling from (default: current year)
  --max-per-month    Max equipment scheduled per month (default 20)
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.db.models import F

from Inventory.models import Equipment
from workshop.models import Workshop
from ppms.models import PPMSchedule

from datetime import date
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Fix equipment whose workshop_id does not match their department\'s workshop '
        '(caused by the transfer bug), then optionally create missing PPM schedules.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without writing to the database',
        )
        parser.add_argument(
            '--workshop-id',
            type=str,
            default=None,
            help='Limit repair to a single workshop UUID (the DESTINATION workshop)',
        )
        parser.add_argument(
            '--create-ppms',
            action='store_true',
            help='After fixing workshop_id, create PPM schedules for equipment that have none',
        )
        parser.add_argument(
            '--maintenance-period',
            type=int,
            default=6,
            help='Maintenance interval in months used when creating PPM schedules (default: 6)',
        )
        parser.add_argument(
            '--base-month',
            type=int,
            default=1,
            help='Month number (1-12) to start scheduling from (default: 1)',
        )
        parser.add_argument(
            '--base-year',
            type=int,
            default=None,
            help='Year to start scheduling from (default: current year)',
        )
        parser.add_argument(
            '--max-per-month',
            type=int,
            default=20,
            help='Maximum equipment items to schedule per month (default: 20)',
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        workshop_id = options['workshop_id']
        create_ppms = options['create_ppms']
        maintenance_period = options['maintenance_period']
        base_month = options['base_month']
        base_year = options['base_year'] or date.today().year
        max_per_month = options['max_per_month']

        self._banner('FIX TRANSFERRED EQUIPMENT' + (' [DRY RUN]' if dry_run else ''))

        # ----------------------------------------------------------------
        # STEP 1 — Find mismatched equipment
        # ----------------------------------------------------------------
        self.stdout.write('\n📋 STEP 1: Scanning for mismatched equipment...')

        # Equipment whose stored workshop_id differs from their department's workshop
        mismatched_qs = Equipment.objects.filter(
            active_status=True
        ).exclude(
            workshop=F('department__workshop')
        ).select_related(
            'department',
            'department__workshop',
            'description',
        )

        if workshop_id:
            # Scope to equipment now living in a specific destination workshop
            mismatched_qs = mismatched_qs.filter(department__workshop_id=workshop_id)
            self.stdout.write(f'   ↳ Scoped to destination workshop: {workshop_id}')

        mismatched = list(mismatched_qs)
        total = len(mismatched)

        if total == 0:
            self.stdout.write(self.style.SUCCESS('   ✅ No mismatched equipment found — nothing to fix.'))
            return

        self.stdout.write(self.style.WARNING(f'   ⚠️  Found {total} equipment item(s) with wrong workshop_id\n'))

        # Print a summary table
        self.stdout.write(f'   {"Serial Number":<20} {"Description":<30} {"Old Workshop (stored)":<25} {"Correct Workshop (dept)":<25}')
        self.stdout.write('   ' + '-' * 105)

        for eq in mismatched:
            old_ws = eq.workshop.name if eq.workshop else 'None'
            correct_ws = eq.department.workshop.name if eq.department and eq.department.workshop else 'None'
            desc = (eq.description.name[:28] + '..') if eq.description and len(eq.description.name) > 30 else (eq.description.name if eq.description else 'N/A')
            serial = eq.serial_number or 'N/A'
            self.stdout.write(f'   {serial:<20} {desc:<30} {old_ws:<25} {correct_ws:<25}')

        # ----------------------------------------------------------------
        # STEP 2 — Fix workshop_id
        # ----------------------------------------------------------------
        self.stdout.write(f'\n🔧 STEP 2: {"[DRY RUN] Would fix" if dry_run else "Fixing"} workshop_id for {total} equipment item(s)...')

        fixed_count = 0
        fix_errors = 0

        if not dry_run:
            try:
                with transaction.atomic():
                    for eq in mismatched:
                        correct_workshop = eq.department.workshop
                        try:
                            eq.workshop = correct_workshop
                            eq.updated_at = timezone.now()
                            eq.save(update_fields=['workshop', 'updated_at'])
                            fixed_count += 1
                            logger.info(
                                f'Fixed equipment {eq.serial_number} '
                                f'workshop → {correct_workshop.name}'
                            )
                        except Exception as e:
                            fix_errors += 1
                            self.stdout.write(self.style.ERROR(
                                f'   ❌ Failed to fix {eq.serial_number}: {e}'
                            ))
                            logger.error(f'fix_transferred_equipment: failed for {eq.id}: {e}')

                self.stdout.write(self.style.SUCCESS(f'   ✅ Fixed {fixed_count} equipment item(s)'))
                if fix_errors:
                    self.stdout.write(self.style.ERROR(f'   ❌ {fix_errors} item(s) failed — check logs'))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'   ❌ Transaction rolled back: {e}'))
                logger.exception('fix_transferred_equipment: transaction failed')
                return
        else:
            self.stdout.write(self.style.WARNING(f'   ↳ [DRY RUN] Would fix {total} item(s) — no changes made'))
            fixed_count = total

        # ----------------------------------------------------------------
        # STEP 3 — Create missing PPM schedules (optional)
        # ----------------------------------------------------------------
        if not create_ppms:
            self._print_summary(dry_run, fixed_count, fix_errors, 0, 0, 0)
            self.stdout.write(
                '\n💡 Tip: run with --create-ppms to also generate PPM schedules '
                'for these equipment items.\n'
            )
            return

        self.stdout.write(f'\n📅 STEP 3: {"[DRY RUN] Would create" if dry_run else "Creating"} missing PPM schedules...')

        # After fixing, find equipment in the target workshop(s) that have no PPM schedule
        if workshop_id:
            unscheduled_qs = Equipment.objects.filter(
                workshop_id=workshop_id,
                active_status=True,
            )
        else:
            # Re-fetch all equipment that was just fixed, using their new correct workshop
            fixed_ids = [eq.id for eq in mismatched]
            unscheduled_qs = Equipment.objects.filter(id__in=fixed_ids, active_status=True)

        # Exclude equipment that already has a PPM schedule
        already_scheduled_ids = set(
            PPMSchedule.objects.filter(
                equipment__in=unscheduled_qs
            ).values_list('equipment_id', flat=True)
        )

        unscheduled = list(
            unscheduled_qs.exclude(id__in=already_scheduled_ids)
            .select_related('department', 'department__workshop', 'description')
            .order_by('department__name', 'description__name')
        )

        unscheduled_count = len(unscheduled)

        if unscheduled_count == 0:
            self.stdout.write(self.style.SUCCESS('   ✅ All equipment already has PPM schedules — nothing to create.'))
            self._print_summary(dry_run, fixed_count, fix_errors, 0, 0, 0)
            return

        self.stdout.write(f'   ↳ {unscheduled_count} equipment item(s) need a PPM schedule\n')

        start_date = date(base_year, base_month, 1)
        month_count = defaultdict(int)

        # Pre-load existing schedule counts per month for capacity tracking
        all_schedules = PPMSchedule.objects.filter(
            equipment__active_status=True
        )
        if workshop_id:
            all_schedules = all_schedules.filter(workshop_id=workshop_id)

        for sched in all_schedules:
            month_key = sched.scheduled_month.replace(day=1)
            month_count[month_key] += 1

        created_count = 0
        skipped_count = 0
        ppm_errors = 0

        for eq in unscheduled:
            workshop = eq.department.workshop if eq.department else None
            if not workshop:
                self.stdout.write(self.style.WARNING(
                    f'   ⚠️  Skipping {eq.serial_number} — no workshop on department'
                ))
                skipped_count += 1
                continue

            # Find the first month under capacity
            month_offset = 0
            max_attempts = 36
            while month_offset < max_attempts:
                candidate = start_date + relativedelta(months=month_offset)
                if month_count[candidate] < max_per_month:
                    break
                month_offset += 1

            scheduled_month = start_date + relativedelta(months=month_offset)

            dept_name = eq.department.name if eq.department else 'No Dept'
            desc_name = eq.description.name if eq.description else 'No Desc'

            self.stdout.write(
                f'   {"[WOULD CREATE]" if dry_run else "[CREATING]"} '
                f'{eq.serial_number} ({desc_name}, {dept_name}) → {scheduled_month.strftime("%B %Y")} '
                f'[{workshop.name}]'
            )

            if not dry_run:
                try:
                    PPMSchedule.objects.create(
                        workshop=workshop,
                        equipment=eq,
                        scheduled_month=scheduled_month,
                        status='pending',
                        maintenance_period=maintenance_period,
                        planning_logic='department',
                    )
                    created_count += 1
                    month_count[scheduled_month] += 1
                    logger.info(
                        f'Created PPM schedule for {eq.serial_number} → {scheduled_month}'
                    )
                except Exception as e:
                    ppm_errors += 1
                    self.stdout.write(self.style.ERROR(f'      ❌ Failed: {e}'))
                    logger.error(f'fix_transferred_equipment: PPM create failed for {eq.id}: {e}')
            else:
                created_count += 1
                month_count[scheduled_month] += 1  # simulate capacity filling up

        self._print_summary(dry_run, fixed_count, fix_errors, created_count, skipped_count, ppm_errors)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _banner(self, title):
        line = '=' * 80
        self.stdout.write(self.style.SUCCESS(f'\n{line}'))
        self.stdout.write(self.style.SUCCESS(f'  {title}'))
        self.stdout.write(self.style.SUCCESS(f'{line}\n'))

    def _print_summary(self, dry_run, fixed, fix_errors, ppms_created, ppms_skipped, ppm_errors):
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('  SUMMARY'))
        self.stdout.write('=' * 80)

        mode = '[DRY RUN — no changes written]' if dry_run else '[LIVE RUN]'
        self.stdout.write(f'  Mode                   : {mode}')
        self.stdout.write(f'  Equipment fixed        : {fixed}')
        if fix_errors:
            self.stdout.write(self.style.ERROR(f'  Fix errors             : {fix_errors}'))
        if ppms_created or ppms_skipped or ppm_errors:
            self.stdout.write(f'  PPM schedules created  : {ppms_created}')
            if ppms_skipped:
                self.stdout.write(self.style.WARNING(f'  PPM skipped            : {ppms_skipped}'))
            if ppm_errors:
                self.stdout.write(self.style.ERROR(f'  PPM errors             : {ppm_errors}'))

        self.stdout.write('=' * 80 + '\n')
