"""The stock ledger: every change to a part's stock is a StockMovement row.

Why: ``Accessories.stock_count`` syncs last-write-wins, so two PCs that each
take a part while offline end with one of the two deductions lost. Movements
are rows that are only ever added, so both reach every PC, and the count is
their total.

How it fits together:

* ``take``/``put``/``set_count`` change a part here: one movement and the
  matching update to ``stock_count``, in one transaction.
* ``reconcile`` (every minute, every PC) sets ``stock_count`` to the total of
  the movements for each part that has an opening balance. It corrects this
  PC only (``updated_at`` is left alone), so PCs do not bounce counts at each
  other while movements are still arriving.
* ``create_missing_openings`` (the site sender PC only) gives every existing
  part its opening balance: today's count less the movements already known.
  The opening's id comes from the part's id, so it is one row however many
  times or places it is written. A part without an opening keeps working
  exactly as before (its synced count is trusted).
* A part created on this PC gets its opening at once (a post_save signal).
"""
import logging
import uuid

from django.db import transaction
from django.db.models import F, Sum
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Accessories, StockMovement

logger = logging.getLogger(__name__)

# Opening balances get ids derived from the part's id, so the same part never
# has two, whichever PCs write them.
OPENING_NAMESPACE = uuid.UUID("6f1d3a52-9c1e-4b7a-8e0f-2d5c7a9b1e44")


class InsufficientStock(Exception):
    def __init__(self, accessory, wanted):
        self.accessory, self.wanted = accessory, wanted
        super().__init__(f"Insufficient stock for {accessory.name}. "
                         f"Available: {accessory.stock_count}, Required: {wanted}")


def opening_id(accessory_id):
    return uuid.uuid5(OPENING_NAMESPACE, str(accessory_id))


def _fire_signals(accessory_id):
    """Run the stock signals (running-low email, automatic restock request)
    after a database update, which fires none itself."""
    accessory = Accessories.objects.filter(pk=accessory_id).first()
    if accessory is not None:
        post_save.send(sender=Accessories, instance=accessory, created=False, raw=False,
                       using="default", update_fields=frozenset({"stock_count"}))
    return accessory


def _move(accessory_id, change, reason, **links):
    return StockMovement.objects.create(accessory_id=accessory_id, change=change, reason=reason, **links)


def take(accessory_id, quantity, reason=StockMovement.WORK_ORDER, **links):
    """Take ``quantity`` out, only if that much is there. Raises InsufficientStock."""
    with transaction.atomic():
        taken = Accessories.objects.filter(pk=accessory_id, stock_count__gte=quantity).update(
            stock_count=F("stock_count") - quantity, updated_at=timezone.now())
        if not taken:
            raise InsufficientStock(Accessories.objects.get(pk=accessory_id), quantity)
        _move(accessory_id, -quantity, reason, **links)
    return _fire_signals(accessory_id)


def put(accessory_id, quantity, reason=StockMovement.RECEIVED, **links):
    """Put ``quantity`` in."""
    with transaction.atomic():
        Accessories.objects.filter(pk=accessory_id).update(
            stock_count=F("stock_count") + quantity, updated_at=timezone.now())
        _move(accessory_id, quantity, reason, **links)
    return _fire_signals(accessory_id)


def set_count(accessory_id, counted, note="", **links):
    """A stock take: record the difference between what was counted and what
    the system holds, as an adjustment. Returns the movement, or None."""
    with transaction.atomic():
        current = Accessories.objects.select_for_update().values_list("stock_count", flat=True).get(pk=accessory_id)
        difference = counted - current
        if not difference:
            return None
        Accessories.objects.filter(pk=accessory_id).update(stock_count=counted, updated_at=timezone.now())
        movement = _move(accessory_id, difference, StockMovement.ADJUSTMENT, note=note[:200], **links)
    _fire_signals(accessory_id)
    return movement


def ensure_opening(accessory, count=None):
    """Write the part's opening balance (``count``, else its stock less the
    movements already known). Does nothing if it already has one."""
    if StockMovement.objects.filter(pk=opening_id(accessory.pk)).exists():
        return None
    if count is None:
        known = (StockMovement.objects.filter(accessory_id=accessory.pk, active_status=True)
                 .aggregate(t=Sum("change"))["t"] or 0)
        count = accessory.stock_count - known
    movement, created = StockMovement.objects.get_or_create(
        pk=opening_id(accessory.pk),
        defaults={"accessory_id": accessory.pk, "change": count, "reason": StockMovement.OPENING,
                  "note": "Opening balance"})
    return movement if created else None


def create_missing_openings():
    """Opening balances for every part without one. The site sender PC only."""
    created = 0
    have = StockMovement.objects.filter(reason=StockMovement.OPENING).values_list("accessory_id", flat=True)
    for accessory in Accessories.objects.exclude(pk__in=have).only("pk", "stock_count"):
        if ensure_opening(accessory):
            created += 1
    return created


def reconcile():
    """Set each part's count to the total of its movements (parts with an
    opening balance only). Returns the parts corrected."""
    with_opening = StockMovement.objects.filter(reason=StockMovement.OPENING, active_status=True).values(
        "accessory_id")
    totals = dict(StockMovement.objects.filter(accessory_id__in=with_opening, active_status=True)
                  .values_list("accessory_id").annotate(t=Sum("change")).values_list("accessory_id", "t"))
    corrected = []
    for pk, count in Accessories.objects.filter(pk__in=totals).values_list("pk", "stock_count"):
        total = max(totals[pk] or 0, 0)
        if total != count:
            # This PC only: updated_at is left alone so the corrected count is
            # not uploaded over another PC's (each PC works it out itself).
            Accessories.objects.filter(pk=pk).update(stock_count=total)
            corrected.append(pk)
            _fire_signals(pk)
    if corrected:
        logger.info("Stock ledger: corrected %d part count(s) from their movements", len(corrected))
    return corrected


@receiver(post_save, sender=Accessories, dispatch_uid="parts_tools.stock_opening")
def opening_for_new_part(sender, instance, created=False, raw=False, **kwargs):
    """A part created on this PC starts its ledger with what it was created with."""
    if created and not raw:
        try:
            ensure_opening(instance, count=instance.stock_count)
        except Exception:
            logger.exception("Stock ledger: no opening balance for new part %s", instance.pk)
