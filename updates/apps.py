from django.apps import AppConfig


class UpdatesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "updates"
    verbose_name = "Application Updates"

    def ready(self):
        """
        Called once when Django starts.
        Registers the Celery beat schedule dynamically so you don't have to
        hard-code it in settings.py (though you can — see tasks.py header).
        """
        self._register_beat_schedule()

    # ── private ───────────────────────────────────────────────────────────────

    def _register_beat_schedule(self):
        """
        Add the autonomous update-check task to Celery beat's schedule.

        The schedule reads check_interval_hours from UpdateSettings at runtime,
        defaulting to 6 hours if the DB isn't ready yet (e.g. first migration).

        The task itself re-checks the interval before doing any work, so even if
        beat fires more often than expected, no duplicate checks happen.
        """
        try:
            from celery import current_app
            from django.conf import settings

            # Read desired interval from UPDATE_SYSTEM config (fallback: 6 h)
            cfg            = getattr(settings, "UPDATE_SYSTEM", {})
            interval_hours = cfg.get("check_interval_hours", 6)

            schedule_key = "updates.autonomous_check"

            # Only register if Celery is actually configured
            if not getattr(settings, "CELERY_BROKER_URL", None):
                return

            from celery.schedules import timedelta as celery_td

            current_app.conf.beat_schedule.setdefault(
                schedule_key,
                {
                    "task":     "updates.tasks.check_and_apply_updates",
                    "schedule": celery_td(hours=interval_hours),
                    "options":  {"expires": 3600},  # drop if not picked up in 1 h
                },
            )

        except Exception:
            # Never crash Django startup over this
            pass
