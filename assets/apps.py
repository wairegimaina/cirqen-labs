from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "assets"
    verbose_name = "Assets: KPIs, service contracts, stock and labels"

    def ready(self):
        from . import signals  # noqa: F401
