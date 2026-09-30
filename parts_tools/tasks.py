"""Background work for parts and accessories."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="parts_tools.tasks.reconcile_stock", ignore_result=True)
def reconcile_stock():
    """Every minute: give parts their opening balance (the site sender PC
    only, so there is one writer), then set every part's count on this PC to
    the total of its stock movements, including those synced from other PCs."""
    from notifications.mailer import is_site_sender

    from . import stock

    openings = stock.create_missing_openings() if is_site_sender() else 0
    corrected = stock.reconcile()
    if openings or corrected:
        logger.info("Stock ledger: %d opening balance(s), %d count(s) corrected", openings, len(corrected))
    return openings, len(corrected)
