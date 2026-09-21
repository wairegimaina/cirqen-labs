# utils.py
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from datetime import datetime, timedelta
from django.utils import timezone
from django.template.loader import render_to_string
import logging

# One decimal precision for the whole application, set once.
#
# This module was previously two modules concatenated, and the second copy of
# the header reset the precision to 12 further down the file. The later
# assignment won at import time and applied process-wide — to every Decimal
# operation in every app, not just this one. 28 digits is Python's default and
# is far more than any measurement needs; the limit on reported precision is the
# six-decimal quantisation applied when results are stored, not this context.
getcontext().prec = 28

logger = logging.getLogger(__name__)


def resolution_for_reading(reading, default=Decimal('1')):
    """The resolution in force for a reading, from its session.

    Resolution belongs to the device on the day, not to the procedure, so it is
    stored per session in ``SessionParameterResolution``. Three helpers in this
    module previously read ``reading.parameter.resolution``, a field that does
    not exist on ``CalibrationParameter``, and raised ``AttributeError`` on
    every call.
    """
    try:
        return reading.session.parameter_resolutions.get(
            parameter=reading.parameter
        ).resolution
    except Exception:
        logger.warning(
            "No session resolution for reading %s; falling back to %s",
            getattr(reading, 'id', '?'), default,
        )
        return default


class CalibrationCalculator:
    """
    Handles statistical and uncertainty calculations for calibration readings.
    """

    def __init__(self, reference_is_expanded=True):
        """
        :param reference_is_expanded: bool, if True then reference_uncertainty
               provided to calculations is expanded (divide by k to get standard)
        """
        self.reference_is_expanded = reference_is_expanded

    # -----------------------------
    # Basic Statistics
    # -----------------------------
    def calculate_statistics(self, readings):
        """
        Calculate mean and sample standard deviation for a list of readings.
        Returns dict with 'mean', 'std_dev', and 'count'.
        """
        readings = [Decimal(str(r)) for r in readings if r is not None]
        n = len(readings)
        if n < 2:
            return None

        mean = sum(readings) / Decimal(n)
        variance = sum((r - mean) ** 2 for r in readings) / Decimal(n - 1)
        std_dev = variance.sqrt()

        return {
            'mean': mean,
            'std_dev': std_dev,
            'count': n
        }

    # -----------------------------
    # Type A
    # -----------------------------
    def calculate_type_a_uncertainty(self, std_dev, count):
        """
        Standard uncertainty of the mean from Type A evaluation.
        u_A = s / sqrt(n)
        """
        if count <= 0:
            return Decimal('0')
        return Decimal(str(std_dev)) / Decimal(str(math.sqrt(count)))

    # -----------------------------
    # Type B: Resolution
    # -----------------------------
    def calculate_type_b_uncertainty(self, resolution):
        """
        Standard uncertainty from resolution (rectangular distribution).
        u_res = resolution / sqrt(12)
        """
        return Decimal(str(resolution)) / Decimal(str(math.sqrt(12)))

    # -----------------------------
    # Reference Uncertainty Component
    # -----------------------------
    def calculate_reference_uncertainty(self, reference_uncertainty, coverage_factor):
        """
        Returns the standard uncertainty component for the reference.
        If self.reference_is_expanded=True, divides by coverage factor.
        """
        ref_unc = Decimal(str(reference_uncertainty))
        cov_factor = Decimal(str(coverage_factor))
        if self.reference_is_expanded:
            return ref_unc / cov_factor
        return ref_unc  # already standard

    # -----------------------------
    # Combined Standard Uncertainty
    # -----------------------------
    def calculate_combined_uncertainty(self, type_a_uncertainty, type_b_uncertainty, reference_uncertainty_component):
        """
        Combine uncertainties in quadrature:
        u_c = sqrt(u_A^2 + u_res^2 + u_ref^2)
        """
        u_A = Decimal(str(type_a_uncertainty))
        u_res = Decimal(str(type_b_uncertainty))
        u_ref = Decimal(str(reference_uncertainty_component))
        return Decimal(str(math.sqrt(u_A**2 + u_res**2 + u_ref**2)))

    # -----------------------------
    # Expanded Uncertainty
    # -----------------------------
    def calculate_expanded_uncertainty(self, combined_uncertainty, coverage_factor):
        """
        U = k * u_c
        """
        return Decimal(str(combined_uncertainty)) * Decimal(str(coverage_factor))

    # -----------------------------
    # Tolerance Check
    # -----------------------------
    def check_tolerance(self, error, tolerance):
        """
        Returns True if |error| <= tolerance
        """
        return abs(Decimal(str(error))) <= Decimal(str(tolerance))
    def calculate_linearity(self, set_values, measured_values, full_scale=None, return_percent=True):
  
        if len(set_values) != len(measured_values) or len(set_values) < 2:
            raise ValueError("Set values and measured values must have the same length >= 2.")

        set_vals = [Decimal(str(v)) for v in set_values]
        meas_vals = [Decimal(str(v)) for v in measured_values]

        # Determine full scale if not given
        if full_scale is None:
            full_scale = max(set_vals) - min(set_vals)
        full_scale = Decimal(str(full_scale))

        # Least squares best fit line y = a*x + b
        n = len(set_vals)
        sum_x = sum(set_vals)
        sum_y = sum(meas_vals)
        sum_xy = sum(set_vals[i] * meas_vals[i] for i in range(n))
        sum_x2 = sum(x * x for x in set_vals)

        slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x**2)
        intercept = (sum_y - slope * sum_x) / n

        # Calculate linearity errors
        errors_units = []
        errors_percent = []
        for x, y in zip(set_vals, meas_vals):
            y_fit = slope * x + intercept
            err_units = y - y_fit
            errors_units.append(err_units)
            errors_percent.append((err_units / full_scale) * Decimal('100'))

        max_err_units = max(errors_units, key=lambda e: abs(e))
        max_err_percent = max(errors_percent, key=lambda e: abs(e))

        # slope and intercept are returned because they diagnose different
        # faults: an intercept away from zero is an offset (zero-adjustable), a
        # slope away from one is a gain error (span-adjustable), and large
        # residuals are true non-linearity, which is not adjustable at all.
        return {
            'slope': slope,
            'intercept': intercept,
            'full_scale': full_scale,
            'max_linearity_error_units': max_err_units,
            'max_linearity_error_percent': max_err_percent,
            'errors_units': errors_units,
            'errors_percent': errors_percent
        }


# ── Coverage factor from effective degrees of freedom ────────────────────────
#
# k = 2 assumes the standard deviation is well known. With the three to five
# readings this system actually operates on, it is not: s was estimated from
# n-1 degrees of freedom and could easily be too small. The honest multiplier
# comes from Student's t, which widens as the sample shrinks.
#
# Only Type A carries few degrees of freedom. Resolution and the reference
# standard are effectively known (conventionally infinite), so the blend —
# Welch-Satterthwaite — gives an effective degrees of freedom higher than n-1
# and a multiplier between 2 and t(n-1). That is what this computes.
#
# The previous implementation was declared as Welch-Satterthwaite and its body
# was `return max(2, 10)`: a literal 10, independent of both arguments.

# Two-sided Student's t at 95% confidence, by degrees of freedom.
T_95 = {
    1: Decimal('12.706'), 2: Decimal('4.303'), 3: Decimal('3.182'),
    4: Decimal('2.776'), 5: Decimal('2.571'), 6: Decimal('2.447'),
    7: Decimal('2.365'), 8: Decimal('2.306'), 9: Decimal('2.262'),
    10: Decimal('2.228'), 11: Decimal('2.201'), 12: Decimal('2.179'),
    13: Decimal('2.160'), 14: Decimal('2.145'), 15: Decimal('2.131'),
    16: Decimal('2.120'), 17: Decimal('2.110'), 18: Decimal('2.101'),
    19: Decimal('2.093'), 20: Decimal('2.086'), 25: Decimal('2.060'),
    30: Decimal('2.042'), 40: Decimal('2.021'), 50: Decimal('2.009'),
    100: Decimal('1.984'),
}
T_95_LARGE = Decimal('1.960')


def statistics_from_readings(readings):
    """
    Compute mean and sample standard deviation from a list of readings.
    
    Args:
        readings (list): List of numeric values (measurements).
    
    Returns:
        tuple: (mean, sample_std, count)
    """
    n = len(readings)
    if n == 0:
        raise ValueError("readings must contain at least one value")

    # Delegates to the one implementation so the arithmetic cannot diverge.
    # The single-reading case is handled here rather than there: a standard
    # deviation is undefined for n = 1, and `calculate_statistics` correctly
    # returns None. This function's callers (quality-assurance checks) need a
    # number, and 0 is the only defensible one — with the caveat that it means
    # "unknown", not "perfectly repeatable".
    if n == 1:
        return float(Decimal(str(readings[0]))), 0.0, 1

    stats = CalibrationCalculator().calculate_statistics(readings)
    return float(stats['mean']), float(stats['std_dev']), stats['count']


def t_factor_95(effective_dof):
    """Student's t at 95% for the given degrees of freedom, rounded up in width.

    Interpolation between tabulated rows is not attempted: the next lower row
    is used, which errs toward a wider interval. Overstating an uncertainty is
    the safe direction.
    """
    if effective_dof is None:
        return T_95_LARGE
    try:
        dof = int(effective_dof)
    except (TypeError, ValueError):
        return T_95_LARGE
    if dof < 1:
        return T_95[1]
    if dof in T_95:
        return T_95[dof]
    candidates = [d for d in T_95 if d <= dof]
    if not candidates:
        return T_95[1]
    if dof > 100:
        return T_95_LARGE
    return T_95[max(candidates)]


def effective_degrees_of_freedom(type_a, combined, count):
    """Welch-Satterthwaite effective degrees of freedom.

        nu_eff = u_c^4 / (u_A^4 / (n - 1))

    Type B components are taken as known (infinite degrees of freedom), so they
    contribute nothing to the denominator — which is the standard treatment for
    a resolution bound and an accredited reference uncertainty.

    Returns ``None`` when Type A contributes nothing, because the effective
    degrees of freedom is then unbounded and ``k = 2`` needs no correction.
    """
    try:
        u_a = abs(Decimal(str(type_a)))
        u_c = abs(Decimal(str(combined)))
        n = int(count)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if u_a == 0 or u_c == 0 or n < 2:
        return None
    denominator = (u_a ** 4) / Decimal(n - 1)
    if denominator == 0:
        return None
    return (u_c ** 4) / denominator


def coverage_factor_for_95(type_a, combined, count):
    """The k that actually gives ~95% coverage for this budget.

    Returns ``(k, effective_dof)``. Where Type A dominates a small sample this
    exceeds 2 — at n = 5 with repeatability dominating it approaches 2.78 —
    and where Type B dominates it falls back toward 2.
    """
    dof = effective_degrees_of_freedom(type_a, combined, count)
    if dof is None:
        return (Decimal('2'), None)
    k = t_factor_95(dof)
    return (k, dof)


# ── Conformity decisions ─────────────────────────────────────────────────────

PASS = 'PASS'
FAIL = 'FAIL'
INDETERMINATE = 'INDETERMINATE'

TUR_FLOOR = Decimal('4')


def test_uncertainty_ratio(tolerance, expanded_uncertainty):
    """TUR = tolerance / U. Is the measurement sharp enough to judge the limit?

    Returns ``None`` when it cannot be computed. The customary floor is 4:1;
    below that a bare pass/fail on a marginal point overstates what is known.
    """
    try:
        tol = abs(Decimal(str(tolerance)))
        expanded = abs(Decimal(str(expanded_uncertainty)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if expanded == 0 or tol == 0:
        return None
    return tol / expanded


def guarded_decision(error, tolerance, expanded_uncertainty):
    """Conformity with the measurement uncertainty taken into account.

    Simple acceptance compares |error| with the tolerance and ignores the
    uncertainty entirely, so a reading just inside the limit passes even when
    the uncertainty puts the true value well outside it.

    Guard banding pulls the acceptance limit in by U and pushes the rejection
    limit out by U, which yields three outcomes rather than two:

        |error| <= T - U   PASS           uncertainty cannot change it
        |error| >= T + U   FAIL           uncertainty cannot save it
        otherwise          INDETERMINATE  the measurement cannot decide

    The third outcome is the point. A device landing there is not condemned; it
    needs a better measurement, or an accepted-risk decision recorded by
    someone with the authority to make it.

    Returns ``(verdict, acceptance_limit, rejection_limit)``. With no usable
    uncertainty it degrades to simple acceptance, and says so by returning
    ``None`` for both limits.
    """
    try:
        err = abs(Decimal(str(error)))
        tol = abs(Decimal(str(tolerance)))
    except (InvalidOperation, TypeError, ValueError):
        return (INDETERMINATE, None, None)

    try:
        expanded = abs(Decimal(str(expanded_uncertainty)))
    except (InvalidOperation, TypeError, ValueError):
        expanded = None

    if not expanded:
        return (PASS if err <= tol else FAIL, None, None)

    acceptance = tol - expanded
    rejection = tol + expanded

    if acceptance < 0:
        # The uncertainty exceeds the tolerance: nothing can be accepted, and
        # only a gross failure can be rejected with confidence.
        acceptance = Decimal('0')

    if err <= acceptance:
        return (PASS, acceptance, rejection)
    if err >= rejection:
        return (FAIL, acceptance, rejection)
    return (INDETERMINATE, acceptance, rejection)


# ── The uncertainty budget: one implementation ───────────────────────────────

SIX_DP = Decimal('0.000001')


def compute_uncertainty_budget(
    readings,
    resolution,
    reference_uncertainty,
    coverage_factor=Decimal('2'),
    *,
    reference_is_expanded=True,
    quantise=True,
):
    """Compute a complete uncertainty budget for one measurement point.

    The single implementation. Both the stored result
    (``CalibrationReading.calculate_statistics``) and the live preview
    (``api_calculate_uncertainty``) call this, so the number a technician sees
    while typing is the number that gets saved.

    They previously diverged: the preview computed Type A from the full-precision
    standard deviation while the model computed it from the value already
    quantised to six decimal places, so the two could differ in the last digits
    for the same readings.

    Args:
        readings: measured values; ``None`` entries are ignored.
        resolution: the display resolution, as a full quantisation step.
        reference_uncertainty: from the reference standard's certificate.
        coverage_factor: k, applied to expand the combined uncertainty.
        reference_is_expanded: whether ``reference_uncertainty`` is expanded
            (and so must be divided by k) or already standard.
        quantise: round each result to six decimal places, matching what the
            model stores. Pass ``False`` for full precision.

    Returns:
        A dict of ``Decimal`` values, or ``None`` when there are fewer than two
        readings and the standard deviation is therefore undefined.
    """
    calculator = CalibrationCalculator(reference_is_expanded=reference_is_expanded)

    stats = calculator.calculate_statistics(readings)
    if not stats:
        return None

    stated_k = Decimal(str(coverage_factor or 2))

    def out(value):
        return value.quantize(SIX_DP, rounding=ROUND_HALF_UP) if quantise else value

    mean = out(stats['mean'])
    std_dev = out(stats['std_dev'])

    # Type A is computed from the same standard deviation that is reported, so
    # the printed budget is internally consistent.
    type_a = out(calculator.calculate_type_a_uncertainty(std_dev, stats['count']))
    type_b = out(calculator.calculate_type_b_uncertainty(resolution))

    # The reference standard's own certificate quotes its uncertainty at the
    # coverage factor stated on it, so that division uses the stated k — not
    # the derived one, which belongs to this measurement rather than to the
    # standard.
    reference = out(calculator.calculate_reference_uncertainty(
        reference_uncertainty or 0, stated_k
    ))
    combined = out(calculator.calculate_combined_uncertainty(type_a, type_b, reference))

    # k derived from the effective degrees of freedom, not assumed.
    #
    # A stated k of 2 assumes the standard deviation is well known. With the
    # three to five readings this system operates on it is not, and a 95%
    # interval needs Student's t — 2.776 at four degrees of freedom. Only
    # Type A carries few degrees of freedom, so the Welch-Satterthwaite blend
    # lands between 2 and t(n-1) depending on how much of the budget is
    # repeatability.
    derived_k, effective_dof = coverage_factor_for_95(type_a, combined, stats['count'])
    k = max(stated_k, derived_k)

    expanded = out(calculator.calculate_expanded_uncertainty(combined, k))

    return {
        'mean': mean,
        'std_dev': std_dev,
        'count': stats['count'],
        'type_a': type_a,
        'type_b': type_b,
        'reference': reference,
        'combined': combined,
        'expanded': expanded,
        'coverage_factor': k,
        'stated_coverage_factor': stated_k,
        'effective_dof': effective_dof,
        'coverage_factor_is_derived': k != stated_k,
    }


# ── Drift grading ────────────────────────────────────────────────────────────
#
# Drift is a dimensioned rate, so a bare number cannot be graded. The previous
# thresholds (0.1 / 0.5 / 1.0 per year) were applied to every parameter
# regardless of unit or tolerance, so 0.4/year graded "Good" whether it was
# 13% of a 3 mmHg tolerance or 400% of a 0.1 mV one — and that grade drove a
# printed recommendation to change a service interval.
#
# Dividing by the parameter's own tolerance makes the quantity dimensionless
# and comparable across parameters, which is the only form in which a single
# set of thresholds means anything.

DRIFT_BANDS = (
    (Decimal('0.10'), 'Very stable', 'Interval can likely be extended.'),
    (Decimal('0.25'), 'Normal', 'Current calibration interval is appropriate.'),
    (Decimal('0.50'), 'Drifting', 'Consider shortening the calibration interval.'),
)
DRIFT_BAND_URGENT = ('Urgent', 'Shorten the calibration interval: tolerance will be '
                               'breached within two years at this rate.')


def drift_fraction_of_tolerance(drift_per_year, tolerance):
    """|drift per year| / tolerance — dimensionless, so comparable.

    Returns ``None`` when the tolerance is missing or zero, because the
    fraction is undefined and a grade would be invented rather than measured.
    """
    if tolerance in (None, ''):
        return None
    try:
        tol = abs(Decimal(str(tolerance)))
        rate = abs(Decimal(str(drift_per_year)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if tol == 0:
        return None
    return rate / tol


def grade_drift(drift_per_year, tolerance):
    """Grade a drift rate against the parameter's own tolerance.

    Returns ``(label, recommendation, fraction)``. When the tolerance is
    unknown the label is ``'Ungraded'`` and the recommendation says so, rather
    than falling back to a unit-blind comparison.
    """
    fraction = drift_fraction_of_tolerance(drift_per_year, tolerance)
    if fraction is None:
        return ('Ungraded',
                'No tolerance on record for this parameter, so the drift rate '
                'cannot be graded.',
                None)
    for limit, label, recommendation in DRIFT_BANDS:
        if fraction < limit:
            return (label, recommendation, fraction)
    return (DRIFT_BAND_URGENT[0], DRIFT_BAND_URGENT[1], fraction)


def years_until_tolerance_breach(current_error, drift_per_year, tolerance):
    """How long until a steadily drifting device reaches its tolerance.

    Handbook section 13.3. Returns ``None`` when the device is not drifting, or
    when the inputs are unusable — an infinite answer is not a useful one.
    """
    try:
        rate = abs(Decimal(str(drift_per_year)))
        tol = abs(Decimal(str(tolerance)))
        err = abs(Decimal(str(current_error)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if rate == 0 or tol == 0:
        return None
    margin = tol - err
    if margin <= 0:
        return Decimal('0')
    return margin / rate


class DriftAnalyzer:
    """Analyze calibration drift over time"""
    
    @staticmethod
    def analyze_drift(device_serial, current_date=None):
        """Analyze drift for a specific device"""
        from CalSoft.models import HistoricalCalibration  # Move import here
        if current_date is None:
            current_date = timezone.now()
        
        # Get historical data for the device
        historical_data = HistoricalCalibration.objects.filter(
            device_serial=device_serial,
            calibration_date__lte=current_date
        ).order_by('parameter_name', 'set_value', 'calibration_date')
        
        if not historical_data.exists():
            return None
        
        drift_results = {}
        
        # Group by parameter and set value
        for param_name in historical_data.values_list('parameter_name', flat=True).distinct():
            param_data = historical_data.filter(parameter_name=param_name)
            drift_results[param_name] = {}
            
            for set_val in param_data.values_list('set_value', flat=True).distinct():
                readings = param_data.filter(set_value=set_val).order_by('calibration_date')
                
                if readings.count() > 1:
                    drift_analysis = DriftAnalyzer._calculate_drift_metrics(readings)
                    drift_results[param_name][float(set_val)] = drift_analysis
        
        return drift_results
    
    @staticmethod
    def _calculate_drift_metrics(readings):
        """Calculate drift metrics for a series of readings"""
        readings_list = list(readings)
        n = len(readings_list)
        
        if n < 2:
            return None
        
        # Extract data
        dates = [r.calibration_date for r in readings_list]
        errors = [float(r.error) for r in readings_list]
        uncertainties = [float(r.uncertainty) for r in readings_list]
        
        # Calculate time intervals in days
        base_date = dates[0]
        days_elapsed = [(date - base_date).days for date in dates]
        
        # Linear regression for drift rate
        if n > 1:
            sum_x = sum(days_elapsed)
            sum_y = sum(errors)
            sum_xy = sum(x * y for x, y in zip(days_elapsed, errors))
            sum_xx = sum(x * x for x in days_elapsed)
            
            denominator = n * sum_xx - sum_x * sum_x
            if denominator != 0:
                drift_rate_per_day = (n * sum_xy - sum_x * sum_y) / denominator
                drift_intercept = (sum_y - drift_rate_per_day * sum_x) / n
            else:
                drift_rate_per_day = 0
                drift_intercept = sum_y / n if n > 0 else 0
        else:
            drift_rate_per_day = 0
            drift_intercept = errors[0]
        
        # Calculate drift statistics
        total_drift = errors[-1] - errors[0]
        total_time_days = days_elapsed[-1] if days_elapsed[-1] > 0 else 1
        average_drift_rate = total_drift / total_time_days
        
        # Stability assessment
        max_error = max(errors)
        min_error = min(errors)
        error_range = max_error - min_error
        
        # Classify stability
        yearly_drift = abs(drift_rate_per_day * 365)
        if yearly_drift < 0.1:
            stability = 'Excellent'
        elif yearly_drift < 0.5:
            stability = 'Good'
        elif yearly_drift < 1.0:
            stability = 'Fair'
        else:
            stability = 'Poor'
        
        # Calculate uncertainty trend
        avg_uncertainty = sum(uncertainties) / len(uncertainties)
        uncertainty_trend = 'stable'
        if len(uncertainties) > 1:
            first_half_avg = sum(uncertainties[:n//2]) / (n//2)
            second_half_avg = sum(uncertainties[n//2:]) / (n - n//2)
            
            if second_half_avg > first_half_avg * 1.2:
                uncertainty_trend = 'increasing'
            elif second_half_avg < first_half_avg * 0.8:
                uncertainty_trend = 'decreasing'
        
        return {
            'drift_rate_per_day': drift_rate_per_day,
            'drift_rate_per_year': drift_rate_per_day * 365,
            'total_drift': total_drift,
            'total_time_days': total_time_days,
            'average_drift_rate': average_drift_rate,
            'stability_rating': stability,
            'error_range': error_range,
            'max_error': max_error,
            'min_error': min_error,
            'average_uncertainty': avg_uncertainty,
            'uncertainty_trend': uncertainty_trend,
            'number_of_calibrations': n,
            'first_calibration': dates[0],
            'last_calibration': dates[-1],
            'drift_equation': f"Error = {drift_rate_per_day:.6f} × Days + {drift_intercept:.6f}"
        }


class QualityAssurance:
    """Quality assurance utilities"""
    
    @staticmethod
    def validate_readings(readings, parameter):
        """Validate readings for quality issues"""
        issues = []
        
        if not readings or len(readings) < parameter.num_readings:
            issues.append("Insufficient number of readings")
            return issues
        
        # Use enhanced statistics function
        try:
            mean, std_dev, n = statistics_from_readings(readings)
        except ValueError as e:
            issues.append(str(e))
            return issues
        
        # Check for outliers using modified Z-score
        median = sorted(readings)[len(readings)//2]
        mad = sorted([abs(x - median) for x in readings])[len(readings)//2]
        
        if mad > 0:
            for i, reading in enumerate(readings):
                modified_z_score = 0.6745 * (reading - median) / mad
                if abs(modified_z_score) > 3.5:
                    issues.append(f"Potential outlier detected in reading {i+1}: {reading}")
        
        # Check for excessive variation
        if std_dev > float(parameter.tolerance) * 0.1:  # Std dev should be much smaller than tolerance
            issues.append("High variation in readings - check measurement conditions")
        
        # Check for systematic patterns
        if len(readings) >= 5:
            # Check for monotonic trend
            increasing = all(readings[i] <= readings[i+1] for i in range(len(readings)-1))
            decreasing = all(readings[i] >= readings[i+1] for i in range(len(readings)-1))
            
            if increasing or decreasing:
                issues.append("Systematic trend detected in readings - possible drift or warm-up issue")
        
        return issues
    
    @staticmethod
    def assess_measurement_capability(session):
        """Assess measurement capability and provide recommendations"""
        assessment = {
            'overall_rating': 'Good',
            'recommendations': [],
            'concerns': []
        }
        
        readings = session.readings.all()
        
        # Check pass rate
        total_points = readings.count()
        passed_points = readings.filter(passes_tolerance=True).count()
        pass_rate = (passed_points / total_points * 100) if total_points > 0 else 0
        
        if pass_rate < 90:
            assessment['overall_rating'] = 'Poor'
            assessment['concerns'].append(f"Low pass rate: {pass_rate:.1f}%")
            assessment['recommendations'].append("Review calibration procedure and measurement conditions")
        
        # Check uncertainty levels using enhanced uncertainty calculations
        high_uncertainty_count = 0
        for reading in readings:
            if hasattr(reading, 'get_readings_list'):
                readings_list = reading.get_readings_list()
                if readings_list:
                    uncertainty_budget = compute_uncertainty_budget(
                        readings=readings_list,
                        resolution=resolution_for_reading(reading),
                        reference_uncertainty=reading.parameter.reference_uncertainty or 0,
                        coverage_factor=reading.parameter.coverage_factor or 2,
                    )
                    
                    if uncertainty_budget and reading.parameter.tolerance:
                        uncertainty_ratio = uncertainty_budget['expanded'] / float(reading.parameter.tolerance)
                        if uncertainty_ratio > 0.25:  # Uncertainty > 25% of tolerance
                            high_uncertainty_count += 1
        
        if high_uncertainty_count > total_points * 0.3:  # More than 30% have high uncertainty
            assessment['concerns'].append("High measurement uncertainty relative to tolerance")
            assessment['recommendations'].append("Consider improving measurement resolution or reference standards")
        
        # Check repeatability
        poor_repeatability_count = 0
        for reading in readings:
            if hasattr(reading, 'get_readings_list'):
                readings_list = reading.get_readings_list()
                if readings_list and reading.parameter.tolerance:
                    try:
                        _, std_dev, _ = statistics_from_readings(readings_list)
                        repeatability_ratio = std_dev / float(reading.parameter.tolerance)
                        if repeatability_ratio > 0.1:  # Std dev > 10% of tolerance
                            poor_repeatability_count += 1
                    except ValueError:
                        continue
        
        if poor_repeatability_count > total_points * 0.2:  # More than 20% have poor repeatability
            assessment['concerns'].append("Poor measurement repeatability")
            assessment['recommendations'].append("Check measurement procedure, environmental conditions, and operator technique")
        
        # Environmental conditions check
        if hasattr(session, 'procedure') and session.procedure.temperature and session.actual_temperature:
            temp_diff = abs(float(session.actual_temperature) - float(session.procedure.temperature))
            if temp_diff > 2.0:  # More than 2°C difference
                assessment['concerns'].append(f"Temperature deviation: {temp_diff:.1f}°C from specified conditions")
                assessment['recommendations'].append("Improve environmental control or account for temperature effects")
        
        return assessment


# Four classes were removed here on 18 September 2026, all with zero callers:
#
#   ReportGenerator      — HTML certificate and budget report. Built on
#                          `reading.parameter.resolution`, a field that does not
#                          exist, so every method raised AttributeError.
#   TrendAnalysis        — monthly pass-rate and uncertainty slopes. Its only
#                          call site asked for `calculate_linear_regression`,
#                          a method the class never had.
#   CalibrationValidator — procedure and session completeness checks, never
#                          invoked on any path.
#   MetrologyUtils       — TUR, guard banding, interval estimation, k lookup and
#                          a degrees-of-freedom stub that returned the constant
#                          10. Every one of these is now a module-level function
#                          above, implemented properly and covered by tests:
#                          `test_uncertainty_ratio`, `guarded_decision`,
#                          `years_until_tolerance_breach`, `t_factor_95` and
#                          `effective_degrees_of_freedom`.
#
# The behaviour worth keeping was reimplemented first and is tested; what went
# was the code that could not run.

def _validate_environmental_conditions(temperature, humidity, pressure, procedure):
    """Validate environmental conditions against procedure requirements."""
    warnings = []
    if procedure.temperature and temperature:
        temp_diff = abs(float(temperature) - float(procedure.temperature))
        if temp_diff > 2.0:
            warnings.append(f"Temperature deviation: {temp_diff:.1f}°C from specified {procedure.temperature}°C")
    if procedure.humidity and humidity:
        humidity_diff = abs(float(humidity) - float(procedure.humidity))
        if humidity_diff > 10.0:
            warnings.append(f"Humidity deviation: {humidity_diff:.1f}%RH from specified {procedure.humidity}%RH")
    if procedure.pressure and pressure:
        pressure_diff = abs(float(pressure) - float(procedure.pressure))
        if pressure_diff > 1.0:
            warnings.append(f"Pressure deviation: {pressure_diff:.3f}kPa from specified {procedure.pressure}kPa")
    return warnings

