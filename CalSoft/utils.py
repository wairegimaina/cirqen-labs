# utils.py
import math
from decimal import Decimal, ROUND_HALF_UP, getcontext
from datetime import datetime, timedelta
from django.utils import timezone
from django.template.loader import render_to_string
import logging

# Set decimal precision high enough for calculations
getcontext().prec = 28

logger = logging.getLogger(__name__)


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
    
    vals = [Decimal(str(x)) for x in readings]
    mean = sum(vals) / Decimal(n)
    
    if n == 1:
        sample_std = Decimal('0')
    else:
        ss = sum((x - mean) ** 2 for x in vals)
        sample_var = ss / Decimal(n - 1)
        sample_std = sample_var.sqrt()
    
    return float(mean), float(sample_std), n


def calculate_uncertainties(
    readings,
    resolution,
    reference_uncertainty,
    reference_is_expanded=False,
    k=2
):
    """
    Calculate Type A, Type B (resolution + reference), combined standard uncertainty, and expanded uncertainty.
    
    Args:
        readings (list): Measurement readings.
        resolution (float): Instrument resolution in measurement units.
        reference_uncertainty (float): Reference uncertainty (standard or expanded).
        reference_is_expanded (bool): If True, 'reference_uncertainty' is expanded and must be divided by k.
        k (float): Coverage factor for expanded uncertainty.
        
    Returns:
        dict: Contains all uncertainty components and summary statistics.
    """
    try:
        # 1. Mean and sample std
        mean, sample_std, n = statistics_from_readings(readings)
        
        # 2. Type A uncertainty (standard uncertainty of the mean)
        u_A = sample_std / math.sqrt(n) if n > 0 else 0.0
        
        # 3. Resolution standard uncertainty (rectangular distribution)
        u_res = float(Decimal(str(resolution)) / Decimal(str(math.sqrt(12))))
        
        # 4. Reference standard uncertainty
        if reference_is_expanded:
            u_ref = float(Decimal(str(reference_uncertainty)) / Decimal(str(k)))
        else:
            u_ref = float(Decimal(str(reference_uncertainty)))
        
        # 5. Combined standard uncertainty
        u_combined = math.sqrt(u_A**2 + u_res**2 + u_ref**2)
        
        # 6. Expanded uncertainty
        U_expanded = k * u_combined
        
        return {
            "mean": mean,
            "sample_std": sample_std,
            "n": n,
            "u_A": u_A,
            "u_res": u_res,
            "u_ref": u_ref,
            "u_combined": u_combined,
            "U_expanded": U_expanded,
            "k": k,
            "reference_is_expanded": reference_is_expanded
        }
    except Exception as e:
        logger.error(f"Error in calculate_uncertainties: {str(e)}", exc_info=True)
        return None

# utils.py
import math
from decimal import Decimal, getcontext, ROUND_HALF_UP

getcontext().prec = 12  # high precision

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

        return {
            'max_linearity_error_units': max_err_units,
            'max_linearity_error_percent': max_err_percent,
            'errors_units': errors_units,
            'errors_percent': errors_percent
        }


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


class ReportGenerator:
    """Generate calibration reports and certificates"""
    
    @staticmethod
    def generate_certificate(session):
        """Generate HTML certificate content"""
        # Calculate overall statistics
        readings = session.readings.all()
        total_points = readings.count()
        passed_points = readings.filter(passes_tolerance=True).count()
        pass_rate = (passed_points / total_points * 100) if total_points > 0 else 0
        
        # Group readings by parameter
        readings_by_parameter = {}
        for reading in readings:
            param_name = reading.parameter.name
            if param_name not in readings_by_parameter:
                readings_by_parameter[param_name] = []
            readings_by_parameter[param_name].append(reading)
        
        # Calculate linearity for each parameter
        linearity_analysis = {}
        for param_name, param_readings in readings_by_parameter.items():
            if len(param_readings) >= 3:
                set_values = [float(r.set_value.value) for r in param_readings]
                errors = [float(r.error) if r.error else 0 for r in param_readings]
                linearity = CalibrationCalculator.calculate_linearity(set_values, errors)
                if linearity:
                    linearity_analysis[param_name] = linearity
        
        context = {
            'session': session,
            'readings_by_parameter': readings_by_parameter,
            'linearity_analysis': linearity_analysis,
            'total_points': total_points,
            'passed_points': passed_points,
            'pass_rate': pass_rate,
            'generation_date': timezone.now(),
        }
        
        return render_to_string('calibration/certificate_template.html', context)
    
    @staticmethod
    def generate_pdf_certificate(session):
        """Generate PDF certificate (placeholder - would use WeasyPrint or similar)"""
        # This would integrate with a PDF generation library
        # For example, using WeasyPrint:
        # from weasyprint import HTML, CSS
        # 
        # html_content = ReportGenerator.generate_certificate(session)
        # pdf = HTML(string=html_content).write_pdf()
        # return pdf
        
        # For now, return a placeholder
        return b"PDF generation would be implemented here using a library like WeasyPrint or ReportLab"
    
    @staticmethod
    def generate_uncertainty_budget_report(session):
        """Generate detailed uncertainty budget report"""
        readings = session.readings.select_related('parameter', 'set_value')
        
        budget_data = []
        for reading in readings:
            if reading.mean is not None:
                # Calculate uncertainty components using the enhanced function
                readings_list = reading.get_readings_list() if hasattr(reading, 'get_readings_list') else []
                
                if readings_list:
                    uncertainty_budget = calculate_uncertainties(
                        readings=readings_list,
                        resolution=float(reading.parameter.resolution) if reading.parameter.resolution else 1.0,
                        reference_uncertainty=float(reading.parameter.reference_uncertainty) if reading.parameter.reference_uncertainty else 0.0,
                        reference_is_expanded=getattr(reading.parameter, 'reference_is_expanded', False),
                        k=2
                    )
                    
                    if uncertainty_budget:
                        budget_item = {
                            'reading': reading,
                            'type_a': uncertainty_budget['u_A'],
                            'type_b': uncertainty_budget['u_res'],
                            'reference': uncertainty_budget['u_ref'],
                            'combined': uncertainty_budget['u_combined'],
                            'expanded': uncertainty_budget['U_expanded'],
                            'type_a_percent': (uncertainty_budget['u_A'] / uncertainty_budget['u_combined'] * 100) if uncertainty_budget['u_combined'] > 0 else 0,
                            'type_b_percent': (uncertainty_budget['u_res'] / uncertainty_budget['u_combined'] * 100) if uncertainty_budget['u_combined'] > 0 else 0,
                            'reference_percent': (uncertainty_budget['u_ref'] / uncertainty_budget['u_combined'] * 100) if uncertainty_budget['u_combined'] > 0 else 0,
                        }
                        budget_data.append(budget_item)
        
        context = {
            'session': session,
            'budget_data': budget_data,
            'generation_date': timezone.now(),
        }
        
        return render_to_string('calibration/uncertainty_budget_template.html', context)


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
                    uncertainty_budget = calculate_uncertainties(
                        readings=readings_list,
                        resolution=float(reading.parameter.resolution) if reading.parameter.resolution else 1.0,
                        reference_uncertainty=float(reading.parameter.reference_uncertainty) if reading.parameter.reference_uncertainty else 0.0
                    )
                    
                    if uncertainty_budget and reading.parameter.tolerance:
                        uncertainty_ratio = uncertainty_budget['U_expanded'] / float(reading.parameter.tolerance)
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


class TrendAnalysis:
    """Analyze trends in calibration data"""
    
    @staticmethod
    def analyze_procedure_trends(procedure, months=12):
        """Analyze trends for a specific procedure over time"""
        end_date = timezone.now()
        start_date = end_date - timedelta(days=30 * months)
        
        sessions = procedure.sessions.filter(
            timestamp__gte=start_date,
            timestamp__lte=end_date
        ).order_by('timestamp')
        
        if sessions.count() < 3:
            return None
        
        # Calculate monthly statistics
        monthly_stats = []
        current_date = start_date.replace(day=1)
        
        while current_date < end_date:
            next_month = (current_date.replace(day=28) + timedelta(days=4)).replace(day=1)
            
            month_sessions = sessions.filter(
                timestamp__gte=current_date,
                timestamp__lt=next_month
            )
            
            if month_sessions.exists():
                total_sessions = month_sessions.count()
                passed_sessions = month_sessions.filter(overall_pass=True).count()
                pass_rate = (passed_sessions / total_sessions * 100) if total_sessions > 0 else 0
                
                # Calculate average uncertainty using enhanced calculations
                uncertainties = []
                for session in month_sessions:
                    for reading in session.readings.all():
                        if hasattr(reading, 'get_readings_list'):
                            readings_list = reading.get_readings_list()
                            if readings_list:
                                uncertainty_budget = calculate_uncertainties(
                                    readings=readings_list,
                                    resolution=float(reading.parameter.resolution) if reading.parameter.resolution else 1.0,
                                    reference_uncertainty=float(reading.parameter.reference_uncertainty) if reading.parameter.reference_uncertainty else 0.0
                                )
                                if uncertainty_budget:
                                    uncertainties.append(uncertainty_budget['U_expanded'])
                
                avg_uncertainty = sum(uncertainties) / len(uncertainties) if uncertainties else 0
                
                monthly_stats.append({
                    'month': current_date,
                    'total_sessions': total_sessions,
                    'pass_rate': pass_rate,
                    'avg_uncertainty': avg_uncertainty
                })
            
            current_date = next_month
        
        if len(monthly_stats) < 3:
            return None
        
        # Calculate trends
        pass_rate_trend = TrendAnalysis._calculate_trend([stat['pass_rate'] for stat in monthly_stats])
        uncertainty_trend = TrendAnalysis._calculate_trend([stat['avg_uncertainty'] for stat in monthly_stats])
        
        return {
            'monthly_stats': monthly_stats,
            'pass_rate_trend': pass_rate_trend,
            'uncertainty_trend': uncertainty_trend,
            'analysis_period': f"{start_date.strftime('%Y-%m')} to {end_date.strftime('%Y-%m')}"
        }
    
    @staticmethod
    def _calculate_trend(values):
        """Calculate trend (slope) for a list of values"""
        if len(values) < 2:
            return 0
        
        n = len(values)
        x_values = list(range(n))
        
        sum_x = sum(x_values)
        sum_y = sum(values)
        sum_xy = sum(x * y for x, y in zip(x_values, values))
        sum_xx = sum(x * x for x in x_values)
        
        denominator = n * sum_xx - sum_x * sum_x
        if denominator == 0:
            return 0
        
        slope = (n * sum_xy - sum_x * sum_y) / denominator
        return slope


class CalibrationValidator:
    """Validate calibration data and procedures"""
    
    @staticmethod
    def validate_procedure(procedure):
        """Validate a calibration procedure for completeness and consistency"""
        errors = []
        warnings = []
        
        # Check basic information
        if not procedure.name.strip():
            errors.append("Procedure name is required")
        
        if not procedure.parameters.exists():
            errors.append("At least one parameter is required")
        
        # Validate parameters
        for param in procedure.parameters.all():
            if not param.name.strip():
                errors.append(f"Parameter name is required")
            
            if param.num_readings < 3:
                warnings.append(f"Parameter '{param.name}': Less than 3 readings may provide poor statistics")
            
            if param.resolution <= 0:
                errors.append(f"Parameter '{param.name}': Resolution must be positive")
            
            if param.tolerance <= 0:
                errors.append(f"Parameter '{param.name}': Tolerance must be positive")
            
            if param.reference_uncertainty < 0:
                errors.append(f"Parameter '{param.name}': Reference uncertainty cannot be negative")
            
            if not param.set_values.exists():
                errors.append(f"Parameter '{param.name}': At least one set value is required")
            
            # Check set value coverage
            set_values = list(param.set_values.values_list('value', flat=True))
            if len(set_values) < 3:
                warnings.append(f"Parameter '{param.name}': Consider using at least 3 set values for linearity analysis")
            
            # Check for reasonable spread in set values
            if len(set_values) > 1:
                value_range = max(set_values) - min(set_values)
                if value_range < float(param.tolerance) * 10:
                    warnings.append(f"Parameter '{param.name}': Set value range might be too small relative to tolerance")
        
        # Environmental conditions check
        if hasattr(procedure, 'temperature'):
            if procedure.temperature < -50 or procedure.temperature > 100:
                warnings.append("Temperature seems outside typical calibration range")
        
        if hasattr(procedure, 'humidity'):
            if procedure.humidity < 0 or procedure.humidity > 100:
                errors.append("Humidity must be between 0 and 100%")
        
        return {
            'is_valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }
    
    @staticmethod
    def validate_calibration_data(session):
        """Validate calibration session data"""
        errors = []
        warnings = []
        
        if not session.readings.exists():
            errors.append("No calibration readings found")
            return {'is_valid': False, 'errors': errors, 'warnings': warnings}
        
        for reading in session.readings.all():
            if hasattr(reading, 'get_readings_list'):
                readings_list = reading.get_readings_list()
            else:
                readings_list = []
            
            if len(readings_list) < reading.parameter.num_readings:
                errors.append(f"Insufficient readings for {reading.parameter.name} at {reading.set_value.value}")
            
            if len(readings_list) < 3:
                errors.append(f"Minimum 3 readings required for {reading.parameter.name} at {reading.set_value.value}")
            
            # Check for quality issues using enhanced validation
            if readings_list:
                qa_issues = QualityAssurance.validate_readings(readings_list, reading.parameter)
                warnings.extend(qa_issues)
        
        return {
            'is_valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }


class MetrologyUtils:
    """Metrology and measurement science utilities"""
    
    @staticmethod
    def calculate_measurement_capability(uncertainty, tolerance):
        """Calculate measurement capability ratio"""
        if tolerance <= 0:
            return float('inf')
        
        # Test Accuracy Ratio (TAR) or Test Uncertainty Ratio (TUR)
        # Should be >= 4:1 for good measurement capability
        return float(tolerance) / float(uncertainty)
    
    @staticmethod
    def estimate_calibration_interval(drift_rate, tolerance, target_probability=0.95):
        """Estimate optimal calibration interval based on drift rate"""
        if drift_rate <= 0:
            return 365  # Default to 1 year if no drift
        
        # Calculate time until drift might exceed tolerance
        # Using normal distribution assumption
        # P(|drift| < tolerance) = target_probability
        
        # For 95% probability, use 1.96 sigma
        z_score = 1.96 if target_probability == 0.95 else 2.58  # 99%
        
        # Assume drift has some uncertainty (e.g., 20% of the drift rate)
        drift_uncertainty = abs(drift_rate) * 0.2
        
        # Time when total uncertainty (drift + measurement) approaches tolerance
        allowable_drift = float(tolerance) / z_score
        optimal_interval_days = allowable_drift / abs(drift_rate) if drift_rate != 0 else 365
        
        # Limit to reasonable range (30 days to 3 years)
        optimal_interval_days = max(30, min(1095, optimal_interval_days))
        
        return int(optimal_interval_days)
    
    @staticmethod
    def calculate_guard_banding(measurement_uncertainty, tolerance):
        """Calculate guard band for pass/fail decisions"""
        # Guard band helps account for measurement uncertainty
        # Conservative approach: guard band = 2 * measurement uncertainty
        
        guard_band = 2 * float(measurement_uncertainty)
        effective_tolerance = float(tolerance) - guard_band
        
        return {
            'guard_band': guard_band,
            'effective_tolerance': max(0, effective_tolerance),
            'risk_reduction': (guard_band / float(tolerance)) * 100 if tolerance > 0 else 0
        }
    
    @staticmethod
    def convert_confidence_level_to_k_factor(confidence_level):
        """Convert confidence level to coverage factor"""
        # Standard coverage factors for normal distribution
        confidence_map = {
            68.27: 1.0,
            90.0: 1.645,
            95.0: 1.96,
            95.45: 2.0,
            99.0: 2.576,
            99.73: 3.0
        }
        
        # Find closest match
        closest_confidence = min(confidence_map.keys(), 
                               key=lambda x: abs(x - confidence_level))
        
        return confidence_map[closest_confidence]
    
    @staticmethod
    def calculate_degrees_of_freedom(type_a_uncertainty, type_b_components):
        """Calculate effective degrees of freedom using Welch-Satterthwaite equation"""
        # Simplified calculation for common case
        # In practice, this would be more complex with proper uncertainty budgets
        
        if type_a_uncertainty <= 0:
            return float('inf')  # Type B only
        
        # Assume Type A has n-1 degrees of freedom (from sample std dev)
        # Type B components typically have infinite degrees of freedom
        
        # For simplicity, return finite value based on Type A contribution
        return max(2, 10)  # Conservative estimate

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

