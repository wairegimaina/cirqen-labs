"""The worked example in docs/validation/ISO17025_VALIDATION.md, section 5.

If a change to the uncertainty budget moves any of these numbers, the
validation pack must be re-issued (change control, section 7).
"""
from decimal import Decimal as D

from django.test import SimpleTestCase

from CalSoft.utils import PASS, compute_uncertainty_budget, guarded_decision, test_uncertainty_ratio

READINGS = [D("120.2"), D("120.6"), D("120.4"), D("120.8"), D("120.5")]


class WorkedExampleTests(SimpleTestCase):
    def test_budget_matches_the_validation_pack(self):
        b = compute_uncertainty_budget(readings=READINGS, resolution=D("0.1"), reference_uncertainty=D("0.3"),
                                       coverage_factor=D("2"), reference_is_expanded=True)
        self.assertEqual(b["mean"], D("120.500000"))
        self.assertEqual(b["std_dev"], D("0.223607"))
        self.assertEqual(b["type_a"], D("0.100000"))
        self.assertEqual(b["type_b"], D("0.028868"))
        self.assertEqual(b["reference"], D("0.150000"))
        self.assertEqual(b["combined"], D("0.182574"))
        self.assertEqual(b["expanded"], D("0.368982"))
        self.assertEqual(str(b["coverage_factor"]), "2.021")
        self.assertEqual(D("120") - b["mean"], D("-0.500000"))  # error = set value - mean
        self.assertEqual(round(test_uncertainty_ratio(D("3"), b["expanded"]), 2), D("8.13"))
        self.assertEqual(guarded_decision(D("-0.500000"), D("3"), b["expanded"]),
                         (PASS, D("2.631018"), D("3.368982")))
