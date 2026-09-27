from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "assets"
    verbose_name = "Assets: contracts, KPIs, risk and labels"

    def ready(self):
        from . import signals  # noqa: F401
