"""Single-reading budgets and one-sided verdicts, pinned to the worked
examples in review/12_ANSUR_PERFORM_CALIBRATION_PLAN (section 6.2)."""
from decimal import Decimal as D

from django.test import SimpleTestCase

from CalSoft.utils import (
    FAIL, INDETERMINATE, LOWER, PASS, UPPER,
    compute_single_reading_budget, guarded_decision, one_sided_decision,
    test_uncertainty_ratio, within_limit,
)


class DefibrillatorEnergyExample(SimpleTestCase):
    """Set 360 J +/-36 J, measured 352.4 J, spec +/-(1% + 0.1 J), res 0.1 J,
    analyser certificate 1.0 J at k=2."""

    def setUp(self):
        self.b = compute_single_reading_budget(
            D("352.4"), D("0.1"), D("1.0"), D("2"), accuracy_percent=D("1"), accuracy_floor=D("0.1"))

    def test_components(self):
        self.assertEqual(self.b["spec"], D("2.092317"))
        self.assertEqual(self.b["type_b"], D("0.028868"))
        self.assertEqual(self.b["calibration"], D("0.500000"))
        self.assertEqual(self.b["type_a"], D("0.000000"))
        self.assertIsNone(self.b["std_dev"])

    def test_combined_and_expanded(self):
        self.assertEqual(self.b["combined"], D("2.151424"))
        self.assertEqual(self.b["expanded"], D("4.302848"))

    def test_verdict_and_tur(self):
        error = D("360") - self.b["mean"]
        self.assertEqual(error, D("7.600000"))
        self.assertEqual(round(test_uncertainty_ratio(D("36"), self.b["expanded"]), 2), D("8.37"))
        verdict, acceptance, _ = guarded_decision(error, D("36"), self.b["expanded"])
        self.assertEqual(acceptance, D("31.697152"))
        self.assertEqual(verdict, PASS)


class EarthLeakageExample(SimpleTestCase):
    """At most 500 uA, measured 112 uA, spec +/-(1% + 1 uA), res 1 uA,
    certificate 2 uA at k=2."""

    def setUp(self):
        self.b = compute_single_reading_budget(
            D("112"), D("1"), D("2"), D("2"), accuracy_percent=D("1"), accuracy_floor=D("1"))

    def test_expanded(self):
        self.assertEqual(self.b["expanded"], D("3.213389"))

    def test_upper_limit_verdict(self):
        verdict, acceptance, rejection = one_sided_decision(D("112"), D("500"), self.b["expanded"], UPPER)
        self.assertEqual(verdict, PASS)
        self.assertEqual(acceptance, D("496.786611"))
        self.assertEqual(rejection, D("503.213389"))


class OneSidedBands(SimpleTestCase):
    def test_upper(self):
        self.assertEqual(one_sided_decision(D("498"), D("500"), D("3"), UPPER)[0], INDETERMINATE)
        self.assertEqual(one_sided_decision(D("503"), D("500"), D("3"), UPPER)[0], FAIL)
        self.assertEqual(one_sided_decision(D("497"), D("500"), D("3"), UPPER)[0], PASS)

    def test_lower(self):
        self.assertEqual(one_sided_decision(D("2.5"), D("2"), D("0.2"), LOWER)[0], PASS)
        self.assertEqual(one_sided_decision(D("2.1"), D("2"), D("0.2"), LOWER)[0], INDETERMINATE)
        self.assertEqual(one_sided_decision(D("1.8"), D("2"), D("0.2"), LOWER)[0], FAIL)

    def test_no_uncertainty_is_simple_acceptance(self):
        self.assertEqual(one_sided_decision(D("500"), D("500"), None, UPPER), (PASS, None, None))
        self.assertEqual(one_sided_decision(D("1.9"), D("2"), 0, LOWER), (FAIL, None, None))

    def test_two_sided_type_is_refused(self):
        with self.assertRaises(ValueError):
            one_sided_decision(1, 2, 0.1, "two_sided")

    def test_simple_acceptance(self):
        self.assertTrue(within_limit(UPPER, D("500"), D("499"), None))
        self.assertFalse(within_limit(LOWER, D("2"), D("1.9"), None))
        self.assertTrue(within_limit("two_sided", D("360"), D("352.4"), D("36")))
        self.assertFalse(within_limit("two_sided", D("360"), D("300"), D("36")))
