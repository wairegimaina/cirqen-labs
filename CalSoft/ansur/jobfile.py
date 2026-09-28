"""The work order (JobOrder file) Cirqen hands to Ansur.

Layout as in the Ansur Test Executive Users Manual (FBC-0001 Rev. 6, "Work
Orders", p. 4-37): under ``<METRONFile Type="JobOrder">`` sit two siblings,
``<JobOrder>`` (``<Language GUI="" Report="">`` and ``<OutputDir>``) and
``<Setup Template="" ResultFile="" ReadOnly="">`` with ``<ServiceEvents>``
(an ``<Activity Type="" Event=""/>``), ``<Standard AlphaName="">`` and
``<DUT>`` items. ``ReadOnly="True"`` stops the device details, standard and
service event being changed inside Ansur. Written in ISO-8859-1 like the
manual's example; characters outside it become XML character references.
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


# The version in the manual's example work order.
WORK_ORDER_VERSION = "1.3.3"
LANGUAGE = "English"


def _dut_items(job):
    """(Name, Caption, value, key) for each device field, in Ansur's order."""
    equipment = job.equipment
    department = getattr(equipment, "department", None)
    return [
        ("Serial No", "Ser No", equipment.serial_number or "", True),
        ("Manufacturer", "Manufacturer", str(equipment.manufacturer or ""), False),
        ("Model", "Model", equipment.model or "", False),
        ("Type", "Type", str(equipment.description or ""), False),
        ("Location", "Location", getattr(department, "name", "") or "", False),
        ("Cirqen Job", "Cirqen Job", job.job_number, False),
    ]


def build(job, cfg, link) -> bytes:
    folders = setup.folder_paths(cfg.base_folder)
    root = ET.Element("METRONFile", {"Version": WORK_ORDER_VERSION, "Type": "JobOrder"})
    order = ET.SubElement(root, "JobOrder")
    ET.SubElement(order, "Language", {"GUI": LANGUAGE, "Report": LANGUAGE})
    ET.SubElement(order, "OutputDir").text = str(folders["results"])

    setup_el = ET.SubElement(root, "Setup", {
        "Template": str(folders["templates"] / link.template_file),
        "ResultFile": result_file_name(job),
        "ReadOnly": "True",
    })
    events = ET.SubElement(setup_el, "ServiceEvents")
    ET.SubElement(events, "Activity", {"Type": "", "Event": link.service_event or "PM"})
    if link.ansur_standard:
        ET.SubElement(setup_el, "Standard", {"AlphaName": link.ansur_standard, "CompleteName": link.ansur_standard})
    dut = ET.SubElement(setup_el, "DUT")
    for order_no, (name, caption, value, key) in enumerate(_dut_items(job), start=1):
        attrs = {"Name": name, "Ord": str(order_no), "Caption": caption}
        if key:
            attrs["Key"] = "True"
        ET.SubElement(dut, "Item", attrs).text = value
    ET.indent(root)
    return ET.tostring(root, encoding="iso-8859-1", xml_declaration=True)


def write(job, cfg, link) -> Path:
    path = setup.folder_paths(cfg.base_folder)["jobs"] / f"{job.job_number}.xml"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(build(job, cfg, link))
    tmp.replace(path)  # Ansur never sees a half-written work order
    return path
