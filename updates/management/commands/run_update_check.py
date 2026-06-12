"""
updates/management/commands/run_update_check.py
===============================================
Standalone management command — a fallback for running the autonomous
update check without Celery (e.g., via system cron or Windows Task Scheduler).

Usage:
    python manage.py run_update_check

Cron example (every 6 hours):
    0 */6 * * * /path/to/venv/bin/python /path/to/manage.py run_update_check >> /var/log/cirqen_updates.log 2>&1

The command calls the same Celery task function directly, so behaviour is
identical whether triggered by beat or by cron.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check HQ for updates and auto-apply if configured."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Ignore check_interval_hours and force a check right now, "
                "even if one ran recently."
            ),
        )

    def handle(self, *args, **options):
        from updates.models import UpdateSettings
        from updates.tasks import check_and_apply_updates

        if options["force"]:
            # Temporarily zero out last_check so the task won't skip
            us = UpdateSettings.get_settings()
            us.last_check = None
            us.save(update_fields=["last_check"])
            self.stdout.write("Forced check: last_check cleared.")

        self.stdout.write("Running autonomous update check …")

        # Call the task function directly (synchronous, no Celery needed)
        check_and_apply_updates()

        self.stdout.write(self.style.SUCCESS("Update check complete."))
