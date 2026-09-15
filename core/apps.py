from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from django.db.models.signals import post_save

        from core import hq_link

        post_save.connect(hq_link.record_save, dispatch_uid="core.hq_link.record_save")
