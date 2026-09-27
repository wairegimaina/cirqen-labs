"""Event-driven notifications (daily checks are in assets.tasks).

These fire on saves made through the app. Rows arriving by sync are written
without the ORM, so a notification is raised once, on the machine where the
event happened, and reaches other machines as a synced notification.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse

logger = logging.getLogger(__name__)


@receiver(post_save, sender="parts_tools.Accessories")
def low_stock(sender, instance, **kwargs):
    """At or below the reorder level: one open restock request, and an alert."""
    if not instance.reorder_level or instance.stock_count > instance.reorder_level or not instance.workshop_id:
        return
    from core.notify import notify, workshop_leads
    from parts_tools.models import AccessoryRequest

    try:
        leads = workshop_leads(instance.workshop)
        open_request = AccessoryRequest.objects.filter(
            existing_accessory=instance, request_type="restock", status__in=["Pending", "Approved"]).exists()
        if not open_request and leads and instance.equipment_description_id:
            AccessoryRequest.objects.create(
                request_type="restock", existing_accessory=instance,
                equipment_description=instance.equipment_description,
                requested_quantity=max(instance.reorder_level * 2 - instance.stock_count, 1),
                unit_cost=instance.unit_cost, workshop=instance.workshop, requested_by=leads[0].userprofile,
                note=f"Raised automatically: {instance.stock_count} left, reorder level {instance.reorder_level}.")
        notify(leads, "stock_low", f"Low stock: {instance.name}",
               f"{instance.stock_count} left (reorder level {instance.reorder_level})."
               + (f" Supplier: {instance.supplier}." if instance.supplier_id else ""),
               url=reverse("assets:stock_alerts"))
    except Exception:
        logger.exception("Low-stock handling failed for accessory %s", instance.pk)
