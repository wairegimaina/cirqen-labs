"""
management/commands/check_group_alignment.py

Django management command to check and fix group alignment

Usage:
    # Check alignment status
    python manage.py check_group_alignment

    # Check specific planning logic
    python manage.py check_group_alignment --logic department
    python manage.py check_group_alignment --logic description

    # Fix misaligned schedules (dry run first)
    python manage.py check_group_alignment --fix

    # Fix misaligned schedules (live)
    python manage.py check_group_alignment --fix --no-dry-run
"""

from django.core.management.base import BaseCommand
from calSchedules.instant_reconciliation import (
    diagnose_group_alignment,
    fix_group_alignment
)
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check and fix calibration schedule group alignment'

    def add_arguments(self, parser):
        parser.add_argument(
            '--logic',
            type=str,
            default='department',
            choices=['department', 'description'],
            help='Planning logic to check (department or description)'
        )
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Fix misaligned schedules'
        )
        parser.add_argument(
            '--no-dry-run',
            action='store_true',
            help='Actually apply fixes (default is dry run)'
        )

    def handle(self, *args, **options):
        planning_logic = options['logic']
        fix_mode = options['fix']
        dry_run = not options['no_dry_run']

        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS(
            f"🔍 GROUP ALIGNMENT CHECK ({planning_logic.upper()})"
        ))
        self.stdout.write("=" * 80)
        self.stdout.write("")

        # Run diagnostics
        self.stdout.write(self.style.WARNING("Running diagnostics..."))
        results = diagnose_group_alignment(planning_logic)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("📊 RESULTS:"))
        self.stdout.write(f"  Total groups: {results['total_groups']}")
        self.stdout.write(
            self.style.SUCCESS(f"  ✅ Well-grouped: {results['well_grouped']}")
        )

        if results['split_groups'] > 0:
            self.stdout.write(
                self.style.ERROR(f"  ❌ Split groups: {results['split_groups']}")
            )
            self.stdout.write(
                self.style.WARNING(
                    f"  Alignment: {results['alignment_percentage']:.1f}%"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("  ✅ All groups properly aligned!")
            )
            self.stdout.write(
                self.style.SUCCESS(f"  Alignment: 100%")
            )

        # Fix if requested
        if fix_mode:
            self.stdout.write("")
            if dry_run:
                self.stdout.write(
                    self.style.WARNING("🔧 FIXING ALIGNMENT (DRY RUN)...")
                )
                self.stdout.write(
                    "  (Use --no-dry-run to actually apply fixes)"
                )
            else:
                self.stdout.write(
                    self.style.ERROR("🔧 FIXING ALIGNMENT (LIVE)...")
                )
                response = input("  Are you sure? (yes/no): ")
                if response.lower() != 'yes':
                    self.stdout.write(self.style.WARNING("  Cancelled."))
                    return

            fix_results = fix_group_alignment(planning_logic, dry_run)

            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("📊 FIX RESULTS:"))
            self.stdout.write(
                self.style.WARNING(
                    f"  {'Would fix' if dry_run else 'Fixed'}: {fix_results['fixed']} schedules"
                )
            )
            self.stdout.write(
                self.style.SUCCESS(f"  Already aligned: {fix_results['skipped']} schedules")
            )

            if dry_run and fix_results['fixed'] > 0:
                self.stdout.write("")
                self.stdout.write(
                    self.style.WARNING(
                        "  To apply these fixes, run with --no-dry-run"
                    )
                )

        elif results['split_groups'] > 0:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "⚠️  Some groups are split across months!"
                )
            )
            self.stdout.write(
                "  To fix this, run: python manage.py check_group_alignment --fix"
            )

        self.stdout.write("")
        self.stdout.write("=" * 80)
