"""Fixtures shared by the Ansur tests: a defibrillator procedure set up for
Ansur, and records shaped like Ansur's (built, since real site samples are
collected in Phase 0 of the plan and will be added beside these)."""
from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape, quoteattr

from django.contrib.auth import get_user_model
from django.utils import timezone

from calSchedules.models import CalibrationSchedule
from CalSoft.ansur import setup
from CalSoft.models import (
    AnsurJob, AnsurSettings, AnsurTemplateMap, CalibrationParameter, CalibrationProcedure, SetValue, Standard,
)
from Inventory.models import Department, Equipment, EquipmentDescription
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()
ANALYSER_SERIAL = "IMP-7000-1"


def record_xml(*, job="", serial="DF-44102", template="Defibrillator.mtt", status="Pass", energy="352.4",
               energy_status="Pass", leakage="112", leakage_status="Pass", visual="Pass",
               instruments=((ANALYSER_SERIAL, "Impulse 7000DP"),), tolerance_pct="10", extra_steps=""):
    items = "".join(
        f'<Instrument><Model>{escape(model)}</Model><SerialNo>{escape(sn)}</SerialNo></Instrument>'
        for sn, model in instruments)
    energy_step = (f'<Test Name="Energy 360 J" Value={quoteattr(energy)} Unit="J" Status="{energy_status}" '
                   f'Nominal="360" TolerancePercent="{tolerance_pct}"/>' if energy is not None else "")
    leakage_step = (f'<Test Name="Earth leakage" Value={quoteattr(leakage)} Unit="uA" Status="{leakage_status}" '
                    f'HighLimit="500"/>' if leakage is not None else "")
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<METRONFile Type="Record" Version="3.1.4">'
        f'<Status>{status}</Status><Operator>J. Technician</Operator>'
        f'<Setup Template="C:\\CirqenAnsur\\templates\\{template}">'
        '<DUT>'
        f'<Item Name="Serial No" Key="True">{escape(serial)}</Item>'
        '<Item Name="Manufacturer">Zoll</Item><Item Name="Model">R Series</Item>'
        f'<Item Name="Cirqen Job">{escape(job)}</Item>'
        '</DUT>'
        '<Standard AlphaName="IEC 60601-2-4"/>'
        f'<TestInstruments>{items}</TestInstruments>'
        '</Setup>'
        '<PlugInData><Tests>'
        f'<Test Name="Visual inspection" Status="{visual}"/>'
        f'{energy_step}{leakage_step}{extra_steps}'
        '</Tests></PlugInData>'
        '</METRONFile>'
    ).encode("utf-8")


class AnsurFixture:
    """Mix into a TestCase: call ``make_ansur_site()`` in setUp."""

    def make_ansur_site(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        windows = mock.patch.object(setup, "is_windows", return_value=True)
        windows.start()
        self.addCleanup(windows.stop)

        self.workshop = Workshop.objects.create(name="Biomed")
        self.department = Department.objects.create(name="ICU", workshop=self.workshop)
        self.hod = self._make_user("ansur_hod", "HOD")
        self.tech = self._make_user("ansur_tech", "Tech", level="Engineer", workshop=self.workshop)

        self.equipment = Equipment.objects.create(
            description=EquipmentDescription.objects.create(name="Defibrillator"), model="R Series",
            serial_number="DF-44102", department=self.department, workshop=self.workshop, status="Working")
        self.standard = Standard.objects.create(
            name="Defibrillator analyser", model_number="Impulse 7000DP", serial_number=ANALYSER_SERIAL,
            manufacturer="Fluke", certificate_number="CAL-1", calibration_date=timezone.localdate() - timedelta(days=90),
            calibration_due_date=timezone.localdate() + timedelta(days=270), created_by=self.hod)

        self.procedure = CalibrationProcedure.objects.create(name="Defibrillator (Ansur)", created_by=self.hod)
        self.energy = CalibrationParameter.objects.create(
            procedure=self.procedure, name="Energy", unit="J", standard_reference=ANALYSER_SERIAL,
            reference_uncertainty=D("1.0"), coverage_factor=D("2"), tolerance=D("36"), order=1,
            ansur_step="Energy 360 J", analyser_accuracy_pct=D("1"), analyser_accuracy_floor=D("0.1"),
            analyser_resolution=D("0.1"))
        self.energy_sv = SetValue.objects.create(parameter=self.energy, value=D("360"))
        self.leakage = CalibrationParameter.objects.create(
            procedure=self.procedure, name="Earth leakage", unit="uA", standard_reference=ANALYSER_SERIAL,
            reference_uncertainty=D("2"), coverage_factor=D("2"), tolerance=None, order=2, limit_type="upper",
            ansur_step="Earth leakage", analyser_accuracy_pct=D("1"), analyser_accuracy_floor=D("1"),
            analyser_resolution=D("1"))
        self.leakage_sv = SetValue.objects.create(parameter=self.leakage, value=D("500"))

        self.base = self.tmp / "CirqenAnsur"
        self.exe = self.tmp / "Ansur" / "Ansur.exe"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"MZ")
        setup.ensure_folders(str(self.base))
        (self.base / "templates" / "Defibrillator.mtt").write_text("<METRONFile Type=\"Template\"/>")
        self.cfg = AnsurSettings(program_path=str(self.exe), base_folder=str(self.base), enabled=True)
        self.cfg.save()
        self.link = AnsurTemplateMap.objects.create(
            procedure=self.procedure, template_file="Defibrillator.mtt", ansur_standard="IEC 60601-2-4")
        self.schedule = CalibrationSchedule.objects.filter(equipment=self.equipment).first()

    def _make_user(self, username, role, **extra):
        user = User.objects.create_user(username=username, password="pw12345!", first_name=username.title())
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **extra})
        return user

    def make_job(self, **overrides):
        values = dict(job_number="CQ-260927-AB12", equipment=self.equipment, procedure=self.procedure,
                      schedule=self.schedule, template_file="Defibrillator.mtt", created_by=self.tech,
                      status=AnsurJob.SENT, actual_temperature=D("23.0"), actual_humidity=D("50.0"))
        values.update(overrides)
        return AnsurJob.objects.create(**values)
