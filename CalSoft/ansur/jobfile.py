"""The work order (JobOrder file) Cirqen hands to Ansur.

Layout from the Ansur manual: ``<METRONFile Type="JobOrder">`` holding a
``JobOrder`` with ``Language``, ``OutputDir`` and a ``Setup`` that names the
template, the result file and the device (DUT) fields. ``ReadOnly="True"``
stops the serial number being changed inside Ansur.
"""
from __future__ import annotations

import secrets
import xml.etree.ElementTree as ET
from pathlib import Path

from django.utils import timezone

from . import setup


def new_job_number():
    return f"CQ-{timezone.localdate():%y%m%d}-{secrets.token_hex(2).upper()}"


def result_file_name(job):
    return f"CIRQEN-{job.job_number}.mtr"


def _dut_items(job):
    equipment = job.equipment
    department = getattr(equipment, "department", None)
    return [
        ("Serial No", equipment.serial_number or "", True),
        ("Manufacturer", str(equipment.manufacturer or ""), False),
        ("Model", equipment.model or "", False),
        ("Type", str(equipment.description or ""), False),
        ("Location", getattr(department, "name", "") or "", False),
        ("Cirqen Job", job.job_number, False),
    ]


def build(job, cfg, link) -> bytes:
    folders = setup.folder_paths(cfg.base_folder)
    root = ET.Element("METRONFile", {"Type": "JobOrder", "Version": "1"})
    order = ET.SubElement(root, "JobOrder")
    ET.SubElement(order, "Language").text = "English"
    ET.SubElement(order, "OutputDir").text = str(folders["results"])
    setup_el = ET.SubElement(order, "Setup", {
        "Template": str(folders["templates"] / link.template_file),
        "ResultFile": result_file_name(job),
        "ReadOnly": "True",
    })
    dut = ET.SubElement(setup_el, "DUT")
    for name, value, key in _dut_items(job):
        attrs = {"Name": name}
        if key:
            attrs["Key"] = "True"
        ET.SubElement(dut, "Item", attrs).text = value
    ET.SubElement(setup_el, "ServiceEvents").text = link.service_event or "PM"
    if link.ansur_standard:
        ET.SubElement(setup_el, "Standard", {"AlphaName": link.ansur_standard})
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write(job, cfg, link) -> Path:
    path = setup.folder_paths(cfg.base_folder)["jobs"] / f"{job.job_number}.xml"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(build(job, cfg, link))
    tmp.replace(path)  # Ansur never sees a half-written work order
    return path
