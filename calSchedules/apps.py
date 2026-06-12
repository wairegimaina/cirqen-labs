from django.apps import AppConfig

class CalschedulesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'calSchedules'

    def ready(self):
        # Import instant reconciliation to activate signals
        import calSchedules.instant_reconciliation
