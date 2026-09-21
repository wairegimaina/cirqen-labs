"""Drift and linearity report real answers — plan items P3.3, P3.4 and P5.7.

Both features previously failed silently:

* the drift consumer read five keys ``DriftAnalyzer.analyze_drift`` never
  returns, so every device displayed "No significant drift detected" whatever
  the data said;
* the linearity helper called a method that does not exist, and a bare
  ``except Exception`` degraded every parameter to "Unable to calculate
  linearity".

Neither raised. Both looked like working features, and one actively reassured.
These tests assert real numbers so that a repeat cannot pass unnoticed.

Drift grading is also asserted to be relative to tolerance (P5.7): an absolute
rate cannot be compared across parameters measured in different units.

    ./venv/bin/python manage.py test CalSoft.test_drift_and_linearity \
        --settings=Equiper.test_settings
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from CalSoft.models import (
    CalibrationParameter,
    CalibrationProcedure,
    CalibrationReading,
    CalibrationSession,
    HistoricalCalibration,
    SessionParameterResolution,
    SetValue,
)
from CalSoft.utils import (
    CalibrationCalculator,
    drift_fraction_of_tolerance,
    grade_drift,
    years_until_tolerance_breach,
)
from CalSoft.view_modules.sessions import (
    _build_drift_analysis,
    _calculate_linearity_analysis,
)


class DriftGradingTests(SimpleTestCase):
    """Grades are fractions of tolerance, not absolute rates — P5.7."""

    def test_fraction_of_tolerance(self):
        self.assertEqual(drift_fraction_of_tolerance("0.3", "3"), Decimal("0.1"))

    def test_the_same_rate_grades_differently_against_different_tolerances(self):
        """The whole point: 0.4/year is modest on +/-3 and severe on +/-0.1."""
        wide, _, wide_fraction = grade_drift("0.4", "3")
        narrow, _, narrow_fraction = grade_drift("0.4", "0.1")

        self.assertEqual(wide, "Normal")
        self.assertEqual(narrow, "Urgent")
        self.assertLess(wide_fraction, narrow_fraction)

    def test_band_boundaries(self):
        self.assertEqual(grade_drift("0.29", "3")[0], "Very stable")   # 0.097
        self.assertEqual(grade_drift("0.5", "3")[0], "Normal")         # 0.167
        self.assertEqual(grade_drift("1.0", "3")[0], "Drifting")       # 0.333
        self.assertEqual(grade_drift("2.0", "3")[0], "Urgent")         # 0.667

    def test_a_stable_device_is_very_stable(self):
        self.assertEqual(grade_drift("0", "3")[0], "Very stable")

    def test_direction_does_not_affect_the_grade(self):
        self.assertEqual(grade_drift("-1.0", "3")[0], grade_drift("1.0", "3")[0])

    def test_a_missing_tolerance_is_ungraded_not_guessed(self):
        """Without a tolerance the fraction is undefined, so no grade is invented."""
        label, recommendation, fraction = grade_drift("0.4", None)
        self.assertEqual(label, "Ungraded")
        self.assertIsNone(fraction)
        self.assertIn("cannot be graded", recommendation)

    def test_a_zero_tolerance_is_ungraded(self):
        self.assertEqual(grade_drift("0.4", "0")[0], "Ungraded")

    def test_years_until_breach(self):
        """Handbook 13.3: 1.2 of margin at 0.4/year is three years."""
        self.assertEqual(years_until_tolerance_breach("1.8", "0.4", "3"), Decimal("3"))

    def test_a_device_already_out_of_tolerance_has_no_margin(self):
        self.assertEqual(years_until_tolerance_breach("4", "0.4", "3"), Decimal("0"))

    def test_a_stable_device_never_breaches(self):
        self.assertIsNone(years_until_tolerance_breach("1.8", "0", "3"))


class LinearityAnalysisTests(TestCase):
    """The session page reports real linearity — P3.4."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="lin", password="x", email="lin@example.test"
        )
        cls.procedure = CalibrationProcedure.objects.create(
            name="Linearity", created_by=cls.user
        )
        cls.parameter = CalibrationParameter.objects.create(
            procedure=cls.procedure, name="Systolic", unit="mmHg", num_readings=5,
            standard_reference="REF", reference_uncertainty=Decimal("0.25"),
            coverage_factor=Decimal("2.0"), tolerance=Decimal("3"),
        )

    def _readings(self, points):
        """points: list of (set_value, mean_offset)."""
        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user,
            device_serial="SN-LIN", overall_pass=True,
        )
        SessionParameterResolution.objects.create(
            session=session, parameter=self.parameter, resolution=Decimal("1")
        )
        readings = []
        for i, (value, offset) in enumerate(points):
            set_value = SetValue.objects.create(
                parameter=self.parameter, value=Decimal(str(value)), order=i
            )
            reading = CalibrationReading.objects.create(
                session=session, parameter=self.parameter, set_value=set_value
            )
            base = Decimal(str(value)) + Decimal(str(offset))
            reading.reading_1 = base
            reading.reading_2 = base
            reading.save()
            reading.calculate_statistics()
            readings.append(reading)
        return readings

    def test_a_linear_device_reports_no_linearity_error(self):
        readings = self._readings([(0, 0), (50, 0), (100, 0)])
        result = _calculate_linearity_analysis({"Systolic_Default": readings})

        entry = result["Systolic_Default"]
        self.assertNotIn("error", entry)
        self.assertAlmostEqual(entry["max_linearity_error"], 0.0, places=6)
        self.assertEqual(entry["points"], 3)

    def test_real_numbers_are_reported_not_an_error_string(self):
        """The regression that matters: this used to be 'Unable to calculate'."""
        readings = self._readings([(0, 0.1), (50, 0.2), (100, 0.1)])
        result = _calculate_linearity_analysis({"Systolic_Default": readings})

        entry = result["Systolic_Default"]
        self.assertNotIn("error", entry)
        self.assertIsInstance(entry["max_linearity_error"], float)
        self.assertGreater(entry["max_linearity_error"], 0)

    def test_an_offset_shows_in_the_intercept_not_the_residual(self):
        """Handbook 12: an offset is zero-adjustable, so it is not non-linearity."""
        readings = self._readings([(0, 2), (50, 2), (100, 2)])
        entry = _calculate_linearity_analysis({"Systolic_Default": readings})["Systolic_Default"]

        self.assertAlmostEqual(entry["intercept"], 2.0, places=6)
        self.assertAlmostEqual(entry["slope"], 1.0, places=6)
        self.assertAlmostEqual(entry["max_linearity_error"], 0.0, places=6)

    def test_a_gain_error_shows_in_the_slope_not_the_residual(self):
        readings = self._readings([(0, 0), (50, 0.5), (100, 1.0)])
        entry = _calculate_linearity_analysis({"Systolic_Default": readings})["Systolic_Default"]

        self.assertAlmostEqual(entry["slope"], 1.01, places=6)
        self.assertAlmostEqual(entry["max_linearity_error"], 0.0, places=6)

    def test_a_single_point_is_skipped_not_errored(self):
        readings = self._readings([(50, 0)])
        self.assertEqual(_calculate_linearity_analysis({"Systolic_Default": readings}), {})

    def test_readings_without_a_mean_do_not_desynchronise_the_series(self):
        """Set values and means are paired positionally, so both must be filtered.

        The old helper filtered None means out of one list but not the other,
        which silently paired a set value with the wrong mean.
        """
        readings = self._readings([(0, 0), (50, 0), (100, 0)])
        readings[1].mean = None
        readings[1].save()

        entry = _calculate_linearity_analysis({"Systolic_Default": readings})["Systolic_Default"]
        self.assertEqual(entry["points"], 2)
        self.assertAlmostEqual(entry["max_linearity_error"], 0.0, places=6)


class DriftAnalysisTests(TestCase):
    """The session page reports real drift — P3.3."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="drift", password="x", email="drift@example.test"
        )
        cls.procedure = CalibrationProcedure.objects.create(
            name="Drift", created_by=cls.user
        )
        cls.parameter = CalibrationParameter.objects.create(
            procedure=cls.procedure, name="Systolic", unit="mmHg", num_readings=5,
            standard_reference="REF", reference_uncertainty=Decimal("0.25"),
            coverage_factor=Decimal("2.0"), tolerance=Decimal("3"),
        )

    def _session(self):
        return CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user,
            device_serial="SN-DRIFT", overall_pass=True,
        )

    def _history(self, errors, *, serial="SN-DRIFT", parameter="Systolic"):
        """One historical row per year, with the given error each time."""
        now = timezone.now()
        for i, error in enumerate(errors):
            HistoricalCalibration.objects.create(
                device_serial=serial,
                parameter_name=parameter,
                sub_parameter_name="",
                set_value=Decimal("200"),
                measured_value=Decimal("200") + Decimal(str(error)),
                error=Decimal(str(error)),
                uncertainty=Decimal("0.98"),
                calibration_date=now - timedelta(days=365 * (len(errors) - 1 - i)),
            )

    def test_no_history_yields_no_analysis(self):
        self.assertIsNone(_build_drift_analysis(self._session()))

    def test_a_device_with_no_serial_yields_no_analysis(self):
        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user,
            device_serial="", overall_pass=True,
        )
        self.assertIsNone(_build_drift_analysis(session))

    def test_real_drift_is_reported(self):
        """The regression that matters: this used to say 'No significant drift'."""
        self._history(["0.0", "0.5", "1.0"])
        analysis = _build_drift_analysis(self._session())

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis["points_analysed"], 1)
        row = analysis["parameters"][0]
        self.assertAlmostEqual(row["drift_per_year"], 0.5, places=3)
        self.assertEqual(row["direction"], "Increasing")

    def test_the_summary_is_not_a_hardcoded_string(self):
        self._history(["0.0", "0.5", "1.0"])
        analysis = _build_drift_analysis(self._session())

        self.assertNotEqual(analysis["trend"], "No significant drift detected")
        self.assertIn("Systolic", analysis["trend"])
        self.assertIn("per year", analysis["trend"])

    def test_a_stable_device_is_graded_very_stable(self):
        self._history(["0.4", "0.4", "0.4"])
        analysis = _build_drift_analysis(self._session())

        row = analysis["parameters"][0]
        self.assertEqual(row["direction"], "Stable")
        self.assertEqual(row["grade"], "Very stable")

    def test_the_grade_is_relative_to_tolerance(self):
        """0.5/year against +/-3 is 17% of tolerance — Normal, not 'Fair'."""
        self._history(["0.0", "0.5", "1.0"])
        row = _build_drift_analysis(self._session())["parameters"][0]

        self.assertEqual(row["grade"], "Normal")
        self.assertAlmostEqual(float(row["fraction_of_tolerance"]), 1 / 6, places=3)
        self.assertEqual(row["tolerance"], Decimal("3.000000"))

    def test_a_fast_drifting_device_is_urgent(self):
        self._history(["0.0", "2.0", "4.0"])
        row = _build_drift_analysis(self._session())["parameters"][0]

        self.assertEqual(row["grade"], "Urgent")
        self.assertIn("Shorten", row["recommendation"])

    def test_years_until_breach_is_reported(self):
        self._history(["0.0", "0.5", "1.0"])
        row = _build_drift_analysis(self._session())["parameters"][0]
        self.assertIsNotNone(row["years_until_breach"])
        self.assertGreater(row["years_until_breach"], 0)

    def test_a_parameter_with_no_tolerance_is_ungraded(self):
        """An unknown tolerance must not fall back to a unit-blind grade."""
        self._history(["0.0", "0.5", "1.0"], parameter="Unmapped")
        row = _build_drift_analysis(self._session())["parameters"][0]

        self.assertEqual(row["grade"], "Ungraded")
        self.assertIsNone(row["fraction_of_tolerance"])

    def test_history_for_another_device_is_not_included(self):
        self._history(["0.0", "0.5", "1.0"], serial="SN-OTHER")
        self.assertIsNone(_build_drift_analysis(self._session()))

    def test_the_worst_point_drives_the_overall_view(self):
        """One parameter drifting badly is the thing worth surfacing."""
        self._history(["0.0", "0.1", "0.2"])
        self._history(["0.0", "2.0", "4.0"], parameter="Diastolic")
        CalibrationParameter.objects.create(
            procedure=self.procedure, name="Diastolic", unit="mmHg", num_readings=5,
            standard_reference="REF", reference_uncertainty=Decimal("0.25"),
            coverage_factor=Decimal("2.0"), tolerance=Decimal("3"),
        )

        analysis = _build_drift_analysis(self._session())
        self.assertEqual(analysis["worst_grade"], "Urgent")
        self.assertIn("Diastolic", analysis["trend"])


class LinearityCalculatorContractTests(SimpleTestCase):
    """slope and intercept are part of the contract — they diagnose the fault."""

    def test_slope_and_intercept_are_returned(self):
        result = CalibrationCalculator().calculate_linearity([0, 50, 100], [2, 52, 102])
        self.assertIn("slope", result)
        self.assertIn("intercept", result)
        self.assertAlmostEqual(float(result["slope"]), 1.0, places=9)
        self.assertAlmostEqual(float(result["intercept"]), 2.0, places=9)
