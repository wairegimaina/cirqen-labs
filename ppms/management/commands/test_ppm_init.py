from django.core.management.base import BaseCommand
from django.utils import timezone
from ppms.models import PPMSchedule
from Inventory.models import Equipment
from workshop.models import Workshop
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import logging

logger = logging.getLogger('PPM.tasks')


class Command(BaseCommand):
    help = 'Test PPM initialization directly (synchronous - no Celery)'

    def add_arguments(self, parser):
        parser.add_argument('workshop_id', type=str, help='Workshop UUID')
        parser.add_argument('--planning-logic', type=str, default='department', help='Planning logic')
        parser.add_argument('--maintenance-period', type=int, default=6, help='Maintenance period in months')
        parser.add_argument('--base-month', type=int, default=1, help='Base month')
        parser.add_argument('--base-year', type=int, default=2025, help='Base year')
        parser.add_argument('--max-departments', type=int, default=20, help='Max departments per month')
        parser.add_argument('--max-descriptions', type=int, default=20, help='Max descriptions per month')
        parser.add_argument('--dry-run', action='store_true', help='Dry run - dont save anything')

    def handle(self, *args, **options):
        workshop_id = options['workshop_id']

        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS('PPM INITIALIZATION TEST - SYNCHRONOUS MODE'))
        self.stdout.write(self.style.SUCCESS('='*80 + '\n'))

        # Step 1: Verify workshop exists
        self.stdout.write('Step 1: Verifying workshop...')
        try:
            workshop = Workshop.objects.get(id=workshop_id)
            self.stdout.write(self.style.SUCCESS(f'  ✓ Workshop found: {workshop.name} (ID: {workshop.id})'))
        except Workshop.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'  ✗ Workshop {workshop_id} not found'))
            return

        # Step 2: Count active equipment
        self.stdout.write('\nStep 2: Counting active equipment...')
        active_equipment = Equipment.objects.filter(
            workshop_id=workshop_id,
            active_status=True
        )
        equipment_count = active_equipment.count()
        self.stdout.write(self.style.SUCCESS(f'  ✓ Found {equipment_count} active equipment items'))

        if equipment_count == 0:
            self.stdout.write(self.style.WARNING('  ⚠ No active equipment to schedule'))
            return

        # Step 3: Check existing schedules
        self.stdout.write('\nStep 3: Checking existing schedules...')
        existing_schedules = PPMSchedule.objects.filter(
            workshop_id=workshop_id,
            equipment__active_status=True
        )
        existing_count = existing_schedules.count()
        self.stdout.write(self.style.SUCCESS(f'  ✓ Found {existing_count} existing schedules'))

        # Step 4: Identify unscheduled equipment
        self.stdout.write('\nStep 4: Identifying unscheduled equipment...')
        scheduled_equipment_ids = set(existing_schedules.values_list('equipment_id', flat=True))
        unscheduled_equipment = active_equipment.exclude(id__in=scheduled_equipment_ids)
        unscheduled_count = unscheduled_equipment.count()
        self.stdout.write(self.style.SUCCESS(f'  ✓ Found {unscheduled_count} unscheduled equipment items'))

        if unscheduled_count == 0:
            self.stdout.write(self.style.WARNING('  ⚠ All equipment is already scheduled'))
            return

        # Step 5: Show sample of unscheduled equipment
        self.stdout.write('\nStep 5: Sample of unscheduled equipment (first 10):')
        for i, equip in enumerate(unscheduled_equipment[:10], 1):
            dept_name = equip.department.name if equip.department else 'No Dept'
            desc_name = equip.description.name if equip.description else 'No Desc'
            self.stdout.write(f'  {i}. {desc_name} - {dept_name} (ID: {equip.id})')

        # Step 6: Simulate scheduling logic
        self.stdout.write('\nStep 6: Simulating scheduling logic...')
        planning_logic = options['planning_logic']
        maintenance_period = options['maintenance_period']
        base_month = options['base_month']
        base_year = options['base_year']
        max_departments = options['max_departments']
        max_descriptions = options['max_descriptions']

        self.stdout.write(f'  Planning Logic: {planning_logic}')
        self.stdout.write(f'  Maintenance Period: {maintenance_period} months')
        self.stdout.write(f'  Base Date: {base_month}/{base_year}')
        self.stdout.write(f'  Max Departments/Month: {max_departments}')
        self.stdout.write(f'  Max Descriptions/Month: {max_descriptions}')

        start_date = date(base_year, base_month, 1)
        month_dept_count = defaultdict(int)
        month_desc_count = defaultdict(int)

        # Count existing schedules per month
        for sched in existing_schedules:
            month_key = sched.scheduled_month.replace(day=1)
            if sched.equipment.department:
                month_dept_count[month_key] += 1
            if sched.equipment.description:
                month_desc_count[month_key] += 1

        # Step 7: Process equipment (dry run or actual)
        self.stdout.write('\nStep 7: Processing equipment...')
        equipment_list = list(unscheduled_equipment.select_related('department', 'description'))
        created_count = 0
        failed_count = 0

        for i, equip in enumerate(equipment_list, 1):
            # Find suitable month
            month_offset = 0
            max_attempts = 36

            while month_offset < max_attempts:
                scheduled_month = start_date + relativedelta(months=month_offset)
                dept_count = month_dept_count[scheduled_month]
                desc_count = month_desc_count[scheduled_month]

                if planning_logic == 'department':
                    if dept_count < max_departments or not equip.department:
                        break
                else:
                    if desc_count < max_descriptions or not equip.description:
                        break

                month_offset += 1

            if month_offset >= max_attempts:
                scheduled_month = start_date + relativedelta(months=month_offset)

            # Log what would be created
            dept_name = equip.department.name if equip.department else 'No Dept'
            desc_name = equip.description.name if equip.description else 'No Desc'

            if i <= 10:  # Show first 10
                self.stdout.write(
                    f'  {i}. {desc_name} ({dept_name}) → {scheduled_month.strftime("%B %Y")}'
                )

            # Create schedule if not dry run
            if not options['dry_run']:
                try:
                    PPMSchedule.objects.create(
                        workshop=workshop,
                        equipment=equip,
                        scheduled_month=scheduled_month,
                        status='pending',
                        maintenance_period=maintenance_period,
                        planning_logic=planning_logic
                    )
                    created_count += 1

                    # Update counters
                    if equip.department and planning_logic == 'department':
                        month_dept_count[scheduled_month] += 1
                    if equip.description and planning_logic == 'description':
                        month_desc_count[scheduled_month] += 1

                except Exception as e:
                    failed_count += 1
                    if i <= 10:
                        self.stdout.write(self.style.ERROR(f'    ✗ Failed: {str(e)}'))

            # Progress indicator
            if i % 50 == 0:
                self.stdout.write(f'  Progress: {i}/{len(equipment_list)} ({int(i/len(equipment_list)*100)}%)')

        # Step 8: Summary
        self.stdout.write('\n' + '='*80)
        self.stdout.write(self.style.SUCCESS('SUMMARY'))
        self.stdout.write('='*80)
        self.stdout.write(f'Total equipment processed: {len(equipment_list)}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN - No schedules were actually created'))
            self.stdout.write(f'Would create: {len(equipment_list)} schedules')
        else:
            self.stdout.write(self.style.SUCCESS(f'Created: {created_count} schedules'))
            if failed_count > 0:
                self.stdout.write(self.style.ERROR(f'Failed: {failed_count} schedules'))

        self.stdout.write('='*80 + '\n')
