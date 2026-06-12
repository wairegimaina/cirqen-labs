from django.apps import AppConfig

class PpmsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ppms'

    def ready(self):
        import ppms.signals