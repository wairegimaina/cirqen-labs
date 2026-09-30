"""Ansur v2: procedures built from Ansur templates, protected records, the
session page, sessions seen from another PC, colleagues finishing a job, and
database defaults for rows written without the Ansur columns."""
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase
from django.urls import reverse

from CalSoft.ansur import template
from CalSoft.ansur.importer import import_record
from CalSoft.ansur.parser import PROTECTED, RecordError, parse_bytes
from CalSoft.ansur.testing import ANALYSER_SERIAL, AnsurFixture, record_xml
from CalSoft.models import AnsurJob, AnsurTemplateMap, CalibrationProcedure, CalibrationSession
from CalSoft.view_modules.ansur import ansur_available
from core.testing import requires_postgres
from workshop.models import Workshop

SETTINGS = reverse("calibration:ansur_settings")
ACTION = reverse("calibration:ansur_job_action")

# A defibrillator template shaped like the records in CalSoft.ansur.testing:
# the same steps, with their expected results and no measured values.
TEMPLATE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<METRONFile Type="Template" Version="3.1.4">'
    '<Setup><DUT><Item Name="Serial No" Key="True"/></DUT></Setup>'
    '<PlugInData><Tests>'
    '<Test Name="Visual inspection"/>'
    '<Test Name="Energy 360 J" Unit="J" Nominal="360" TolerancePercent="10"/>'
    '<Test Name="Earth leakage" Unit="uA" HighLimit="500"/>'
    '</Tests></PlugInData>'
    '</METRONFile>'
).encode()


class TemplateReaderTests(TestCase):
    def test_steps_with_limits_become_parameters(self):
        found = template.read_template(TEMPLATE)
        self.assertEqual([(p.name, p.unit, p.limit_type) for p in found],
                         [("Energy 360 J", "J", "two_sided"), ("Earth leakage", "uA", "upper")])
        self.assertEqual(found[0].set_values, [D("360")])
        self.assertEqual(found[0].tolerance, D("36"))
        self.assertEqual(found[1].set_values, [D("500")])

    def test_one_step_at_several_points_gives_several_set_values(self):
        data = TEMPLATE.replace(
            b'<Test Name="Earth leakage"',
            b'<Test Name="Energy" Unit="J" Low="45" High="55"/><Test Name="Energy" Unit="J" Low="95" High="105"/>'
            b'<Test Name="Earth leakage"')
        energy = next(p for p in template.read_template(data) if p.name == "Energy")
        self.assertEqual(energy.set_values, [D("50"), D("100")])

    def test_one_name_with_two_tolerances_becomes_two_named_parameters(self):
        data = TEMPLATE.replace(
            b'<Test Name="Earth leakage"',
            b'<Test Name="Energy" Low="45" High="55"/><Test Name="Energy" Low="90" High="110"/>'
            b'<Test Name="Earth leakage"')
        names = [(p.name, p.step) for p in template.read_template(data) if p.step == "Energy"]
        self.assertEqual(names, [("Energy (50)", "Energy"), ("Energy (100)", "Energy")])

    def test_a_template_with_no_limits_is_refused(self):
        data = b'<METRONFile Type="Template"><PlugInData><Test Name="Visual inspection"/></PlugInData></METRONFile>'
        with self.assertRaisesMessage(RecordError, "No test steps with limits"):
            template.read_template(data)

    def test_a_record_is_not_a_template(self):
        with self.assertRaisesMessage(RecordError, "not a test template"):
            template.read_template(record_xml(job="CQ-1"))


class ProcedureFromTemplateTests(AnsurFixture, TestCase):
    def setUp(self):
        self.make_ansur_site()
        # The fixture's own procedure is not needed here; drop its link so
        # the template is offered as unlinked.
        AnsurTemplateMap.objects.all().delete()
        (self.base / "templates" / "Defibrillator.mtt").write_bytes(TEMPLATE)
        self.client.force_login(self.hod)

    def _create(self, **extra):
        return self.client.post(SETTINGS, {"action": "from_template", "template_file": "Defibrillator.mtt",
                                           "procedure_name": "Defibrillator from Ansur",
                                           "standard": self.standard.pk, **extra}, follow=True)

    def test_the_page_offers_unlinked_templates(self):
        self.assertContains(self.client.get(SETTINGS), 'value="Defibrillator.mtt"')

    def test_creating_builds_the_procedure_and_links_it(self):
        response = self._create(service_event="PM", ansur_standard="IEC 60601-2-4")
        self.assertContains(response, "with 2 parameter(s)")
        procedure = CalibrationProcedure.objects.get(name="Defibrillator from Ansur")
        link = procedure.ansur_template
        self.assertEqual((link.template_file, link.ansur_standard), ("Defibrillator.mtt", "IEC 60601-2-4"))
        energy, leakage = procedure.parameters.order_by("order")
        self.assertEqual((energy.ansur_step, energy.limit_type, energy.tolerance), ("Energy 360 J", "two_sided", D("36")))
        self.assertEqual((leakage.ansur_step, leakage.limit_type, leakage.tolerance), ("Earth leakage", "upper", None))
        self.assertEqual(energy.standard_reference, ANALYSER_SERIAL)
        self.assertEqual([sv.value for sv in energy.set_values.all()], [D("360")])

    def test_it_stays_off_until_accuracy_and_uncertainty_are_entered(self):
        self._create()
        procedure = CalibrationProcedure.objects.get(name="Defibrillator from Ansur")
        self.assertNotIn(str(procedure.pk), ansur_available())
        page = self.client.get(SETTINGS)
        self.assertContains(page, "has no reference uncertainty")

        data = {"action": "save_parameters", "map_id": procedure.ansur_template.pk}
        for p in procedure.parameters.all():
            data.update({f"p{p.pk}-ansur_step": p.ansur_step, f"p{p.pk}-limit_type": p.limit_type,
                         f"p{p.pk}-analyser_accuracy_pct": "1", f"p{p.pk}-analyser_accuracy_floor": "0.1",
                         f"p{p.pk}-analyser_resolution": "0.1", f"p{p.pk}-reference_uncertainty": "1.5"})
        self.client.post(SETTINGS, data)
        self.assertIn(str(procedure.pk), ansur_available())

    def test_a_record_from_that_template_imports(self):
        self._create()
        procedure = CalibrationProcedure.objects.get(name="Defibrillator from Ansur")
        for p in procedure.parameters.all():
            p.analyser_accuracy_pct, p.analyser_accuracy_floor = D("1"), D("0.1")
            p.analyser_resolution, p.reference_uncertainty = D("0.1"), D("1.5")
            p.save()
        job = self.make_job(procedure=procedure)
        session = import_record(job, record_xml(job=job.job_number))
        self.assertEqual(session.source, "ansur")
        self.assertEqual(session.readings.count(), 2)
        self.assertTrue(session.overall_pass)

    def test_a_name_already_in_use_is_refused(self):
        self._create()
        response = self._create()
        self.assertContains(response, "already exists")
        self.assertEqual(CalibrationProcedure.objects.filter(name="Defibrillator from Ansur").count(), 1)

    def test_only_files_in_the_templates_folder(self):
        response = self._create(template_file="..\\..\\secrets.mtt")
        self.assertContains(response, "Choose a template from the templates folder")


class ProtectedRecordTests(TestCase):
    def test_a_protected_record_says_which_ansur_settings_to_change(self):
        with self.assertRaisesMessage(RecordError, "Disable Electronic Signature"):
            parse_bytes(b"\x00\x13\x9a\xffENCRYPTED")
        self.assertIn("Restrict Access", PROTECTED)


class SessionPagesTests(AnsurFixture, TestCase):
    def setUp(self):
        self.make_ansur_site()
        self.job = self.make_job()
        self.session = import_record(self.job, record_xml(job=self.job.job_number), pdf=b"%PDF-1.4 ansur")
        self.client.force_login(self.hod)

    def test_the_session_keeps_the_job_number(self):
        self.assertEqual(self.session.ansur_job_number, self.job.job_number)

    def test_the_session_page_shows_the_ansur_details(self):
        page = self.client.get(reverse("calibration:session_detail", args=[self.session.pk]))
        self.assertContains(page, "Measured with Fluke Ansur")
        self.assertContains(page, self.job.job_number)
        self.assertContains(page, "Open Ansur's PDF")
        self.assertContains(page, "Checks recorded in Ansur")

    def test_on_another_pc_the_job_number_stays_and_the_pdf_is_explained(self):
        # Another PC has the synced session but not the Ansur PC's job.
        AnsurJob.objects.filter(pk=self.job.pk).delete()
        page = self.client.get(reverse("calibration:session_detail", args=[self.session.pk]))
        self.assertContains(page, self.job.job_number)
        self.assertContains(page, "kept on the Ansur PC")


class ColleagueTests(AnsurFixture, TestCase):
    def setUp(self):
        self.make_ansur_site()
        self.job = self.make_job()

    def test_calibration_centre_staff_can_cancel_a_colleagues_job(self):
        centre = Workshop.objects.create(name="Calibration", category="calibration_center")
        colleague = self._make_user("ansur_colleague", "Tech", level="Engineer", workshop=centre)
        self.client.force_login(colleague)
        response = self.client.post(ACTION, {"job": self.job.pk, "action": "cancel"})
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_maintenance_technician_still_cannot(self):
        other = self._make_user("ansur_other", "Tech", level="Engineer", workshop=self.workshop)
        self.client.force_login(other)
        self.assertEqual(self.client.post(ACTION, {"job": self.job.pk, "action": "cancel"}).status_code, 403)


@requires_postgres
class DatabaseDefaultTests(AnsurFixture, TestCase):
    """A row written without the Ansur columns (an older build, or a row from
    HQ before its migration) must still insert."""

    def setUp(self):
        self.make_ansur_site()

    def test_a_session_inserted_without_ansur_columns(self):
        session = CalibrationSession.objects.create(procedure=self.procedure, performed_by=self.tech)
        table = CalibrationSession._meta.db_table
        columns = [f.column for f in CalibrationSession._meta.concrete_fields
                   if not f.column.startswith("ansur_") and f.column != "source"]
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in columns)} FROM "{table}" WHERE id = %s',
                           [session.pk])
            row = cursor.fetchone()
            cursor.execute(f'DELETE FROM "{table}" WHERE id = %s', [session.pk])
            cursor.execute(
                f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in columns)}) '
                f'VALUES ({", ".join(["%s"] * len(columns))})', row)
        session = CalibrationSession.objects.get(pk=session.pk)
        self.assertEqual((session.source, session.ansur_checks, session.ansur_disagreements, session.ansur_job_number),
                         ("manual", [], 0, ""))


class PlainNumberTests(AnsurFixture, TestCase):
    def setUp(self):
        self.make_ansur_site()

    def test_refusals_print_numbers_as_people_write_them(self):
        from CalSoft.ansur.importer import ImportRefused
        job = self.make_job()
        for data, expected in [
            (record_xml(job=job.job_number).replace(b'Nominal="360"', b'Nominal="300"'),
             "Ansur's value 300 is not a set value"),
            (record_xml(job=job.job_number, tolerance_pct="5"),
             "Energy at 360: Ansur's tolerance ±18 differs from the procedure's ±36."),
        ]:
            with self.assertRaises(ImportRefused) as refused:
                import_record(job, data)
            message = " ".join(refused.exception.reasons)
            self.assertIn(expected, message)
            self.assertNotIn("E+", message)
