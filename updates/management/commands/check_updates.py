from django.core.management.base import BaseCommand
from django.conf import settings as django_settings
from updates.models import UpdateSettings
import requests
import logging

LOG = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for application updates from HQ server'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force check even if not due'
        )

    def handle(self, *args, **options):
        cfg = getattr(django_settings, 'UPDATE_SYSTEM', {})
        server_url = cfg.get('server_url', '').rstrip('/')
        api_key    = cfg.get('api_key', '')
        current    = getattr(django_settings, 'APP_VERSION', '1.0.0')

        if not server_url:
            self.stdout.write(self.style.ERROR('UPDATE_SYSTEM.server_url not set in settings.py'))
            return

        self.stdout.write(f'🔍 Checking for updates (current: v{current})...')

        try:
            resp = requests.get(
                f'{server_url}/api/updates/latest/',
                params={'current_version': current},
                headers={'X-Api-Key': api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Could not reach HQ server: {e}'))
            return

        if data.get('update_available'):
            self.stdout.write(self.style.SUCCESS(
                f'✅ Update available: v{data["version"]}'
            ))
            size_mb = round(data.get('size_bytes', 0) / 1048576, 1)
            self.stdout.write(f'   Size:     {size_mb} MB')
            self.stdout.write(f'   Changes:  {data.get("changes", "")}')
            self.stdout.write(f'   Critical: {data.get("critical", False)}')
            self.stdout.write(f'\nVisit /updates/ in the browser to apply.')
        else:
            self.stdout.write(self.style.SUCCESS('✅ Application is up to date'))
