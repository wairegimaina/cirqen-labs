"""Build a Cirqen calibration procedure from an Ansur test template (.mtt).

A template is the same ``<METRONFile>`` XML as a record, without results: each
test element names its step and, where it has one, its expected result (a
high/low limit, or a nominal value with a tolerance; Users Manual 4-14, 4-29).
Every step with a limit becomes a parameter already linked to that step, with
its set value, tolerance and how it is judged, so the procedure is not typed
twice. Steps without a limit (checklists, messages) stay Ansur's Pass/Fail
checks. What a template cannot say (the analyser's accuracy and the
reference uncertainty from its certificate) is left for the Ansur connection
page, which keeps Start with Ansur off until they are filled in.

Like the record reader, steps are found by shape because each plug-in lays
out its test elements differently; a template whose layout gives no limits is
refused rather than guessed at.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction

from CalSoft.models import AnsurTemplateMap, CalibrationParameter, CalibrationProcedure, SetValue

from .parser import (
    HIGH_KEYS, LOW_KEYS, MAX_RECORD_BYTES, NAME_KEYS, NOMINAL_KEYS, TOL_KEYS, TOL_PCT_KEYS, UNIT_KEYS, VALUE_KEYS,
    PROTECTED, RecordError, Step, _fields, _local, _pick, _plain, parse_number,
)


@dataclass
class TemplateParameter:
    name: str
    unit: str
    limit_type: str
    tolerance: Decimal | None
    set_values: list = field(default_factory=list)
    step: str = ""  # the step name exactly as Ansur prints it

    def __post_init__(self):
        self.step = self.step or self.name


def _limit_step(element):
    """The step this element describes, if it names one and carries a limit."""
    fields = _fields(element)
    name = _pick(fields, NAME_KEYS)
    if not name:
        return None
    step = Step(
        name=name.strip(), value=parse_number(_pick(fields, VALUE_KEYS)),
        unit=(_pick(fields, UNIT_KEYS) or "").strip(),
        low=parse_number(_pick(fields, LOW_KEYS)), high=parse_number(_pick(fields, HIGH_KEYS)),
        nominal=parse_number(_pick(fields, NOMINAL_KEYS)),
        tolerance=parse_number(_pick(fields, TOL_KEYS)),
        tolerance_pct=parse_number(_pick(fields, TOL_PCT_KEYS)),
    )
    return step if step.limit() is not None else None


def read_template(data: bytes) -> list[TemplateParameter]:
    """The parameters a template defines, in template order. Raises RecordError."""
    if len(data) > MAX_RECORD_BYTES:
        raise RecordError("The file is too large to be an Ansur template.")
    if b"<!doctype" in data[:4096].lower() or b"<!entity" in data.lower():
        raise RecordError("The file contains a document type declaration, which Ansur templates do not.")
    if not data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        raise RecordError(PROTECTED.replace("an Ansur record", "an Ansur template", 1))
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise RecordError(f"The template is not readable XML ({exc}).")
    if _local(root.tag) != "METRONFile":
        raise RecordError("Not an Ansur file: the root element is not METRONFile.")
    if root.attrib.get("Type", "").lower() not in ("template", ""):
        raise RecordError(f"This is an Ansur {root.attrib['Type']}, not a test template (.mtt).")

    setup = {e for s in root.iter() if _local(s.tag) == "Setup" for e in s.iter()}
    parameters: dict[tuple, TemplateParameter] = {}
    claimed = set()
    for element in root.iter():
        if element in setup or element in claimed:
            continue
        step = _limit_step(element)
        if step is None:
            continue
        claimed.update(element.iter())
        limit_type, set_point, tolerance = step.limit()
        # One parameter per step name and way of judging it; the same step at
        # several points (e.g. energy at 50 J and 360 J under one name) gives
        # several set values, provided the tolerance is the same.
        key = (step.name.lower(), limit_type, tolerance)
        parameter = parameters.get(key)
        if parameter is None:
            parameter = parameters[key] = TemplateParameter(step.name, step.unit, limit_type, tolerance)
        if set_point not in parameter.set_values:
            parameter.set_values.append(set_point)
    if not parameters:
        raise RecordError("No test steps with limits were found in this template, so Cirqen cannot build a "
                          "procedure from it. Link it to a procedure by hand instead.")

    # A step name used with two different tolerances becomes two parameters;
    # name them apart so each can be told from the other.
    names = [p.name.lower() for p in parameters.values()]
    for parameter in parameters.values():
        if names.count(parameter.name.lower()) > 1:
            parameter.name = f"{parameter.name} ({_plain(parameter.set_values[0])})"
    return list(parameters.values())


def create_procedure(template_file, data, *, name, user, standard=None, service_event="PM", ansur_standard=""):
    """Create the procedure, its parameters and set values, and link it to the
    template. Returns (procedure, parameters). Raises RecordError."""
    found = read_template(data)
    with transaction.atomic():
        procedure = CalibrationProcedure.objects.create(
            name=name[:200], created_by=user,
            description=f"Created from the Fluke Ansur template {template_file}.")
        for order, item in enumerate(found, start=1):
            parameter = CalibrationParameter.objects.create(
                procedure=procedure, name=item.name[:100], unit=item.unit[:20], order=order,
                limit_type=item.limit_type,
                tolerance=item.tolerance if item.limit_type == "two_sided" else None,
                ansur_step=item.step[:150],
                standard_reference=getattr(standard, "serial_number", "") or "",
                # Unknown until the analyser's certificate is entered; the
                # Ansur connection page asks for it before switching on.
                reference_uncertainty=Decimal("0"),
            )
            for sv_order, value in enumerate(item.set_values):
                SetValue.objects.create(parameter=parameter, value=value, order=sv_order)
        AnsurTemplateMap.objects.create(
            procedure=procedure, template_file=template_file, service_event=service_event or "PM",
            ansur_standard=ansur_standard or "")
    return procedure, found
