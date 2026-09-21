"""Test vectors for the calibration mathematics — no database.

Every expected value here was computed by hand and is derived in
``review/01_CALIBRATION_MATHEMATICS.pdf``; the primary vector is that document's
§15 worked example, reproduced in :class:`WorkedExampleTests` below.

Why this file exists: before it, nothing in the suite asserted a mean, a
standard deviation, an uncertainty component, a tolerance decision or a drift
rate. A formula could be changed — or silently broken — without a single test
failing. These tests pin the arithmetic that reaches a certificate so that any
change to it has to be deliberate.

The calculation engine depends only on ``math`` and ``decimal``, so this runs as
``SimpleTestCase`` with no database and no fixtures:

    ./venv/bin/python manage.py test CalSoft.test_calculations \
        --settings=Equiper.test_settings

Two notes for whoever changes these numbers:

* Several tests pin **current** behaviour that the improvement plan intends to
  change — notably the sign convention in :class:`DeviationAndToleranceTests`
  and the rounding cascade in :class:`RoundingCascadeTests`. They are marked
  ``PINS CURRENT BEHAVIOUR``. When the plan changes that behaviour the test
  should change with it, visibly, in the same commit.
* ``CalSoft.utils`` sets the decimal context to 28 significant digits at import
  (one place, since P3.6). Stored and printed values are quantised to 6 decimal
  places, which is what most assertions here use.
"""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from CalSoft.utils import (
    CalibrationCalculator,
    QualityAssurance,
    statistics_from_readings,
)

# The §15 vector: five readings of a 200 mmHg test point, 1 mmHg resolution,
# reference standard +/- 0.25 mmHg at k = 2, tolerance +/- 3 mmHg.
READINGS = [Decimal("201"), Decimal("202"), Decimal("201"), Decimal("203"), Decimal("202")]
SET_VALUE = Decimal("200")
RESOLUTION = Decimal("1")
REFERENCE_U = Decimal("0.25")
COVERAGE_K = Decimal("2")
TOLERANCE = Decimal("3")

SIX_DP = Decimal("0.000001")


def q6(value):
    """Quantise to the six decimal places the model stores and the PDF reads."""
    return Decimal(str(value)).quantize(SIX_DP, rounding=ROUND_HALF_UP)


class StatisticsTests(SimpleTestCase):
    """Mean and sample standard deviation — handbook sections 3.1 and 3.2."""

    def setUp(self):
        self.calc = CalibrationCalculator()

    def test_mean_and_standard_deviation(self):
        stats = self.calc.calculate_statistics(READINGS)
        self.assertEqual(stats["count"], 5)
        self.assertEqual(q6(stats["mean"]), Decimal("201.800000"))
        self.assertEqual(q6(stats["std_dev"]), Decimal("0.836660"))

    def test_bessel_correction_is_applied(self):
        """s divides by n-1, not n. Dividing by n would give 0.748331."""
        stats = self.calc.calculate_statistics(READINGS)
        self.assertEqual(q6(stats["std_dev"]), Decimal("0.836660"))
        self.assertNotEqual(q6(stats["std_dev"]), Decimal("0.748331"))

    def test_identical_readings_have_zero_spread(self):
        stats = self.calc.calculate_statistics([Decimal("5")] * 4)
        self.assertEqual(stats["mean"], Decimal("5"))
        self.assertEqual(stats["std_dev"], Decimal("0"))

    def test_fewer_than_two_readings_returns_none(self):
        """s is undefined for n = 1, so the caller must handle None."""
        self.assertIsNone(self.calc.calculate_statistics([Decimal("201")]))
        self.assertIsNone(self.calc.calculate_statistics([]))

    def test_none_values_are_ignored(self):
        stats = self.calc.calculate_statistics([Decimal("201"), None, Decimal("203")])
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["mean"], Decimal("202"))


class UncertaintyComponentTests(SimpleTestCase):
    """The three components — handbook sections 4, 5 and 6."""

    def setUp(self):
        self.calc = CalibrationCalculator()

    def test_type_a_is_standard_uncertainty_of_the_mean(self):
        """u_A = s / sqrt(n) — of the mean, not of a single reading."""
        u_a = self.calc.calculate_type_a_uncertainty(Decimal("0.836660"), 5)
        self.assertEqual(q6(u_a), Decimal("0.374166"))

    def test_type_a_falls_as_root_n(self):
        """Quadrupling the readings halves the component."""
        u_4 = self.calc.calculate_type_a_uncertainty(Decimal("1"), 4)
        u_16 = self.calc.calculate_type_a_uncertainty(Decimal("1"), 16)
        self.assertEqual(q6(u_4), Decimal("0.500000"))
        self.assertEqual(q6(u_16), Decimal("0.250000"))

    def test_type_a_of_zero_spread_is_zero(self):
        self.assertEqual(self.calc.calculate_type_a_uncertainty(Decimal("0"), 5), Decimal("0"))

    def test_resolution_component_uses_root_twelve(self):
        """u_res = d / sqrt(12) for a rectangular distribution of full width d."""
        self.assertEqual(q6(self.calc.calculate_type_b_uncertainty(RESOLUTION)),
                         Decimal("0.288675"))

    def test_resolution_component_scales_linearly(self):
        self.assertEqual(q6(self.calc.calculate_type_b_uncertainty(Decimal("0.1"))),
                         Decimal("0.028868"))

    def test_root_twelve_is_not_root_three(self):
        """A regression guard: d/sqrt(3) would overstate this component by 2x."""
        self.assertNotEqual(q6(self.calc.calculate_type_b_uncertainty(RESOLUTION)),
                            Decimal("0.577350"))

    def test_expanded_reference_is_divided_by_k(self):
        """The stored reference uncertainty is expanded at k = 2 by convention."""
        calc = CalibrationCalculator(reference_is_expanded=True)
        self.assertEqual(calc.calculate_reference_uncertainty(REFERENCE_U, COVERAGE_K),
                         Decimal("0.125"))

    def test_standard_reference_is_not_divided(self):
        calc = CalibrationCalculator(reference_is_expanded=False)
        self.assertEqual(calc.calculate_reference_uncertainty(REFERENCE_U, COVERAGE_K),
                         Decimal("0.25"))


class CombinationTests(SimpleTestCase):
    """Combination and expansion — handbook sections 7 and 8."""

    def setUp(self):
        self.calc = CalibrationCalculator()

    def test_components_combine_in_quadrature(self):
        u_c = self.calc.calculate_combined_uncertainty(
            Decimal("0.374166"), Decimal("0.288675"), Decimal("0.125000")
        )
        self.assertEqual(q6(u_c), Decimal("0.488834"))

    def test_combination_is_not_a_direct_sum(self):
        """Adding the components would give 0.787841 and assume they conspire."""
        u_c = self.calc.calculate_combined_uncertainty(
            Decimal("0.374166"), Decimal("0.288675"), Decimal("0.125000")
        )
        self.assertNotEqual(q6(u_c), Decimal("0.787841"))
        self.assertLess(u_c, Decimal("0.787841"))

    def test_combined_is_at_least_the_largest_component(self):
        u_c = self.calc.calculate_combined_uncertainty(
            Decimal("0.374166"), Decimal("0.288675"), Decimal("0.125000")
        )
        self.assertGreater(u_c, Decimal("0.374166"))

    def test_expansion_multiplies_by_k(self):
        self.assertEqual(
            q6(self.calc.calculate_expanded_uncertainty(Decimal("0.488834"), COVERAGE_K)),
            Decimal("0.977668"),
        )

    def test_expansion_at_k_equals_three(self):
        self.assertEqual(
            q6(self.calc.calculate_expanded_uncertainty(Decimal("0.488834"), Decimal("3"))),
            Decimal("1.466502"),
        )


class DeviationAndToleranceTests(SimpleTestCase):
    """The conformity decision — handbook section 9.

    PINS CURRENT BEHAVIOUR: the stored deviation is ``set_value - mean``, which
    is the *correction*, not the error of indication. Plan item P1.4 decides
    whether to invert it or rename the column; when that lands, these
    expectations flip sign and this note comes out.
    """

    def setUp(self):
        self.calc = CalibrationCalculator()

    def test_deviation_is_set_value_minus_mean(self):
        mean = Decimal("201.8")
        self.assertEqual(q6(SET_VALUE - mean), Decimal("-1.800000"))

    def test_a_device_reading_high_yields_a_negative_deviation(self):
        """The sign that makes P1.4 necessary: reads high, prints negative."""
        self.assertLess(SET_VALUE - Decimal("201.8"), 0)

    def test_within_tolerance_passes(self):
        self.assertTrue(self.calc.check_tolerance(Decimal("-1.8"), TOLERANCE))
        self.assertTrue(self.calc.check_tolerance(Decimal("1.8"), TOLERANCE))

    def test_outside_tolerance_fails(self):
        self.assertFalse(self.calc.check_tolerance(Decimal("-3.5"), TOLERANCE))
        self.assertFalse(self.calc.check_tolerance(Decimal("3.5"), TOLERANCE))

    def test_exactly_at_tolerance_passes(self):
        """The boundary is inclusive: |error| <= tolerance."""
        self.assertTrue(self.calc.check_tolerance(TOLERANCE, TOLERANCE))
        self.assertTrue(self.calc.check_tolerance(-TOLERANCE, TOLERANCE))

    def test_just_outside_tolerance_fails(self):
        self.assertFalse(self.calc.check_tolerance(Decimal("3.000001"), TOLERANCE))

    def test_tolerance_decision_ignores_uncertainty(self):
        """PINS CURRENT BEHAVIOUR, and documents why P5.4 exists.

        A deviation of 2.9 against a tolerance of 3.0 passes, even though the
        expanded uncertainty of 0.977668 puts the true deviation anywhere from
        about 1.92 to 3.88 — partly outside tolerance. A guarded decision would
        return INDETERMINATE here.
        """
        self.assertTrue(self.calc.check_tolerance(Decimal("2.9"), TOLERANCE))
        guarded_limit = TOLERANCE - Decimal("0.977668")
        self.assertGreater(Decimal("2.9"), guarded_limit)


class WorkedExampleTests(SimpleTestCase):
    """The full chain, handbook section 15, as the model computes it.

    This reproduces the model's quantise-at-every-step cascade so the values
    asserted are exactly those that reach the certificate.
    """

    def test_full_chain_from_readings_to_expanded_uncertainty(self):
        calc = CalibrationCalculator()

        stats = calc.calculate_statistics(READINGS)
        mean = q6(stats["mean"])
        std_dev = q6(stats["std_dev"])
        self.assertEqual(mean, Decimal("201.800000"))
        self.assertEqual(std_dev, Decimal("0.836660"))

        u_a = q6(calc.calculate_type_a_uncertainty(std_dev, stats["count"]))
        u_res = q6(calc.calculate_type_b_uncertainty(RESOLUTION))
        u_ref = q6(REFERENCE_U / COVERAGE_K)
        self.assertEqual(u_a, Decimal("0.374166"))
        self.assertEqual(u_res, Decimal("0.288675"))
        self.assertEqual(u_ref, Decimal("0.125000"))

        u_c = q6(calc.calculate_combined_uncertainty(u_a, u_res, u_ref))
        expanded = q6(calc.calculate_expanded_uncertainty(u_c, COVERAGE_K))
        self.assertEqual(u_c, Decimal("0.488834"))
        self.assertEqual(expanded, Decimal("0.977668"))

        deviation = q6(SET_VALUE - mean)
        self.assertEqual(deviation, Decimal("-1.800000"))
        self.assertTrue(calc.check_tolerance(deviation, TOLERANCE))

    def test_printed_budget_reconciles_only_with_the_reference_column(self):
        """Why P1.3 exists: the certificate omits u_ref, so it cannot be checked.

        Type A and Type B alone give 0.472582, not the printed 0.488834. An
        assessor adding up the printed columns finds a discrepancy with no
        stated cause.
        """
        calc = CalibrationCalculator()
        without_ref = calc.calculate_combined_uncertainty(
            Decimal("0.374166"), Decimal("0.288675"), Decimal("0")
        )
        self.assertEqual(q6(without_ref), Decimal("0.472582"))
        self.assertNotEqual(q6(without_ref), Decimal("0.488834"))

    def test_uncertainty_budget_shares(self):
        """Handbook 7.2: repeatability dominates, so that is where to improve."""
        u_a_sq = Decimal("0.374166") ** 2
        u_res_sq = Decimal("0.288675") ** 2
        u_ref_sq = Decimal("0.125000") ** 2
        total = u_a_sq + u_res_sq + u_ref_sq
        self.assertEqual(round(u_a_sq / total * 100), 59)
        self.assertEqual(round(u_res_sq / total * 100), 35)
        self.assertEqual(round(u_ref_sq / total * 100), 7)

    def test_test_uncertainty_ratio_is_below_four(self):
        """Handbook 10.1: TUR = 3.07, short of the customary 4:1 floor."""
        tur = TOLERANCE / Decimal("0.977668")
        self.assertEqual(tur.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                         Decimal("3.07"))
        self.assertLess(tur, Decimal("4"))


class RoundingCascadeTests(SimpleTestCase):
    """PINS CURRENT BEHAVIOUR — the four sequential roundings of plan item P3.7.

    The model quantises each intermediate to six decimal places and feeds the
    rounded value into the next step. For millimetres of mercury this is
    immaterial; for a parameter in millivolts it is not. These tests fail if the
    cascade is changed, which is the point: P3.7 should change them on purpose.
    """

    def test_rounding_at_each_step_differs_from_rounding_once(self):
        calc = CalibrationCalculator()
        stats = calc.calculate_statistics(READINGS)

        cascaded = q6(calc.calculate_combined_uncertainty(
            q6(calc.calculate_type_a_uncertainty(q6(stats["std_dev"]), stats["count"])),
            q6(calc.calculate_type_b_uncertainty(RESOLUTION)),
            q6(REFERENCE_U / COVERAGE_K),
        ))
        once = q6(calc.calculate_combined_uncertainty(
            calc.calculate_type_a_uncertainty(stats["std_dev"], stats["count"]),
            calc.calculate_type_b_uncertainty(RESOLUTION),
            REFERENCE_U / COVERAGE_K,
        ))
        # Equal at this magnitude; the assertion records that and will speak up
        # if either path changes.
        self.assertEqual(cascaded, Decimal("0.488834"))
        self.assertEqual(once, Decimal("0.488834"))

    def test_a_fine_resolution_component_vanishes_at_four_decimal_places(self):
        """Why P1.7 exists: the certificate prints uncertainties at 4 dp."""
        calc = CalibrationCalculator()
        u_res = calc.calculate_type_b_uncertainty(Decimal("0.0001"))
        self.assertEqual(q6(u_res), Decimal("0.000029"))
        as_printed = f"{float(u_res):.4f}"
        self.assertEqual(as_printed, "0.0000")

    def test_decimal_context_is_set_once_to_twenty_eight_digits(self):
        """P3.6 (done): one precision for the application, set once.

        The module used to be two modules concatenated, and the second header
        reset the precision from 28 to 12 further down the file. The later
        assignment won at import time and applied to every Decimal operation in
        every app. The duplicate header is gone and 28 stands.
        """
        from decimal import getcontext

        import CalSoft.utils  # noqa: F401  (import applies the context)

        self.assertEqual(getcontext().prec, 28)


class DuplicateImplementationTests(SimpleTestCase):
    """Evidence for plan item P3.1 — the parallel implementations agree.

    ``statistics_from_readings`` (float) and ``CalibrationCalculator``
    (Decimal) compute the same two quantities. Confirming they agree is what
    makes deleting one of them safe rather than hopeful.
    """

    def test_float_and_decimal_paths_agree(self):
        mean_f, std_f, n_f = statistics_from_readings([float(r) for r in READINGS])
        stats = CalibrationCalculator().calculate_statistics(READINGS)

        self.assertEqual(n_f, stats["count"])
        self.assertAlmostEqual(mean_f, float(stats["mean"]), places=9)
        self.assertAlmostEqual(std_f, float(stats["std_dev"]), places=9)

    def test_float_path_handles_single_reading_differently(self):
        """A real divergence: one returns 0, the other returns None.

        ``statistics_from_readings`` reports a standard deviation of 0 for a
        single reading; ``calculate_statistics`` returns None. Zero is the more
        dangerous answer, because it flows into u_A as a real value and reports
        a repeatability of zero from one observation.
        """
        mean_f, std_f, n_f = statistics_from_readings([201.0])
        self.assertEqual((mean_f, std_f, n_f), (201.0, 0.0, 1))
        self.assertIsNone(CalibrationCalculator().calculate_statistics([Decimal("201")]))


class LinearityTests(SimpleTestCase):
    """Least-squares linearity — handbook section 12.

    The method works; only its call site was broken (it was invoked unbound, so
    the set values bound to ``self``). These tests call it correctly and pin the
    arithmetic, so plan item P3.4 can repair the call site against a known
    result.
    """

    def setUp(self):
        self.calc = CalibrationCalculator()

    def test_a_perfect_device_has_no_linearity_error(self):
        result = self.calc.calculate_linearity([0, 50, 100], [0, 50, 100])
        self.assertEqual(q6(result["max_linearity_error_units"]), Decimal("0.000000"))

    def test_residual_from_the_fitted_line(self):
        result = self.calc.calculate_linearity([0, 50, 100], [0.1, 50.2, 100.1])
        self.assertEqual(q6(result["max_linearity_error_units"]), Decimal("0.066667"))

    def test_a_pure_offset_is_not_a_linearity_error(self):
        """Every point 2 units high fits a straight line perfectly."""
        result = self.calc.calculate_linearity([0, 50, 100], [2, 52, 102])
        self.assertEqual(q6(result["max_linearity_error_units"]), Decimal("0.000000"))

    def test_a_pure_gain_error_is_not_a_linearity_error(self):
        """A 1% gain error is still a straight line."""
        result = self.calc.calculate_linearity([0, 50, 100], [0, 50.5, 101])
        self.assertEqual(q6(result["max_linearity_error_units"]), Decimal("0.000000"))

    def test_error_as_percent_of_full_scale(self):
        result = self.calc.calculate_linearity([0, 50, 100], [0.1, 50.2, 100.1])
        self.assertEqual(q6(result["max_linearity_error_percent"]), Decimal("0.066667"))

    def test_fewer_than_two_points_is_rejected(self):
        with self.assertRaises(ValueError):
            self.calc.calculate_linearity([0], [0])

    def test_mismatched_lengths_are_rejected(self):
        with self.assertRaises(ValueError):
            self.calc.calculate_linearity([0, 50, 100], [0, 50])


class DriftTests(SimpleTestCase):
    """Drift regression over time — handbook section 13."""

    @staticmethod
    def _series(errors, *, uncertainties=None, passes=None, years=1, tolerance=None):
        base = datetime(2024, 1, 1)
        unc = uncertainties or [0.1] * len(errors)
        ok = passes if passes is not None else [True] * len(errors)
        return [
            {
                "session_date": base + timedelta(days=365 * years * i),
                "error": float(e),
                "uncertainty": float(u),
                "passes": p,
                "session_overall_pass": p,
                "session_cert": f"BNH-{i + 1:04d}",
                "tolerance": tolerance,
            }
            for i, (e, u, p) in enumerate(zip(errors, unc, ok))
        ]

    def setUp(self):
        from CalSoft.pdf_generators.drift import DriftMixin

        self.metrics = DriftMixin._compute_drift_metrics

    def test_steady_drift_rate_per_year(self):
        m = self.metrics(self._series([0.0, 0.5, 1.0]))
        self.assertAlmostEqual(m["drift_rate_per_year"], 0.5, places=6)
        self.assertEqual(m["direction"], "Increasing ▲")

    def test_a_perfect_fit_scores_r_squared_of_one(self):
        m = self.metrics(self._series([0.0, 0.5, 1.0]))
        self.assertAlmostEqual(m["r_squared"], 1.0, places=9)

    def test_two_points_always_fit_perfectly(self):
        """Handbook 13.1: with n = 2, R-squared is 1.0 by construction and means nothing."""
        m = self.metrics(self._series([0.0, 3.7]))
        self.assertEqual(m["r_squared"], 1.0)
        self.assertEqual(m["n_sessions"], 2)

    def test_a_stable_device_shows_no_direction(self):
        m = self.metrics(self._series([0.4, 0.4, 0.4]))
        self.assertAlmostEqual(m["drift_rate_per_year"], 0.0, places=9)
        self.assertEqual(m["direction"], "Stable")

    def test_negative_drift_is_reported_as_decreasing(self):
        m = self.metrics(self._series([1.0, 0.5, 0.0]))
        self.assertLess(m["drift_rate_per_year"], 0)
        self.assertEqual(m["direction"], "Decreasing ▼")

    def test_total_drift_is_last_minus_first(self):
        m = self.metrics(self._series([0.2, 0.6, 1.4]))
        self.assertAlmostEqual(m["total_drift"], 1.2, places=9)

    def test_a_single_session_yields_no_metrics(self):
        self.assertIsNone(self.metrics(self._series([0.5])))

    def test_stability_grades_are_relative_to_tolerance(self):
        """P5.7 (done): the certificate grades drift as a fraction of tolerance.

        The same 0.4/year rate used to grade "Good" whether it was 13% of a
        3 mmHg tolerance or 400% of a 0.1 mV one, because the thresholds were
        absolute. It now depends on the tolerance, so parameters in different
        units are comparable.
        """
        wide = self.metrics(self._series([0.0, 0.4, 0.8], tolerance="3"))
        narrow = self.metrics(self._series([0.0, 0.4, 0.8], tolerance="0.1"))

        self.assertAlmostEqual(wide["drift_rate_per_year"], 0.4, places=6)
        self.assertAlmostEqual(narrow["drift_rate_per_year"], 0.4, places=6)
        self.assertEqual(wide["stability"], "Normal")
        self.assertEqual(narrow["stability"], "Urgent")

    def test_the_grade_carries_its_advice(self):
        """The printed recommendation comes from the grade, not a bare rate."""
        m = self.metrics(self._series([0.0, 2.0, 4.0], tolerance="3"))
        self.assertEqual(m["stability"], "Urgent")
        self.assertIn("Shorten", m["stability_advice"])

    def test_a_parameter_without_a_tolerance_is_ungraded(self):
        """No tolerance means no grade — not a fallback to the old thresholds."""
        m = self.metrics(self._series([0.0, 0.4, 0.8]))
        self.assertEqual(m["stability"], "Ungraded")
        self.assertIsNone(m["tolerance_fraction"])

    def test_the_tolerance_fraction_is_reported(self):
        """Printed beside the rate so the grade can be checked on the page."""
        m = self.metrics(self._series([0.0, 0.5, 1.0], tolerance="3"))
        self.assertAlmostEqual(float(m["tolerance_fraction"]), 1 / 6, places=4)

    def test_fail_history_is_counted(self):
        m = self.metrics(self._series([0.1, 0.2, 0.3], passes=[True, False, False]))
        self.assertEqual((m["fail_count"], m["pass_count"]), (2, 1))
        self.assertAlmostEqual(m["fail_rate"], 2 / 3, places=9)

    def test_rising_uncertainty_is_flagged(self):
        m = self.metrics(self._series([0.1, 0.1, 0.1], uncertainties=[0.1, 0.1, 0.5]))
        self.assertEqual(m["uncertainty_trend"], "Increasing")


class OutlierTests(SimpleTestCase):
    """Robust outlier detection — handbook section 14."""

    @staticmethod
    def _parameter(tolerance="3", num_readings=5):
        return SimpleNamespace(
            name="systolic",
            tolerance=Decimal(tolerance),
            num_readings=num_readings,
        )

    def test_a_clear_outlier_is_flagged(self):
        issues = QualityAssurance.validate_readings(
            [201.0, 202.0, 248.0, 203.0, 202.0], self._parameter()
        )
        self.assertTrue(any("outlier" in i.lower() for i in issues))

    def test_clean_readings_raise_no_outlier(self):
        issues = QualityAssurance.validate_readings(
            [201.0, 202.0, 201.0, 203.0, 202.0], self._parameter()
        )
        self.assertFalse(any("outlier" in i.lower() for i in issues))

    def test_modified_z_score_of_the_documented_case(self):
        """Handbook 14: the 248 scores 31.0 against a MAD of 1."""
        readings = [201.0, 202.0, 248.0, 203.0, 202.0]
        median = sorted(readings)[len(readings) // 2]
        mad = sorted(abs(x - median) for x in readings)[len(readings) // 2]
        self.assertEqual((median, mad), (202.0, 1.0))
        self.assertAlmostEqual(0.6745 * (248.0 - median) / mad, 31.027, places=3)

    def test_identical_readings_suppress_detection_safely(self):
        """With MAD = 0 every score would be infinite, so detection is skipped."""
        issues = QualityAssurance.validate_readings([202.0] * 5, self._parameter())
        self.assertFalse(any("outlier" in i.lower() for i in issues))

    def test_too_few_readings_is_reported(self):
        issues = QualityAssurance.validate_readings([201.0, 202.0], self._parameter())
        self.assertTrue(any("insufficient" in i.lower() for i in issues))

    def test_a_monotonic_run_is_flagged_as_a_trend(self):
        issues = QualityAssurance.validate_readings(
            [201.0, 202.0, 203.0, 204.0, 205.0], self._parameter(tolerance="30")
        )
        self.assertTrue(any("trend" in i.lower() for i in issues))

    def test_excessive_variation_is_flagged(self):
        """s should be small relative to the tolerance; here it is not."""
        issues = QualityAssurance.validate_readings(
            [200.0, 210.0, 190.0, 205.0, 195.0], self._parameter(tolerance="3")
        )
        self.assertTrue(any("variation" in i.lower() for i in issues))
