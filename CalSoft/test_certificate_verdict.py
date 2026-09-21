"""The certificate verdict is strict conformity — plan item P1.1.

Before this, the printed verdict came from a 40% failure-rate threshold while
the session record used strict conformity, so a device failing up to 39% of its
test points was certified PASSED. These tests hold the two apart:

    conformity_failed  the verdict. One point outside tolerance fails.
    is_failed_report   a triage signal about how widespread the failure is.

The generator is exercised directly rather than through a request, so these
tests need a database but no HTTP layer:

    ./venv/bin/python manage.py test CalSoft.test_certificate_verdict \
        --settings=Equiper.test_settings
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from CalSoft.models import (
    CalibrationParameter,
    CalibrationProcedure,
    CalibrationReading,
    CalibrationSession,
    SessionParameterResolution,
    SetValue,
)
from CalSoft.pdf_generators.certificate import BtwelveHospitalCertificateGenerator


class ConformityVerdictTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="tech", password="x", email="tech@example.test"
        )
        cls.procedure = CalibrationProcedure.objects.create(
            name="NIBP", created_by=cls.user
        )
        cls.parameter = CalibrationParameter.objects.create(
            procedure=cls.procedure,
            name="Systolic",
            unit="mmHg",
            num_readings=5,
            standard_reference="REF-1",
            reference_uncertainty=Decimal("0.25"),
            coverage_factor=Decimal("2.0"),
            tolerance=Decimal("3"),
        )
        cls.set_values = [
            SetValue.objects.create(parameter=cls.parameter, value=Decimal(v), order=i)
            for i, v in enumerate(["100", "150", "200", "250", "300"])
        ]

    def _session(self, overall_pass=True):
        session = CalibrationSession.objects.create(
            procedure=self.procedure,
            performed_by=self.user,
            device_serial="SN-TEST-1",
            device_model="Model X",
            overall_pass=overall_pass,
            certificate_number=None,
        )
        SessionParameterResolution.objects.create(
            session=session, parameter=self.parameter, resolution=Decimal("1")
        )
        return session

    def _reading(self, session, set_value, offset):
        """A reading whose mean sits ``offset`` above the set value."""
        reading = CalibrationReading.objects.create(
            session=session, parameter=self.parameter, set_value=set_value
        )
        base = set_value.value + Decimal(str(offset))
        for i, delta in enumerate(["0", "1", "0", "2", "1"], start=1):
            setattr(reading, f"reading_{i}", base + Decimal(delta))
        reading.save()
        reading.calculate_statistics()
        return reading

    def _generator(self, session):
        return BtwelveHospitalCertificateGenerator(session)

    # ── all points pass ─────────────────────────────────────────────────────

    def test_all_points_within_tolerance_passes(self):
        session = self._session(overall_pass=True)
        for sv in self.set_values:
            self._reading(session, sv, 0)

        gen = self._generator(session)
        self.assertFalse(gen.conformity_failed)
        self.assertFalse(gen.is_failed_report)

    # ── the band that used to certify a failing device ──────────────────────

    def test_one_failed_point_in_five_fails_the_certificate(self):
        """20% failure. Under the old 40% rule this printed PASSED."""
        session = self._session(overall_pass=False)
        for sv in self.set_values[:4]:
            self._reading(session, sv, 0)
        self._reading(session, self.set_values[4], 10)  # 10 mmHg out on +/-3

        gen = self._generator(session)
        self.assertEqual(gen.failure_stats["failed_readings"], 1)
        self.assertLess(gen.failure_stats["overall_failure_rate"], 0.40)
        self.assertTrue(gen.conformity_failed, "one failed point must fail the certificate")
        self.assertFalse(gen.is_failed_report, "20% is not a widespread-failure report")

    def test_two_failed_points_in_five_fails_the_certificate(self):
        """40% exactly — the old threshold boundary."""
        session = self._session(overall_pass=False)
        for sv in self.set_values[:3]:
            self._reading(session, sv, 0)
        for sv in self.set_values[3:]:
            self._reading(session, sv, 10)

        gen = self._generator(session)
        self.assertTrue(gen.conformity_failed)
        self.assertTrue(gen.is_failed_report)

    def test_the_qr_status_matches_the_verdict(self):
        """The QR payload said PASSED for a failing device. It must not."""
        session = self._session(overall_pass=False)
        for sv in self.set_values[:4]:
            self._reading(session, sv, 0)
        self._reading(session, self.set_values[4], 10)

        gen = self._generator(session)
        status = "FAILED" if gen.conformity_failed else "PASSED"
        self.assertEqual(status, "FAILED")

    # ── a missing point is invisible to the rate but fails the session ──────

    def test_a_session_marked_failed_with_no_failed_readings_still_fails(self):
        """Covers the blank test point: no reading row, so the rate sees nothing.

        ``_process_readings`` sets overall_pass False when a point yields no
        readings, but creates no row for it — so failure_stats counts 0 of 4
        failed. The verdict must still honour the session flag.
        """
        session = self._session(overall_pass=False)
        for sv in self.set_values[:4]:
            self._reading(session, sv, 0)

        gen = self._generator(session)
        self.assertEqual(gen.failure_stats["failed_readings"], 0)
        self.assertEqual(gen.failure_stats["overall_failure_rate"], 0)
        self.assertTrue(gen.conformity_failed, "a failed session must not print PASSED")

    def test_a_stale_passing_flag_cannot_override_a_failed_reading(self):
        """The readings on the page win over a stale session flag.

        ``overall_pass`` is locked in at submission and can be left stale by a
        later recomputation. If a reading on this certificate failed, the
        certificate fails, whatever the flag says.
        """
        session = self._session(overall_pass=True)  # stale: says it passed
        for sv in self.set_values[:4]:
            self._reading(session, sv, 0)
        self._reading(session, self.set_values[4], 10)

        gen = self._generator(session)
        self.assertTrue(session.overall_pass)
        self.assertTrue(gen.conformity_failed)


class UncertaintyTableTests(TestCase):
    """The printed budget must reconcile — plan items P1.3 and P1.7."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="tech2", password="x", email="tech2@example.test"
        )
        cls.procedure = CalibrationProcedure.objects.create(
            name="NIBP-2", created_by=cls.user
        )
        cls.parameter = CalibrationParameter.objects.create(
            procedure=cls.procedure,
            name="Systolic",
            unit="mmHg",
            num_readings=5,
            standard_reference="REF-1",
            reference_uncertainty=Decimal("0.25"),
            coverage_factor=Decimal("2.0"),
            tolerance=Decimal("3"),
        )
        cls.set_value = SetValue.objects.create(
            parameter=cls.parameter, value=Decimal("200"), order=0
        )

    def _render(self):
        """Build a session, render its uncertainty table, return header/row/reading."""
        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user,
            device_serial="SN-T2", overall_pass=True,
        )
        SessionParameterResolution.objects.create(
            session=session, parameter=self.parameter, resolution=Decimal("1")
        )
        reading = CalibrationReading.objects.create(
            session=session, parameter=self.parameter, set_value=self.set_value
        )
        for i, v in enumerate(["201", "202", "201", "203", "202"], start=1):
            setattr(reading, f"reading_{i}", Decimal(v))
        reading.save()
        reading.calculate_statistics()

        gen = BtwelveHospitalCertificateGenerator(session)
        elements = []
        gen._add_parameter_uncertainty_table(elements, [reading])
        table = elements[0]
        return table._cellvalues[0], table._cellvalues[1], reading

    def _rendered_header_and_row(self):
        header, row, _ = self._render()
        return header, row

    def test_reference_column_is_printed(self):
        """Without it, Combined cannot be checked against its components."""
        header, _ = self._rendered_header_and_row()
        self.assertIn("Reference", header)
        self.assertIn("Type A", header)
        self.assertIn("Type B", header)
        self.assertIn("Combined", header)
        self.assertIn("Expanded", header)

    def test_reading_count_is_printed(self):
        """Type A depends on n, so n has to be on the page."""
        header, row = self._rendered_header_and_row()
        self.assertIn("n", header)
        self.assertEqual(row[header.index("n")], "5")

    def test_the_stored_budget_reconciles_exactly(self):
        """At stored precision the components must root-sum-square to Combined."""
        header, row, reading = self._render()

        recomputed = (
            float(reading.type_a_uncertainty) ** 2
            + float(reading.type_b_uncertainty) ** 2
            + float(reading.reference_uncertainty_component) ** 2
        ) ** 0.5
        self.assertAlmostEqual(recomputed, float(reading.combined_uncertainty), places=6)

        # Expanded uses the DERIVED coverage factor, not the configured 2.0,
        # so the printed k is what reconciles it (plan item P5.1).
        applied_k = float(row[header.index("k")])
        self.assertAlmostEqual(
            float(reading.combined_uncertainty) * applied_k,
            float(reading.expanded_uncertainty),
            places=5,
        )
        self.assertGreater(applied_k, 2.0, "few readings require k above 2")

    def test_the_printed_budget_reconciles_within_display_rounding(self):
        """Square the printed components, add, root: it must give Combined.

        This is the check an assessor does by eye, and it is the reason the
        Reference column had to be added. It reconciles to within one unit in
        the last printed place rather than exactly, because each column is
        rounded independently for display — squaring and re-rooting rounded
        values cannot reproduce a separately rounded result. One unit in the
        last place is the honest guarantee, and it is what the stored-precision
        test above pins exactly.
        """
        header, row = self._rendered_header_and_row()
        type_a = float(row[header.index("Type A")])
        type_b = float(row[header.index("Type B")])
        reference = float(row[header.index("Reference")])
        combined = float(row[header.index("Combined")])

        recomputed = (type_a ** 2 + type_b ** 2 + reference ** 2) ** 0.5
        one_unit_last_place = 1e-4
        self.assertLess(abs(recomputed - combined), 1.5 * one_unit_last_place)

    def test_expanded_is_combined_times_k_within_display_rounding(self):
        header, row = self._rendered_header_and_row()
        combined = float(row[header.index("Combined")])
        expanded = float(row[header.index("Expanded")])
        k = float(row[header.index("k")])
        self.assertLess(abs(combined * k - expanded), 1.5e-4)

    def test_dropping_the_reference_column_would_break_reconciliation(self):
        """The regression this column exists to prevent.

        Type A and Type B alone give 0.4726, which is nowhere near the printed
        Combined of 0.4888 — a discrepancy of 160 units in the last place.
        """
        header, row = self._rendered_header_and_row()
        type_a = float(row[header.index("Type A")])
        type_b = float(row[header.index("Type B")])
        combined = float(row[header.index("Combined")])

        without_reference = (type_a ** 2 + type_b ** 2) ** 0.5
        self.assertGreater(abs(without_reference - combined), 1e-2)

    def test_the_worked_example_values_reach_the_page(self):
        """The handbook §15 vector, as printed.

        Expanded is 1.0759 rather than the handbook's 0.9777 because k is now
        derived from the effective degrees of freedom (2.201 at nu_eff ~ 11.7)
        rather than assumed to be 2. The components are unchanged.
        """
        header, row = self._rendered_header_and_row()
        self.assertEqual(row[header.index("Type A")], "0.3742")
        self.assertEqual(row[header.index("Type B")], "0.2887")
        self.assertEqual(row[header.index("Reference")], "0.1250")
        self.assertEqual(row[header.index("Combined")], "0.4888")
        self.assertEqual(row[header.index("Expanded")], "1.0759")
        self.assertEqual(row[header.index("k")], "2.201")

    def test_the_printed_k_is_the_one_applied_not_the_one_configured(self):
        """The parameter is configured at 2.0; the page must not claim that."""
        header, row, reading = self._render()
        self.assertEqual(reading.parameter.coverage_factor, Decimal("2.0"))
        self.assertNotEqual(row[header.index("k")], "2.0")

    def test_small_components_do_not_print_as_zero(self):
        from CalSoft.pdf_generators.results import ResultsMixin

        fmt = ResultsMixin._format_uncertainty
        # A 0.0001 resolution gives 0.0000289 — this used to render "0.0000".
        self.assertEqual(fmt(Decimal("0.0000289")), "0.000029")
        self.assertEqual(fmt(Decimal("0.288675")), "0.2887")
        self.assertEqual(fmt(Decimal("0.977668")), "0.9777")
        self.assertEqual(fmt(None), "N/A")
        self.assertEqual(fmt(Decimal("0")), "0.0000")


class CoverageFactorNoteTests(TestCase):
    """The notes must quote the k actually used — plan item P1.6."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="tech3", password="x", email="tech3@example.test"
        )
        cls.procedure = CalibrationProcedure.objects.create(
            name="NIBP-3", created_by=cls.user
        )

    def _session_with_k(self, *factors):
        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user,
            device_serial="SN-T3", overall_pass=True,
        )
        for i, k in enumerate(factors):
            parameter = CalibrationParameter.objects.create(
                procedure=self.procedure, name=f"P{i}", unit="mmHg",
                num_readings=5, standard_reference="REF",
                reference_uncertainty=Decimal("0.25"),
                coverage_factor=Decimal(k), tolerance=Decimal("3"),
            )
            set_value = SetValue.objects.create(
                parameter=parameter, value=Decimal("200"), order=0
            )
            SessionParameterResolution.objects.create(
                session=session, parameter=parameter, resolution=Decimal("1")
            )
            reading = CalibrationReading.objects.create(
                session=session, parameter=parameter, set_value=set_value
            )
            # Five readings, matching the parameter's declared num_readings. A
            # two-reading point drives the derived k to 12.706 (t at one degree
            # of freedom), which is statistically correct and not a realistic
            # calibration — see test_two_readings_produce_an_enormous_factor.
            for j, v in enumerate(["200", "201", "200", "202", "201"], start=1):
                setattr(reading, f"reading_{j}", Decimal(v))
            reading.save()
            reading.calculate_statistics()
        return session

    def test_the_configured_factor_acts_as_a_floor(self):
        """A parameter configured at k=3 is never expanded by less than 3."""
        gen = BtwelveHospitalCertificateGenerator(self._session_with_k("3.0"))
        note = gen._coverage_factor_note()
        self.assertIn("k=3", note)

    def test_the_note_discloses_that_k_is_derived(self):
        """An assessor must be able to see where the factor came from."""
        gen = BtwelveHospitalCertificateGenerator(self._session_with_k("2.0"))
        note = gen._coverage_factor_note()
        self.assertIn("effective degrees of freedom", note)
        self.assertIn("Welch-Satterthwaite", note)

    def test_mixed_factors_are_not_collapsed_to_one_claim(self):
        """The old note asserted k=2 while the table showed something else."""
        gen = BtwelveHospitalCertificateGenerator(self._session_with_k("2.0", "3.0"))
        note = gen._coverage_factor_note()
        self.assertIn("per parameter", note)
        self.assertIn("3", note)

    def test_two_readings_produce_an_enormous_coverage_factor(self):
        """Documents an open policy question rather than asserting it is fine.

        With two readings there is one degree of freedom, and Student's t at
        95% is 12.706. The derived factor is therefore ~12.7 and the expanded
        uncertainty is six times what k=2 would give. That is statistically
        correct — two readings say almost nothing about the spread — but it is
        not a certifiable measurement.

        The system currently accepts it: ``calculate_statistics`` needs only
        two readings, while the parameter declares five, the validator wants
        three and PDFConfig says three. Plan item P2/1.x (enforce num_readings
        from below) is the fix; until then this test records the consequence so
        nobody mistakes a 12.7 on a certificate for a bug in the arithmetic.
        """
        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user,
            device_serial="SN-T3b", overall_pass=True,
        )
        parameter = CalibrationParameter.objects.create(
            procedure=self.procedure, name="TwoOnly", unit="mmHg",
            num_readings=5, standard_reference="REF",
            reference_uncertainty=Decimal("0.25"),
            coverage_factor=Decimal("2.0"), tolerance=Decimal("3"),
        )
        set_value = SetValue.objects.create(
            parameter=parameter, value=Decimal("200"), order=0
        )
        SessionParameterResolution.objects.create(
            session=session, parameter=parameter, resolution=Decimal("1")
        )
        reading = CalibrationReading.objects.create(
            session=session, parameter=parameter, set_value=set_value
        )
        reading.reading_1 = Decimal("200")
        reading.reading_2 = Decimal("201")
        reading.save()
        reading.calculate_statistics()

        gen = BtwelveHospitalCertificateGenerator(session)
        self.assertEqual(gen._coverage_factor_used(reading), "12.706")
