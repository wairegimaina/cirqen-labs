"""Turn an Ansur test record into a Cirqen calibration session.

The record is checked against the job it claims to belong to before
anything is written: job number, serial, template, test completeness, the
analysers used, and that every parameter of the procedure has exactly one
result per set value with the same limit the procedure states. A record that
fails any check is refused with every reason, and nothing is saved.

Accepted records produce a session like a manual one (source "ansur"), with
one analyser reading per test point, Cirqen's own uncertainty budget and
guard-banded verdict, and Ansur's Pass/Fail kept beside it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from CalSoft.models import (
    AnsurJob, CalibrationParameter, CalibrationReading, CalibrationSession, SessionParameterResolution,
    SetValue, Standard,
)
from CalSoft.utils import PASS, reading_conformity

from .parser import Record, RecordError, parse_bytes

SIX_DP = Decimal("0.000001")


class ImportRefused(Exception):
    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


@dataclass
class Plan:
    """What an accepted record will write: (parameter, set value, step) rows."""
    rows: list = field(default_factory=list)
    failed_checks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _q(value):
    return Decimal(value).quantize(SIX_DP)


def _base_name(path):
    return re.split(r"[\\/]", path or "")[-1].lower()


def _check_identity(job, record, reasons, warnings):
    if not record.cirqen_job:
        reasons.append("The record has no Cirqen Job number. Add the Cirqen Job field to the Ansur template.")
    elif record.cirqen_job.upper() != job.job_number.upper():
        reasons.append(f"The record belongs to job {record.cirqen_job}, not {job.job_number}.")

    serial = (job.equipment.serial_number or "").strip().lower()
    if (record.serial or "").strip().lower() != serial:
        reasons.append(f"Serial mismatch: the record says {record.serial or 'nothing'}, "
                       f"the equipment is {job.equipment.serial_number}.")

    if record.overall_status in ("Aborted", "Not performed"):
        reasons.append(f"The test was {record.overall_status.lower()} in Ansur. Run it to the end, then save.")

    if record.template and _base_name(record.template) != job.template_file.lower():
        reasons.append(f"Wrong template: the record used {_base_name(record.template)}, "
                       f"the procedure is linked to {job.template_file}.")


def _check_instruments(job, record, reasons, warnings):
    today = timezone.localdate()
    if not record.instruments:
        warnings.append("The record lists no test instruments, so the analyser could not be checked "
                        "against the standards register.")
    for instrument in record.instruments:
        if not instrument.serial:
            continue
        standard = Standard.objects.filter(serial_number__iexact=instrument.serial, active_status=True).first()
        label = f"{instrument.model or instrument.name or 'Analyser'} S/N {instrument.serial}"
        if standard is None:
            reasons.append(f"{label} is not in the standards register.")
        elif standard.calibration_due_date and standard.calibration_due_date < today:
            reasons.append(f"{label} was past its calibration due date "
                           f"({standard.calibration_due_date:%d %b %Y}).")

    serials = {p.standard_reference for p in job.procedure.parameters.all() if p.standard_reference}
    for standard in Standard.objects.filter(serial_number__in=serials):
        if standard.calibration_due_date and standard.calibration_due_date < today:
            reasons.append(f"Reference standard {standard.name} (S/N {standard.serial_number}) is past its "
                           f"calibration due date.")


def _match_steps(job, record, reasons):
    rows = []
    parameters = list(CalibrationParameter.objects.filter(
        procedure=job.procedure, active_status=True, pending_delete=False).order_by("order", "name"))
    if not parameters:
        reasons.append("The procedure has no parameters.")
    if not record.steps:
        reasons.append("No measured results were found in the record.")
        return rows

    for parameter in parameters:
        if not parameter.ansur_step:
            reasons.append(f"Parameter {parameter.name} is not linked to an Ansur test step "
                           f"(Ansur connection page).")
            continue
        steps = [s for s in record.steps if _norm(s.name) == _norm(parameter.ansur_step)]
        set_values = list(SetValue.objects.filter(
            parameter=parameter, sub_parameter=None, active_status=True, pending_delete=False))
        if not steps:
            reasons.append(f"No result for {parameter.name}: the record has no step named "
                           f"\"{parameter.ansur_step}\".")
            continue

        covered = {}
        errors_before = len(reasons)
        for step in steps:
            if step.status == "Not performed":
                reasons.append(f"{parameter.name}: the step was not performed.")
                continue
            limit = step.limit()
            if limit is None:
                reasons.append(f"{parameter.name}: the Ansur step has no limit to judge it by.")
                continue
            limit_type, set_point, tolerance = limit
            if limit_type != parameter.limit_type:
                reasons.append(f"{parameter.name}: Ansur judges it as {limit_type.replace('_', '-')}, the "
                               f"procedure as {parameter.limit_type.replace('_', '-')}.")
                continue
            match = next((sv for sv in set_values if _q(sv.value) == _q(set_point)), None)
            if match is None:
                reasons.append(f"{parameter.name}: Ansur's value {set_point.normalize()} is not a set value "
                               f"of the procedure.")
                continue
            if (limit_type == "two_sided" and parameter.tolerance is not None
                    and _q(tolerance) != _q(parameter.tolerance)):
                reasons.append(f"{parameter.name} at {match.value.normalize()}: Ansur's tolerance "
                               f"±{tolerance.normalize()} differs from the procedure's "
                               f"±{parameter.tolerance.normalize()}.")
                continue
            if match.pk in covered:
                reasons.append(f"{parameter.name} at {match.value.normalize()}: more than one result.")
                continue
            covered[match.pk] = step
            rows.append((parameter, match, step))

        for sv in set_values:
            if sv.pk not in covered and len(reasons) == errors_before:
                reasons.append(f"No result for {parameter.name} at {sv.value.normalize()}.")
    return rows


def plan_import(job, record: Record) -> Plan:
    reasons, warnings = [], []
    _check_identity(job, record, reasons, warnings)
    _check_instruments(job, record, reasons, warnings)
    rows = _match_steps(job, record, reasons)
    if reasons:
        raise ImportRefused(reasons)
    failed = [name for name, status in record.checks if status == "Fail"]
    return Plan(rows=rows, failed_checks=failed, warnings=warnings)


def _schedule_for(job):
    from calSchedules.models import CalibrationSchedule
    from CalSoft.view_modules.calibration import _ensure_saved_schedule

    schedule = job.schedule
    if schedule is None or schedule.status == "completed" or not schedule.active_status:
        schedule = (CalibrationSchedule.objects
                    .filter(equipment=job.equipment, active_status=True, pending_delete=False)
                    .exclude(status="completed").order_by("scheduled_month").first())
    if schedule is None:
        # As a manual calibration does: no open schedule, so this one opens it.
        schedule, _ = CalibrationSchedule.objects.get_or_create(
            equipment=job.equipment, scheduled_month=timezone.localdate(),
            defaults={"calibration_procedure": job.procedure, "status": "pending"})
    return _ensure_saved_schedule(schedule, job.equipment, job.procedure)


def _notes(job, record, plan):
    lines = [job.notes.strip()] if job.notes.strip() else []
    lines.append(f"Measured with Fluke Ansur (job {job.job_number}, template {job.template_file}).")
    if record.instruments:
        lines.append("Analyser: " + "; ".join(
            " ".join(x for x in (i.model or i.name, f"S/N {i.serial}" if i.serial else "") if x)
            for i in record.instruments) + ".")
    if record.checks:
        lines.append("Ansur checks: " + "; ".join(f"{n}: {s}" for n, s in record.checks) + ".")
    return "\n".join(lines)


def import_record(job: AnsurJob, data: bytes, *, pdf: bytes | None = None, file_name: str = "") -> CalibrationSession:
    """Check the record and create its session, or raise ImportRefused."""
    from CalSoft.view_modules.calibration import finalise_session

    try:
        record = parse_bytes(data)
    except RecordError as exc:
        raise ImportRefused([str(exc)])

    plan = plan_import(job, record)
    sha = hashlib.sha256(data).hexdigest()

    with transaction.atomic():
        job = AnsurJob.objects.select_for_update().get(pk=job.pk)
        if job.status in (AnsurJob.IMPORTED, AnsurJob.CANCELLED):
            raise ImportRefused([f"Job {job.job_number} is already {job.get_status_display().lower()}."])

        equipment = job.equipment
        schedule = _schedule_for(job)
        session = CalibrationSession.objects.create(
            procedure=job.procedure, schedule=schedule, performed_by=job.created_by,
            timestamp=timezone.now(), device_model=equipment.model,
            device_serial=equipment.serial_number, device_manufacturer=equipment.manufacturer,
            device_description=equipment.description, actual_temperature=job.actual_temperature,
            actual_humidity=job.actual_humidity, actual_pressure=job.actual_pressure,
            notes=_notes(job, record, plan), status="pending_review", source="ansur",
            ansur_operator=record.operator[:150], ansur_record_sha256=sha,
        )

        for parameter in {row[0] for row in plan.rows}:
            SessionParameterResolution.objects.create(
                session=session, parameter=parameter, resolution=parameter.analyser_resolution or Decimal("0"))

        overall_pass = not plan.failed_checks
        disagreements = 0
        for parameter, set_value, step in plan.rows:
            reading = CalibrationReading(
                session=session, parameter=parameter, set_value=set_value,
                reading_1=step.value, ansur_status=step.status[:20])
            reading.save()
            reading.calculate_statistics()
            if not reading.passes_tolerance:
                overall_pass = False
            verdict = reading_conformity(reading)
            if (step.status == "Pass" and verdict != PASS) or (step.status == "Fail" and verdict == PASS):
                disagreements += 1

        session.ansur_disagreements = disagreements
        finalise_session(session, job.procedure, equipment, schedule, job.created_by, overall_pass)

        job.record_copy.save(file_name or f"CIRQEN-{job.job_number}.mtr", ContentFile(data), save=False)
        job.record_sha256 = sha
        if pdf:
            job.pdf_copy.save(f"CIRQEN-{job.job_number}.pdf", ContentFile(pdf), save=False)
            job.pdf_sha256 = hashlib.sha256(pdf).hexdigest()
        job.status = AnsurJob.IMPORTED
        job.session = session
        job.imported_at = timezone.now()
        job.error = ""
        job.warning = "\n".join(plan.warnings + ([] if pdf else ["Ansur's PDF could not be produced."]))
        job.save()
    return session


def refuse(job: AnsurJob, reasons) -> None:
    job.status = AnsurJob.REJECTED
    job.error = "\n".join(reasons)
    job.save(update_fields=["status", "error", "updated_at"])
