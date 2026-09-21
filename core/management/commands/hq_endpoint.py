"""Read, test and change where this machine looks for HQ, without the UI.

    python manage.py hq_endpoint --show
    python manage.py hq_endpoint --test
    python manage.py hq_endpoint --set sync.api_url=https://hq.example.com/api/sync
    python manage.py hq_endpoint --reset sync.api_url

Same logic as the HQ Connection settings page (core/hq_settings.py), for
headless machines, scripted rollouts and support calls.
"""
from django.core.management.base import BaseCommand, CommandError

from config import HQ_ENDPOINT_DEFAULTS
from core import hq_settings


class Command(BaseCommand):
    help = "Show, test or change this machine's HQ addresses."

    def add_arguments(self, parser):
        parser.add_argument("--show", action="store_true",
                            help="Print each address and where it came from (the default).")
        parser.add_argument("--test", action="store_true",
                            help="Check that the configured servers answer.")
        parser.add_argument("--set", metavar="KEY=VALUE", action="append", default=[],
                            help="Pin a setting on this machine. Repeatable.")
        parser.add_argument("--reset", metavar="KEY", action="append", default=[],
                            help="Follow the shipped default again. Repeatable; 'all' for every key.")

    def handle(self, *args, **options):
        cfg = hq_settings.load_config()

        if options["set"] or options["reset"]:
            cfg = self._change(cfg, options["set"], options["reset"])

        if options["test"]:
            self._test(cfg)
        if options["show"] or not (options["test"] or options["set"] or options["reset"]):
            self._show(cfg)

    # ── actions ──────────────────────────────────────────────────────────────

    def _show(self, cfg):
        rows = hq_settings.describe(cfg)
        width = max(len(row["key"]) for row in rows)
        self.stdout.write(self.style.MIGRATE_HEADING("HQ addresses for this machine"))
        for row in rows:
            source = row["source"]
            painter = self.style.WARNING if source in ("config.json", "provisioning") else (
                self.style.NOTICE if source == "env" else self.style.SUCCESS
            )
            self.stdout.write(f"  {row['key']:<{width}}  {row['value']}  {painter(f'[{source}]')}")
        self.stdout.write("")
        self.stdout.write(f"  config.json: {cfg.config_file}")

        if cfg.get("update.public_key"):
            self.stdout.write(self.style.SUCCESS(
                "  Signing: ON — update packages are verified and HQ address "
                "changes can be followed."
            ))
        else:
            self.stdout.write(self.style.ERROR(
                "  Signing: OFF — packages are applied WITHOUT verification and HQ "
                "address changes are refused. Generate a key with "
                "`python hq_server/build_package.py --genkeys`."
            ))
        remote = hq_settings.remote_state(cfg)
        if remote.get("adopted"):
            self.stdout.write("")
            self.stdout.write(f"  Adopted from the update server at {remote.get('adopted_at')}:")
            for key, value in remote["adopted"].items():
                self.stdout.write(f"    {key} = {value}")
            if remote.get("failures_since_adopt"):
                self.stdout.write(self.style.WARNING(
                    f"    {remote['failures_since_adopt']} consecutive health failure(s) since"
                ))
            if remote.get("reverted_from"):
                self.stdout.write(self.style.WARNING(
                    f"    reverted automatically from {remote['reverted_from']}"
                ))

        pinned = [r["key"] for r in rows if r["source"] == "config.json"]
        if pinned:
            self.stdout.write(self.style.WARNING(
                "  Pinned on this machine, so a release that moves HQ will not move it: "
                + ", ".join(pinned)
            ))
        else:
            self.stdout.write("  Nothing is pinned: this machine follows the shipped defaults.")

    def _test(self, cfg):
        self.stdout.write(self.style.MIGRATE_HEADING("Testing the configured servers"))
        failures = 0
        for name, result in hq_settings.probe_all(cfg).items():
            if result["ok"]:
                self.stdout.write(f"  {self.style.SUCCESS('ok')}     {name}: {result['detail']}")
            else:
                failures += 1
                self.stdout.write(f"  {self.style.ERROR('failed')} {name}: {result['detail']}")
        if failures:
            raise CommandError(f"{failures} of 3 checks failed")

    def _change(self, cfg, assignments, resets):
        submitted = {}
        for assignment in assignments:
            if "=" not in assignment:
                raise CommandError(f"--set expects KEY=VALUE, got {assignment!r}")
            key, value = assignment.split("=", 1)
            key = key.strip()
            if key not in HQ_ENDPOINT_DEFAULTS:
                raise CommandError(f"unknown setting {key!r}; known: {', '.join(HQ_ENDPOINT_DEFAULTS)}")
            submitted[key] = value.strip()

        for key in resets:
            key = key.strip()
            if key == "all":
                submitted.update({k: "" for k in HQ_ENDPOINT_DEFAULTS})
                continue
            if key not in HQ_ENDPOINT_DEFAULTS:
                raise CommandError(f"unknown setting {key!r}; known: {', '.join(HQ_ENDPOINT_DEFAULTS)}")
            submitted[key] = ""  # blank means "use the shipped default"

        locked = [k for k in submitted if cfg.endpoint_sources.get(k) == "env"]
        if locked:
            raise CommandError(
                "set by the environment, which wins over config.json: "
                + ", ".join(f"{k} ({hq_settings.HQ_ENDPOINT_ENV[k]})" for k in locked)
            )

        changed, errors = hq_settings.apply_changes(cfg, submitted)
        if errors:
            for error in errors:
                self.stderr.write(self.style.ERROR(f"  {error}"))
            raise CommandError("nothing was changed")

        if changed:
            for key in changed:
                self.stdout.write(self.style.SUCCESS(f"  set {key} = {cfg.get(key)}"))
            self.stdout.write(self.style.WARNING(
                "  Restart the application for the sync agent and update checker to use this."
            ))
        else:
            self.stdout.write("  No change — the settings already say that.")
        return hq_settings.load_config()
