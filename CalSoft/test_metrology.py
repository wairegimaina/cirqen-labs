"""Effective degrees of freedom, TUR and guarded decisions — plan items P5.1, P5.4, P5.6.

Three capabilities the module computed uncertainty without ever using:

* ``coverage_factor_for_95`` derives k from the effective degrees of freedom
  instead of assuming 2. At the three-to-five readings this system operates on,
  k = 2 understates a 95% interval.
* ``test_uncertainty_ratio`` answers whether the measurement is sharp enough to
  judge the tolerance at all.
* ``guarded_decision`` lets the uncertainty change the answer, with a third
  outcome for a reading the measurement cannot decide.

All pure arithmetic, so no database:

    ./venv/bin/python manage.py test CalSoft.test_metrology \
        --settings=Equiper.test_settings
"""

from decimal import Decimal

from django.test import SimpleTestCase

from CalSoft.utils import (
    FAIL,
    INDETERMINATE,
    PASS,
    coverage_factor_for_95,
    effective_degrees_of_freedom,
    guarded_decision,
    t_factor_95,
    test_uncertainty_ratio,
)

# The handbook §15 budget.
TYPE_A = Decimal("0.374166")
COMBINED = Decimal("0.488834")
EXPANDED = Decimal("0.977668")
TOLERANCE = Decimal("3")


class StudentTTests(SimpleTestCase):
    """Handbook section 11 — the multiplier widens as the sample shrinks."""

    def test_tabulated_values(self):
        self.assertEqual(t_factor_95(2), Decimal("4.303"))
        self.assertEqual(t_factor_95(4), Decimal("2.776"))
        self.assertEqual(t_factor_95(9), Decimal("2.262"))
        self.assertEqual(t_factor_95(19), Decimal("2.093"))

    def test_large_samples_approach_one_point_nine_six(self):
        self.assertEqual(t_factor_95(500), Decimal("1.960"))

    def test_an_untabulated_value_errs_toward_a_wider_interval(self):
        """22 degrees of freedom uses the row for 20, which is slightly wider."""
        self.assertEqual(t_factor_95(22), Decimal("2.086"))
        self.assertGreater(t_factor_95(22), t_factor_95(25))

    def test_unknown_degrees_of_freedom_falls_back_to_the_normal_value(self):
        self.assertEqual(t_factor_95(None), Decimal("1.960"))

    def test_t_always_exceeds_two_for_small_samples(self):
        for dof in (1, 2, 3, 4, 5, 6):
            self.assertGreater(t_factor_95(dof), Decimal("2"))


class EffectiveDegreesOfFreedomTests(SimpleTestCase):
    """Welch-Satterthwaite — P5.1, replacing a stub that returned 10."""

    def test_the_handbook_budget(self):
        """Type A is 59% of the budget, so nu_eff exceeds n-1 = 4."""
        dof = effective_degrees_of_freedom(TYPE_A, COMBINED, 5)
        self.assertIsNotNone(dof)
        self.assertGreater(dof, 4)

    def test_a_type_a_dominated_budget_approaches_n_minus_one(self):
        """When Type A is everything, nu_eff collapses to n-1."""
        dof = effective_degrees_of_freedom(Decimal("1"), Decimal("1"), 5)
        self.assertAlmostEqual(float(dof), 4.0, places=6)

    def test_a_type_b_dominated_budget_has_many_degrees_of_freedom(self):
        """A tiny Type A against a large combined means s barely matters."""
        dof = effective_degrees_of_freedom(Decimal("0.01"), Decimal("1"), 5)
        self.assertGreater(dof, 1000)

    def test_no_type_a_is_unbounded(self):
        self.assertIsNone(effective_degrees_of_freedom(Decimal("0"), COMBINED, 5))

    def test_a_single_reading_is_unbounded(self):
        self.assertIsNone(effective_degrees_of_freedom(TYPE_A, COMBINED, 1))

    def test_it_depends_on_its_arguments(self):
        """The regression guard: the old stub returned 10 for everything."""
        few = effective_degrees_of_freedom(Decimal("1"), Decimal("1"), 3)
        many = effective_degrees_of_freedom(Decimal("1"), Decimal("1"), 21)
        self.assertNotEqual(few, many)
        self.assertLess(few, many)


class CoverageFactorTests(SimpleTestCase):
    """k derived rather than assumed — P5.1."""

    def test_the_handbook_budget_needs_more_than_two(self):
        k, dof = coverage_factor_for_95(TYPE_A, COMBINED, 5)
        self.assertIsNotNone(dof)
        self.assertGreater(k, Decimal("2"))

    def test_a_small_type_a_dominated_sample_approaches_the_t_value(self):
        k, _ = coverage_factor_for_95(Decimal("1"), Decimal("1"), 5)
        self.assertEqual(k, Decimal("2.776"))

    def test_a_larger_sample_needs_a_smaller_k(self):
        k_small, _ = coverage_factor_for_95(Decimal("1"), Decimal("1"), 5)
        k_large, _ = coverage_factor_for_95(Decimal("1"), Decimal("1"), 21)
        self.assertLess(k_large, k_small)

    def test_a_type_b_only_budget_keeps_k_at_two(self):
        k, dof = coverage_factor_for_95(Decimal("0"), COMBINED, 5)
        self.assertEqual(k, Decimal("2"))
        self.assertIsNone(dof)

    def test_k_equals_two_understates_the_handbook_interval(self):
        """Handbook 11: the reason this item exists."""
        k, _ = coverage_factor_for_95(Decimal("1"), Decimal("1"), 5)
        assumed = COMBINED * Decimal("2")
        honest = COMBINED * k
        self.assertGreater(honest, assumed)
        self.assertGreater((honest - assumed) / assumed, Decimal("0.3"))


class TestUncertaintyRatioTests(SimpleTestCase):
    """TUR — P5.6."""

    def test_the_handbook_value(self):
        tur = test_uncertainty_ratio(TOLERANCE, EXPANDED)
        self.assertEqual(tur.quantize(Decimal("0.01")), Decimal("3.07"))

    def test_below_the_customary_floor(self):
        self.assertLess(test_uncertainty_ratio(TOLERANCE, EXPANDED), Decimal("4"))

    def test_a_sharper_measurement_clears_the_floor(self):
        self.assertGreater(test_uncertainty_ratio(TOLERANCE, Decimal("0.5")), Decimal("4"))

    def test_zero_uncertainty_is_not_an_infinite_ratio(self):
        self.assertIsNone(test_uncertainty_ratio(TOLERANCE, Decimal("0")))

    def test_a_missing_tolerance_yields_nothing(self):
        self.assertIsNone(test_uncertainty_ratio(None, EXPANDED))


class GuardedDecisionTests(SimpleTestCase):
    """Three outcomes, not two — P5.4."""

    def test_comfortably_inside_passes(self):
        verdict, acceptance, rejection = guarded_decision(
            Decimal("1.8"), TOLERANCE, EXPANDED
        )
        self.assertEqual(verdict, PASS)
        self.assertEqual(acceptance, TOLERANCE - EXPANDED)
        self.assertEqual(rejection, TOLERANCE + EXPANDED)

    def test_the_handbook_worked_example_passes_guarded(self):
        """1.8 against an acceptance limit of 2.022 — handbook 10.2."""
        verdict, acceptance, _ = guarded_decision(Decimal("1.8"), TOLERANCE, EXPANDED)
        self.assertEqual(verdict, PASS)
        self.assertEqual(acceptance.quantize(Decimal("0.001")), Decimal("2.022"))

    def test_a_marginal_reading_is_indeterminate(self):
        """2.9 of 3.0 used to be an unqualified PASS. It cannot be decided."""
        verdict, _, _ = guarded_decision(Decimal("2.9"), TOLERANCE, EXPANDED)
        self.assertEqual(verdict, INDETERMINATE)

    def test_just_inside_the_tolerance_is_still_indeterminate(self):
        verdict, _, _ = guarded_decision(Decimal("2.999"), TOLERANCE, EXPANDED)
        self.assertEqual(verdict, INDETERMINATE)

    def test_just_outside_the_tolerance_is_also_indeterminate(self):
        """Symmetry: the uncertainty cuts both ways."""
        verdict, _, _ = guarded_decision(Decimal("3.001"), TOLERANCE, EXPANDED)
        self.assertEqual(verdict, INDETERMINATE)

    def test_clearly_outside_fails(self):
        verdict, _, _ = guarded_decision(Decimal("4.5"), TOLERANCE, EXPANDED)
        self.assertEqual(verdict, FAIL)

    def test_the_sign_of_the_error_does_not_matter(self):
        for value in ("1.8", "-1.8"):
            self.assertEqual(guarded_decision(Decimal(value), TOLERANCE, EXPANDED)[0], PASS)
        for value in ("4.5", "-4.5"):
            self.assertEqual(guarded_decision(Decimal(value), TOLERANCE, EXPANDED)[0], FAIL)

    def test_exactly_on_the_acceptance_limit_passes(self):
        acceptance = TOLERANCE - EXPANDED
        self.assertEqual(guarded_decision(acceptance, TOLERANCE, EXPANDED)[0], PASS)

    def test_exactly_on_the_rejection_limit_fails(self):
        rejection = TOLERANCE + EXPANDED
        self.assertEqual(guarded_decision(rejection, TOLERANCE, EXPANDED)[0], FAIL)

    def test_guarding_is_stricter_than_simple_acceptance(self):
        """The trade this makes: fewer bad devices pass, more good ones are held."""
        simple_pass = abs(Decimal("2.9")) <= TOLERANCE
        guarded, _, _ = guarded_decision(Decimal("2.9"), TOLERANCE, EXPANDED)
        self.assertTrue(simple_pass)
        self.assertNotEqual(guarded, PASS)

    def test_an_uncertainty_larger_than_the_tolerance_accepts_nothing(self):
        """Nothing can be accepted when U exceeds T, and the limit clamps at zero."""
        verdict, acceptance, _ = guarded_decision(
            Decimal("0.5"), Decimal("1"), Decimal("2")
        )
        self.assertEqual(acceptance, Decimal("0"))
        self.assertEqual(verdict, INDETERMINATE)

    def test_a_perfect_reading_passes_even_with_a_huge_uncertainty(self):
        verdict, _, _ = guarded_decision(Decimal("0"), Decimal("1"), Decimal("2"))
        self.assertEqual(verdict, PASS)

    def test_no_uncertainty_degrades_to_simple_acceptance(self):
        """Stated by returning no limits, so a caller can tell which rule ran."""
        verdict, acceptance, rejection = guarded_decision(
            Decimal("2.9"), TOLERANCE, None
        )
        self.assertEqual(verdict, PASS)
        self.assertIsNone(acceptance)
        self.assertIsNone(rejection)
