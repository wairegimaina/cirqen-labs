"""Background work for the calibration module (Celery, see Equiper/celery.py)."""
from celery import shared_task


@shared_task
def scan_ansur_results():
    """Import records Ansur has saved into the results folder."""
    from CalSoft.ansur import watcher

    return watcher.scan()


@shared_task
def prune_ansur_archive():
    """Remove archived Ansur records past the retention on the settings page."""
    from CalSoft.ansur import watcher

    return watcher.prune_archive()
