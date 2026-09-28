"""Pick up the records Ansur saves into the results folder.

Called every few seconds by the background worker, and also each time the
Perform Calibration page asks for a job's status, so a result is picked up
even when the worker is not running.

A record is taken only once it has stopped changing, and is claimed by
moving it into ``results/.processing`` first. On Windows that move fails
while Ansur still has the file open, and when two scans race only one move
succeeds, so a record is never read half-written or imported twice.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from django.utils import timezone

from CalSoft.models import AnsurJob, AnsurSettings

from . import launcher, setup
from .importer import ImportRefused, import_record, plan_import, refuse
from .parser import RecordError, parse_bytes

logger = logging.getLogger(__name__)

STABLE_SECONDS = 2
_JOB_IN_NAME = re.compile(r"(CQ-\d{6}-[0-9A-F]{4})", re.IGNORECASE)


def _move(path: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    if target.exists():
        target = folder / f"{path.stem}-{int(time.time())}{path.suffix}"
    os.replace(path, target)
    return target


def _quarantine(path: Path, folders, reasons) -> None:
    moved = _move(path, folders["quarantine"])
    moved.with_suffix(".reason.txt").write_text("\n".join(reasons) + "\n", encoding="utf-8")


def _find_job(record, name):
    number = record.cirqen_job if record is not None else ""
    if not number:
        match = _JOB_IN_NAME.search(name)
        number = match.group(1) if match else ""
    if not number:
        return None
    return AnsurJob.objects.filter(job_number__iexact=number).select_related(
        "equipment", "procedure", "created_by").first()


def process(path: Path, cfg) -> str:
    """Import one claimed record; returns what happened, for the log."""
    folders = setup.folder_paths(cfg.base_folder)
    data = path.read_bytes()
    try:
        record = parse_bytes(data)
        parse_error = None
    except RecordError as exc:
        record, parse_error = None, str(exc)

    job = _find_job(record, path.name)
    if job is None or not job.is_open:
        why = ("No open Cirqen job matches this record."
               if job is None else f"Job {job.job_number} is {job.get_status_display().lower()}.")
        _quarantine(path, folders, [why] + ([parse_error] if parse_error else []))
        return "quarantined: " + why

    if parse_error:
        refuse(job, [parse_error])
        _quarantine(path, folders, [parse_error])
        return "refused: " + parse_error

    try:
        plan_import(job, record)
    except ImportRefused as exc:
        refuse(job, exc.reasons)
        _quarantine(path, folders, exc.reasons)
        return "refused: " + "; ".join(exc.reasons)

    pdf_path = launcher.make_pdf(cfg, path)
    pdf = pdf_path.read_bytes() if pdf_path else None
    try:
        import_record(job, data, pdf=pdf, file_name=path.name)
    except ImportRefused as exc:
        refuse(job, exc.reasons)
        _quarantine(path, folders, exc.reasons)
        return "refused: " + "; ".join(exc.reasons)

    archive = folders["archive"] / f"{timezone.localdate():%Y}"
    _move(path, archive)
    if pdf_path and pdf_path.exists():
        _move(pdf_path, archive)
    if cfg.delete_job_files and job.job_file:
        Path(job.job_file).unlink(missing_ok=True)
    return f"imported {job.job_number}"


def scan(cfg=None) -> list[str]:
    cfg = cfg or AnsurSettings.load()
    if not cfg.enabled:
        return []
    folders = setup.folder_paths(cfg.base_folder)
    results = folders["results"]
    if not results.is_dir():
        return []
    claimed_dir = results / ".processing"
    outcomes = []
    now = time.time()
    for path in sorted(results.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".mtr":
            continue
        try:
            if now - path.stat().st_mtime < STABLE_SECONDS:
                continue  # still being written
            claimed = _move(path, claimed_dir)
        except OSError:
            continue  # Ansur still has it open, or another scan claimed it
        try:
            outcomes.append(process(claimed, cfg))
        except Exception:
            logger.exception("Ansur record %s could not be processed", claimed.name)
            try:
                _quarantine(claimed, folders, ["Unexpected error while importing; see the Cirqen log."])
            except OSError:
                pass
            outcomes.append(f"error: {claimed.name}")
    for outcome in outcomes:
        logger.info("Ansur: %s", outcome)
    return outcomes


def prune_archive(cfg=None, today=None) -> int:
    """Delete archived records older than the retention on the settings page.
    Cirqen keeps its own copy of every imported record (AnsurJob.record_copy)."""
    cfg = cfg or AnsurSettings.load()
    archive = setup.folder_paths(cfg.base_folder)["archive"]
    if not archive.is_dir():
        return 0
    year = (today or timezone.localdate()).year
    removed = 0
    for folder in archive.iterdir():
        if folder.is_dir() and folder.name.isdigit() and int(folder.name) < year - cfg.archive_years:
            for file in folder.iterdir():
                file.unlink(missing_ok=True)
                removed += 1
            folder.rmdir()
    return removed
