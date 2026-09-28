"""Ansur records into calibration sessions: the parser, the checks that
refuse a record, the session an accepted record makes, and the watcher."""
import os
import time
from decimal import Decimal as D
from unittest import mock

from django.test import SimpleTestCase, TestCase

from CalSoft.ansur import watcher
from CalSoft.ansur.importer import ImportRefused, import_record
from CalSoft.ansur.parser import RecordError, parse_bytes
from CalSoft.ansur.testing import AnsurFixture, record_xml
from CalSoft.models import AnsurJob, CalibrationSession
from CalSoft.utils import INDETERMINATE, PASS, reading_conformity


class ParserTests(SimpleTestCase):
    def test_reads_device_job_instruments_and_steps(self):
        record = parse_bytes(record_xml(job="CQ-1"))
        self.assertEqual(record.serial, "DF-44102")
        self.assertEqual(record.cirqen_job, "CQ-1")
        self.assertEqual(record.overall_status, "Pass")
        self.assertEqual(record.operator, "J. Technician")
        self.assertEqual(record.version, "3.1.4")
        self.assertEqual(record.instruments[0].serial, "IMP-7000-1")
        self.assertEqual([s.name for s in record.steps], ["Energy 360 J", "Earth leakage"])
        self.assertEqual(record.checks, [("Visual inspection", "Pass")])

    def test_limits(self):
        energy, leakage = parse_bytes(record_xml()).steps
        self.assertEqual(energy.value, D("352.4"))
        self.assertEqual(energy.limit(), ("two_sided", D("360"), D("36.0")))
        self.assertEqual(leakage.limit(), ("upper", D("500"), None))

    def test_child_element_layout_and_decimal_comma(self):
        xml = (b'<METRONFile Type="Record"><Setup><DUT><Item Name="Serial No" Key="True">X1</Item></DUT></Setup>'
               b'<PlugInData><Step><Name>Insulation</Name><MeasuredValue>2,5 MOhm</MeasuredValue>'
               b'<LowLimit>2</LowLimit><Result>Passed</Result></Step></PlugInData></METRONFile>')
        (step,) = parse_bytes(xml).steps
        self.assertEqual((step.name, step.value, step.status), ("Insulation", D("2.5"), "Pass"))
        self.assertEqual(step.limit(), ("lower", D("2"), None))

    def test_high_and_low_pair_is_two_sided(self):
        xml = (b'<METRONFile Type="Record"><PlugInData><T Name="Rate" Value="61" LowLimit="58" HighLimit="62"/>'
               b'</PlugInData></METRONFile>')
        self.assertEqual(parse_bytes(xml).steps[0].limit(), ("two_sided", D("60"), D("2")))

    def test_refuses_what_is_not_a_record(self):
        for data, reason in [
            (b"not xml", "not readable XML"),
            (b'<METRONFile Type="Template"/>', "not a test record"),
            (b"<Other/>", "root element"),
            (b'<!DOCTYPE x [<!ENTITY a "b">]><METRONFile Type="Record"/>', "document type"),
        ]:
            with self.assertRaisesMessage(RecordError, reason):
                parse_bytes(data)


class ImportTests(AnsurFixture, TestCase):
    def setUp(self):
        self.make_ansur_site()
        self.job = self.make_job()

    def _import(self, **record):
        return import_record(self.job, record_xml(job=self.job.job_number, **record))

    def _refused(self, **record):
        with self.assertRaises(ImportRefused) as ctx:
            self._import(**record)
        self.assertFalse(CalibrationSession.objects.exists())
        return " ".join(ctx.exception.reasons)

    def test_accepted_record_makes_an_ansur_session(self):
        session = self._import()
        self.assertEqual(session.source, "ansur")
        self.assertEqual(session.status, "pending_review")
        self.assertEqual(session.performed_by, self.tech)
        self.assertEqual(session.device_serial, "DF-44102")
        self.assertEqual(session.actual_temperature, D("23.0"))
        self.assertEqual(session.ansur_operator, "J. Technician")
        self.assertEqual(len(session.ansur_record_sha256), 64)
        self.assertTrue(session.overall_pass)
        self.assertIn("Measured with Fluke Ansur", session.notes)
        self.assertIn("Visual inspection: Pass", session.notes)

        energy = session.readings.get(parameter=self.energy)
        self.assertEqual(energy.get_readings_list(), [D("352.4")])
        self.assertEqual(energy.expanded_uncertainty, D("4.302848"))
        self.assertEqual(energy.error, D("7.600000"))
        self.assertEqual(energy.ansur_status, "Pass")
        self.assertEqual(reading_conformity(energy), PASS)
        leakage = session.readings.get(parameter=self.leakage)
        self.assertEqual(leakage.expanded_uncertainty, D("3.213389"))
        self.assertEqual(reading_conformity(leakage), PASS)

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, AnsurJob.IMPORTED)
        self.assertEqual(self.job.session, session)
        self.assertEqual(self.job.record_sha256, session.ansur_record_sha256)
        self.assertIsNotNone(session.schedule)
        self.assertEqual(session.schedule.status, "pending_approval")

    def test_recalculating_keeps_the_single_reading_budget(self):
        energy = self._import().readings.get(parameter=self.energy)
        energy.calculate_statistics()
        energy.refresh_from_db()
        self.assertEqual(energy.expanded_uncertainty, D("4.302848"))

    def test_verdict_disagreement_is_counted(self):
        # 497 uA against a 500 uA limit with U = 3.2: Ansur says Pass, Cirqen cannot decide.
        session = self._import(leakage="497")
        self.assertEqual(reading_conformity(session.readings.get(parameter=self.leakage)), INDETERMINATE)
        self.assertEqual(session.ansur_disagreements, 1)

    def test_failed_check_fails_the_session(self):
        self.assertFalse(self._import(visual="Fail").overall_pass)

    def test_out_of_limit_fails_the_session(self):
        self.assertFalse(self._import(leakage="620", leakage_status="Fail").overall_pass)

    def test_refusals_name_the_reason(self):
        cases = [
            (dict(serial="OTHER-1"), "Serial mismatch"),
            (dict(status="Aborted"), "aborted"),
            (dict(template="Infusion.mtt"), "Wrong template"),
            (dict(instruments=(("NOPE-1", "Unknown"),)), "not in the standards register"),
            (dict(tolerance_pct="5"), "differs from the procedure"),
            (dict(energy=None), "no step named"),
            (dict(energy_status="Not performed"), "not performed"),
        ]
        for record, reason in cases:
            with self.subTest(reason=reason):
                self.assertIn(reason, self._refused(**record))

    def test_wrong_job_number_is_refused(self):
        with self.assertRaises(ImportRefused) as ctx:
            import_record(self.job, record_xml(job="CQ-000000-0000"))
        self.assertIn("belongs to job CQ-000000-0000", ctx.exception.reasons[0])

    def test_overdue_analyser_is_refused(self):
        from datetime import timedelta
        from django.utils import timezone
        self.standard.calibration_due_date = timezone.localdate() - timedelta(days=1)
        self.standard.save()
        self.assertIn("past its calibration due date", self._refused())

    def test_unlinked_parameter_is_refused(self):
        self.leakage.ansur_step = ""
        self.leakage.save()
        self.assertIn("not linked to an Ansur test step", self._refused())

    def test_a_job_imports_once(self):
        self._import()
        with self.assertRaises(ImportRefused):
            import_record(self.job, record_xml(job=self.job.job_number))


class WatcherTests(AnsurFixture, TestCase):
    def setUp(self):
        self.make_ansur_site()
        self.job = self.make_job()
        self.results = self.base / "results"
        pdf = mock.patch("CalSoft.ansur.launcher.make_pdf", return_value=None)
        pdf.start()
        self.addCleanup(pdf.stop)

    def _drop(self, name, data, age=10):
        path = self.results / name
        path.write_bytes(data)
        past = time.time() - age
        os.utime(path, (past, past))
        return path

    def test_imports_and_archives_a_saved_record(self):
        self._drop(f"CIRQEN-{self.job.job_number}.mtr", record_xml(job=self.job.job_number))
        self.assertEqual(watcher.scan(), [f"imported {self.job.job_number}"])
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, AnsurJob.IMPORTED)
        self.assertFalse(list(self.results.glob("*.mtr")))
        self.assertEqual(len(list((self.base / "archive").rglob("*.mtr"))), 1)
        self.assertIn("PDF could not be produced", self.job.warning)

    def test_keeps_the_ansur_pdf(self):
        pdf_path = self.base / "results" / "made.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 ansur")
        with mock.patch("CalSoft.ansur.launcher.make_pdf", return_value=pdf_path):
            self._drop("CIRQEN-x.mtr", record_xml(job=self.job.job_number))
            watcher.scan()
        self.job.refresh_from_db()
        self.assertTrue(self.job.pdf_copy.name.endswith(".pdf"))
        self.assertEqual(len(self.job.pdf_sha256), 64)

    def test_leaves_a_file_that_is_still_being_written(self):
        self._drop("CIRQEN-x.mtr", record_xml(job=self.job.job_number), age=0)
        self.assertEqual(watcher.scan(), [])
        self.assertTrue((self.results / "CIRQEN-x.mtr").exists())

    def test_refused_record_goes_to_quarantine_with_its_reason(self):
        self._drop("CIRQEN-x.mtr", record_xml(job=self.job.job_number, serial="WRONG"))
        watcher.scan()
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, AnsurJob.REJECTED)
        self.assertIn("Serial mismatch", self.job.error)
        reason = next((self.base / "quarantine").glob("*.reason.txt")).read_text()
        self.assertIn("Serial mismatch", reason)

    def test_a_refused_job_can_import_a_corrected_record(self):
        self._drop("a.mtr", record_xml(job=self.job.job_number, serial="WRONG"))
        watcher.scan()
        self._drop("b.mtr", record_xml(job=self.job.job_number))
        watcher.scan()
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, AnsurJob.IMPORTED)

    def test_record_with_no_job_is_quarantined(self):
        self._drop("stray.mtr", record_xml(job=""))
        self.assertTrue(watcher.scan()[0].startswith("quarantined"))
        self.assertTrue(list((self.base / "quarantine").glob("stray*.mtr")))

    def test_does_nothing_when_switched_off(self):
        self.cfg.enabled = False
        self.cfg.save()
        self._drop("CIRQEN-x.mtr", record_xml(job=self.job.job_number))
        self.assertEqual(watcher.scan(), [])

    def test_prunes_old_archive_years(self):
        old = self.base / "archive" / "2001"
        old.mkdir(parents=True)
        (old / "a.mtr").write_text("x")
        self.assertEqual(watcher.prune_archive(), 1)
        self.assertFalse(old.exists())


class AnsurHistoryAndChecksTests(AnsurFixture, TestCase):
    def setUp(self):
        self.make_ansur_site()

    def test_checks_are_kept_as_data(self):
        job = self.make_job()
        session = import_record(job, record_xml(job=job.job_number, visual="Fail"))
        self.assertEqual(session.ansur_checks, [{"name": "Visual inspection", "status": "Fail"}])

    def test_ansur_results_feed_the_drift_history(self):
        from CalSoft.models import HistoricalCalibration
        from CalSoft.utils import DriftAnalyzer

        first = self.make_job(job_number="CQ-250101-0001")
        session = import_record(first, record_xml(job=first.job_number, energy="352.4"))
        CalibrationSession.objects.filter(pk=session.pk).update(
            timestamp=session.timestamp.replace(year=session.timestamp.year - 1))
        HistoricalCalibration.objects.filter(device_serial="DF-44102").update(
            calibration_date=session.timestamp.replace(year=session.timestamp.year - 1))
        second = self.make_job(job_number="CQ-260101-0002")
        import_record(second, record_xml(job=second.job_number, energy="356.0"))

        rows = HistoricalCalibration.objects.filter(device_serial="DF-44102", parameter_name="Energy")
        self.assertEqual(sorted(r.measured_value for r in rows), [D("352.4"), D("356.0")])
        drift = DriftAnalyzer.analyze_drift("DF-44102")
        self.assertIn(360.0, drift["Energy"])
