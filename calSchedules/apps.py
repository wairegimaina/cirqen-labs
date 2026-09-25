from django.apps import AppConfig


class CalschedulesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'calSchedules'

    def ready(self):
        import calSchedules.signals  # noqa: F401
