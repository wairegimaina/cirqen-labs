"""Stock running low: one email to everyone in the part's workshop.

A part is running low at or below its lower limit (Accessories.reorder_level;
0 means no limit). When it gets there, everyone in its workshop is emailed
and the HOD is copied (the hospital's copy rule). The part records when
(``low_stock_alerted_at``, a synced field), so neither this PC nor any other
mails it again until the stock has gone back above the limit, which clears it.

Driven by saves on this PC (work order approved or declined, stock received,
a hand edit, a new lower limit). Rows that arrive through sync are written
with SQL, fire no signal, and so are never mailed twice.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone

from parts_tools.models import Accessories

from .mailer import queue
from .recipients import workshop_staff

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Accessories, dispatch_uid="notifications.stock_level")
def stock_changed(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    low = instance.active_status and not instance.pending_delete and instance.is_running_low
    if low and instance.low_stock_alerted_at is None:
        pk = instance.pk
        transaction.on_commit(lambda: _safe(pk))
    elif not low:
        # Back above the limit (or no limit any more): the next time it runs
        # low is news again. Checked in the database, not on this instance,
        # which may have been loaded before the alert went out.
        Accessories.objects.filter(pk=instance.pk, low_stock_alerted_at__isnull=False).update(
            low_stock_alerted_at=None, updated_at=timezone.now(), needs_sync=True)
        instance.low_stock_alerted_at = None


def _safe(pk):
    try:
        alert(pk)
    except Exception:  # mail must never break the save that triggered it
        logger.exception("notifications: stock alert failed for accessory %s", pk)


def alert(pk):
    """Mail the part's workshop if it is running low and not yet mailed. Returns the outbox row or None."""
    now = timezone.now()
    with transaction.atomic():
        part = (Accessories.objects.select_for_update(of=("self",))
                .select_related("name", "workshop", "equipment_description", "supplier").filter(pk=pk).first())
        if part is None or part.low_stock_alerted_at is not None or not part.is_running_low or not part.workshop:
            return None
        # Claim it first: the update below does not fire this signal again.
        Accessories.objects.filter(pk=pk).update(low_stock_alerted_at=now, updated_at=now, needs_sync=True)

    name = part.name.name if part.name else "A part"
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    suggested = max(part.reorder_level * 2 - part.stock_count, part.reorder_level)
    from parts_tools.models import AccessoryRequest

    # assets.signals raises a restock request for the HOD when a part runs low.
    restock = (AccessoryRequest.objects.filter(existing_accessory=part, request_type="restock",
                                               status__in=["Pending", "Approved"]).order_by("-requested_at").first())
    context = {
        "restock": restock,
        "part": part, "name": name, "workshop": part.workshop.name, "state": part.stock_label,
        "equipment": part.equipment_description.name if part.equipment_description_id else "",
        "supplier": part.supplier, "suggested": suggested,
        "link": f"{base}{reverse('partstools:accessories_dashboard')}?stock=low",
    }
    return queue(
        kind="stock_low", dedupe_key=f"stock-low:{part.pk}:{now.isoformat()}",
        to_users=workshop_staff(part.workshop),
        subject=(f"[Stock] {name} is {'out of stock' if part.stock_count <= 0 else 'running low'}: "
                 f"{part.stock_count} left ({part.workshop.name})"),
        template="stock_low", context=context,
        in_app_message=f"{name}: {part.stock_count} left, lower limit {part.reorder_level}.",
    )
