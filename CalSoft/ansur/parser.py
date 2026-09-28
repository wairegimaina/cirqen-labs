"""Read a Fluke Ansur test record (.mtr) into plain data.

What the Ansur manual documents, and this reads exactly:

* the root ``<METRONFile Type="Record" Version="...">``;
* ``Setup/DUT`` items (``<Item Name="Serial No" Key="True">...``), including
  custom fields such as the ``Cirqen Job`` field Cirqen puts in each template;
* ``Setup/Standard``, ``Setup/TestInstruments`` and the overall status
  (Pass, Fail, NA, Aborted, Not performed).

What it does not document is how each plug-in lays out its measurements
inside ``PlugInData``. This module therefore reads test steps by *shape*
rather than by one fixed layout: an element that carries a name and a
measured value, with its limits and status, whether they are attributes or
child elements, and whatever the exact tag spelling. A layout it does not
recognise yields no steps, and the importer refuses the record ("no measured
results found") rather than guessing. Real records collected at the site
(plan, Phase 0) become test fixtures, and any plug-in whose layout differs
gets a small adapter here.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAX_RECORD_BYTES = 50 * 1024 * 1024

# Spellings seen across Ansur plug-ins and their exports, compared lower-case
# with separators removed.
NAME_KEYS = ("name", "description", "caption", "testname", "stepname", "step", "title")
VALUE_KEYS = ("measuredvalue", "measured", "value", "reading", "actualvalue", "actual", "resultvalue")
UNIT_KEYS = ("unit", "units", "uom")
STATUS_KEYS = ("status", "teststatus", "result", "verdict", "passfail")
LOW_KEYS = ("lowlimit", "lowerlimit", "low", "min", "minimum", "minlimit")
HIGH_KEYS = ("highlimit", "upperlimit", "high", "max", "maximum", "maxlimit")
NOMINAL_KEYS = ("nominal", "preset", "expected", "expectedvalue", "setvalue", "target", "x")
TOL_KEYS = ("tolerance", "tol", "y")
TOL_PCT_KEYS = ("tolerancepercent", "tolpercent", "percent", "tolerancepct", "ypercent")

STATUSES = {
    "pass": "Pass", "passed": "Pass", "ok": "Pass",
    "fail": "Fail", "failed": "Fail",
    "na": "NA", "n/a": "NA", "notapplicable": "NA",
    "aborted": "Aborted", "abort": "Aborted",
    "notperformed": "Not performed", "notrun": "Not performed", "skipped": "Not performed",
}

_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")


class RecordError(ValueError):
    """The file is not an Ansur record this module can read."""


@dataclass
class Step:
    name: str
    value: Decimal | None
    unit: str = ""
    status: str = ""
    low: Decimal | None = None
    high: Decimal | None = None
    nominal: Decimal | None = None
    tolerance: Decimal | None = None
    tolerance_pct: Decimal | None = None

    def limit(self):
        """``(limit_type, set_value, tolerance)`` Cirqen judges this step by.

        Dynamic limits (X + Y, X + X*Y%) and a high/low pair become a set
        value with a +/- tolerance; a lone high or low limit becomes an upper
        or lower limit. ``None`` when the step carries no limit at all.
        """
        if self.nominal is not None and (self.tolerance is not None or self.tolerance_pct is not None):
            tol = self.tolerance if self.tolerance is not None else abs(self.nominal) * self.tolerance_pct / 100
            return ("two_sided", self.nominal, abs(tol))
        if self.low is not None and self.high is not None:
            return ("two_sided", (self.low + self.high) / 2, abs(self.high - self.low) / 2)
        if self.high is not None:
            return ("upper", self.high, None)
        if self.low is not None:
            return ("lower", self.low, None)
        return None


@dataclass
class Instrument:
    model: str = ""
    serial: str = ""
    name: str = ""


@dataclass
class Record:
    version: str = ""
    template: str = ""
    dut: dict = field(default_factory=dict)
    standard: str = ""
    operator: str = ""
    overall_status: str = ""
    instruments: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    checks: list = field(default_factory=list)  # (name, status) for steps with no value

    @property
    def serial(self):
        for key, value in self.dut.items():
            if _norm(key) in ("serialno", "serialnumber", "serial", "sn"):
                return value
        return self.dut.get("__key__", "")

    @property
    def cirqen_job(self):
        for key, value in self.dut.items():
            if _norm(key) in ("cirqenjob", "cirqenjobno", "cirqenjobnumber"):
                return value.strip()
        return ""


def _norm(text):
    return re.sub(r"[\s_\-.:]", "", (text or "")).lower()


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_number(text):
    """First number in the text ("352.4 J", "0,25"), or None."""
    if text is None:
        return None
    match = _NUMBER.search(str(text))
    if not match:
        return None
    raw = match.group(0)
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    raw = raw.replace(",", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def normalise_status(text):
    return STATUSES.get(_norm(text).replace("/", ""), STATUSES.get(_norm(text), (text or "").strip()))


def _fields(element):
    """Attributes and simple child elements of ``element``, keyed by normalised name."""
    out = {}
    for key, value in element.attrib.items():
        out.setdefault(_norm(key), value)
    for child in element:
        if len(child) == 0 and child.text and child.text.strip():
            out.setdefault(_norm(_local(child.tag)), child.text.strip())
            name_attr = child.attrib.get("Name") or child.attrib.get("name")
            if name_attr:
                out.setdefault(_norm(name_attr), child.text.strip())
    return out


def _pick(fields, keys):
    for key in keys:
        if key in fields and str(fields[key]).strip() != "":
            return fields[key]
    return None


def _read_dut(dut):
    items = {}
    for item in dut.iter():
        if _local(item.tag).lower() != "item":
            continue
        name = item.attrib.get("Name") or item.attrib.get("name") or ""
        value = (item.text or item.attrib.get("Value") or "").strip()
        if name:
            items[name] = value
        if (item.attrib.get("Key") or "").lower() == "true":
            items["__key__"] = value
    return items


def _read_instruments(element):
    instruments = []
    for child in element:
        fields = _fields(child)
        serial = _pick(fields, ("serialno", "serialnumber", "serial", "sn")) or ""
        model = _pick(fields, ("model", "type", "instrument")) or ""
        name = _pick(fields, ("name", "description")) or (child.text or "").strip()
        if serial or model or name:
            instruments.append(Instrument(model=model, serial=serial.strip(), name=name))
    return instruments


def _read_steps(root, skip=None):
    steps, checks, seen = [], [], set(skip.iter()) if skip is not None else set()
    for element in root.iter():
        if element in seen:
            continue
        fields = _fields(element)
        name = _pick(fields, NAME_KEYS)
        if not name:
            continue
        raw_value = _pick(fields, VALUE_KEYS)
        status = normalise_status(_pick(fields, STATUS_KEYS) or "")
        value = parse_number(raw_value)
        if value is None:
            # A Pass/Fail-only step (visual inspection, alarms), not a measurement.
            if status in ("Pass", "Fail", "NA", "Not performed") and len(element) <= 12:
                checks.append((name.strip(), status))
            continue
        steps.append(Step(
            name=name.strip(), value=value,
            unit=(_pick(fields, UNIT_KEYS) or "").strip(), status=status,
            low=parse_number(_pick(fields, LOW_KEYS)), high=parse_number(_pick(fields, HIGH_KEYS)),
            nominal=parse_number(_pick(fields, NOMINAL_KEYS)),
            tolerance=parse_number(_pick(fields, TOL_KEYS)),
            tolerance_pct=parse_number(_pick(fields, TOL_PCT_KEYS)),
        ))
        seen.update(element.iter())
    return steps, checks


def parse_bytes(data: bytes) -> Record:
    if len(data) > MAX_RECORD_BYTES:
        raise RecordError("The file is too large to be an Ansur test record.")
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise RecordError("The file contains a document type declaration, which Ansur records do not.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise RecordError(f"The file is not readable XML ({exc}). It may be locked by Ansur's Restrict access.")

    if _local(root.tag) != "METRONFile":
        raise RecordError("Not an Ansur file: the root element is not METRONFile.")
    file_type = root.attrib.get("Type", "")
    if file_type.lower() != "record":
        raise RecordError(f"This is an Ansur {file_type or 'file'}, not a test record (.mtr).")

    record = Record(version=root.attrib.get("Version", ""))
    setup = next((e for e in root.iter() if _local(e.tag) == "Setup"), None)
    if setup is not None:
        record.template = setup.attrib.get("Template", "") or ""
        for child in setup:
            tag = _local(child.tag)
            if tag == "DUT":
                record.dut = _read_dut(child)
            elif tag == "Standard":
                record.standard = child.attrib.get("AlphaName") or child.attrib.get("CompleteName") or (child.text or "").strip()
            elif tag == "TestInstruments":
                record.instruments = _read_instruments(child)

    root_fields = _fields(root)
    setup_fields = _fields(setup) if setup is not None else {}
    record.overall_status = normalise_status(
        _pick(root_fields, ("status", "teststatus", "overallstatus", "result"))
        or _pick(setup_fields, ("status", "teststatus", "overallstatus"))
        or "")
    record.operator = (_pick(root_fields, ("operator", "user", "testedby", "technician"))
                       or _pick(setup_fields, ("operator", "user", "testedby", "technician")) or "")

    plugin = next((e for e in root.iter() if _local(e.tag) == "PlugInData"), None)
    if plugin is not None:
        record.steps, record.checks = _read_steps(plugin)
    else:
        record.steps, record.checks = _read_steps(root, skip=setup)
    return record


def parse_file(path) -> Record:
    return parse_bytes(Path(path).read_bytes())
