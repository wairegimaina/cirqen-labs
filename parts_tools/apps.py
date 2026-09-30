from django.apps import AppConfig


class PartsToolsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'parts_tools'

    def ready(self):
        from . import stock  # noqa: F401  (opening balance for parts created on this PC)

