"""Show or set which hospital this PC belongs to.

    python manage.py hospital_code             # show it
    python manage.py hospital_code CH0001      # set it (checks HQ first)
    python manage.py hospital_code --clear     # back to no hospital code

With a hospital code, this PC syncs only with an HQ that proves it is that
hospital's (hq_handshake.py). So the code is saved only after the HQ this PC
uses today passes that check; otherwise the PC would go offline the moment
the sync agent restarts. --force skips the check (for a PC being prepared
before its HQ is ready). New installs get the code from provisioning.json.
"""
from django.core.management.base import BaseCommand, CommandError

import hq_handshake
from core import hq_settings

KEY = "sync.hospital_code"


class Command(BaseCommand):
    help = "Show or set this PC's hospital code."

    def add_arguments(self, parser):
        parser.add_argument("code", nargs="?", help="e.g. CH0001")
        parser.add_argument("--clear", action="store_true", help="Remove the hospital code.")
        parser.add_argument("--force", action="store_true",
                            help="Save without checking that this PC's HQ proves the hospital.")

    def handle(self, *args, code=None, clear=False, force=False, **options):
        cfg = hq_settings.load_config()
        current = cfg.get(KEY) or ""
        sync_url = (cfg.get("sync.api_url") or "").rstrip("/")

        if clear:
            cfg.set(KEY, "")
            self.stdout.write(self.style.SUCCESS("Hospital code removed."))
            self._restart_note()
            return
        if not code:
            self.stdout.write(f"Hospital code: {current or '(none)'}")
            self.stdout.write(f"HQ address:    {sync_url}")
            if current:
                ok, why = hq_handshake.confirm(sync_url, current)
                self.stdout.write(self.style.SUCCESS("HQ check:      passed") if ok
                                  else self.style.ERROR(f"HQ check:      FAILED, {why}"))
            return

        code = hq_handshake.normalise_code(code)
        if not force:
            ok, why = hq_handshake.confirm(sync_url, code)
            if not ok:
                raise CommandError(
                    f"Not saved: the HQ at {sync_url} did not prove it is hospital {code} ({why}). "
                    "Set up that HQ's identity first (hq_server/hq_certificates.py), or use --force."
                )
            self.stdout.write(self.style.SUCCESS(f"HQ at {sync_url} confirmed as hospital {code}."))
        cfg.set(KEY, code)
        self.stdout.write(self.style.SUCCESS(f"Hospital code set to {code} in {cfg.config_file}."))
        self._restart_note()

    def _restart_note(self):
        self.stdout.write("Restart the sync agent (or the app) for it to take effect.")
