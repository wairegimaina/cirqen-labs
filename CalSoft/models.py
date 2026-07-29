import logging
import uuid
from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.contrib.auth import get_user_model
User = get_user_model()

from django.core.validators import MinValueValidator, MaxValueValidator
from django.forms import IntegerField
from django.urls import reverse
from django.utils.timezone import now
import json
import math
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from CalSoft.utils import CalibrationCalculator
from Inventory.models import Department, Equipment
from workshop.models import Workshop
import re
from django.db.models import Max

logger = logging.getLogger(__name__)

class CalibrationProcedure(models.Model):
    """Main calibration procedure with environmental conditions and metadata"""
    active_status = models.BooleanField(default=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)
    pending_delete = models.BooleanField(default=False)


    # Environmental conditions
    temperature = models.DecimalField(max_digits=5, decimal_places=2, default=23.0, help_text="°C")
    humidity = models.DecimalField(max_digits=5, decimal_places=2, default=50.0, help_text="%RH")
    pressure = models.DecimalField(max_digits=8, decimal_places=3, default=101.325, help_text="kPa")

    is_active = models.BooleanField(default=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:

        ordering = ['-created_at']
        verbose_name = "Calibration Procedure"
        verbose_name_plural = "Calibration Procedures"

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('calibration:procedure_detail', kwargs={'pk': self.pk})

    def get_standards_used(self):
        """
        Returns a list of standards referenced by this procedure's parameters
        with their associated parameter information
        """
        standards = []
        seen_standards = set()  # To avoid duplicates

        for param in self.parameters.all():
            if param.standard_reference and param.standard_reference not in seen_standards:
                try:
                    standard = Standard.objects.get(serial_number=param.standard_reference)
                    standards.append({
                        'parameter': param.name,
                        'standard': standard,
                        'parameter_unit': param.unit,
                        'parameter_tolerance': param.tolerance
                    })
                    seen_standards.add(param.standard_reference)
                except Standard.DoesNotExist:
                    logger.warning(f"Standard {param.standard_reference} not found for parameter {param.name}")

        return standards

    def get_parameters_with_standards(self):
        """
        Returns parameters with their associated standard information
        """
        parameters = []

        for param in self.parameters.all():
            param_data = {
                'id': param.id,
                'name': param.name,
                'unit': param.unit,
                'num_readings': param.num_readings,
                'tolerance': param.tolerance,
                'standard': None,
                'standard_uncertainty': None
            }

            if param.standard_reference:
                try:
                    standard = Standard.objects.get(serial_number=param.standard_reference)
                    param_data['standard'] = standard

                    # Get parameter-specific uncertainty if available
                    try:
                        standard_param = StandardParameter.objects.get(
                            standard=standard,
                            parameter__name=param.name
                        )
                        param_data['standard_uncertainty'] = standard_param.uncertainty
                    except StandardParameter.DoesNotExist:
                        pass

                except Standard.DoesNotExist:
                    param_data['standard_reference'] = param.standard_reference
                    param_data['standard_error'] = 'Standard not found in database'

            parameters.append(param_data)

        return parameters

    def get_required_equipment(self):
        """
        Returns equipment required to perform this calibration procedure
        """
        return Equipment.objects.filter(
            calsoft_procedure_mappings__calibration_procedure=self
        ).distinct()

    def get_recent_sessions(self, limit=5):
        """
        Returns recent calibration sessions using this procedure
        """
        return self.sessions.all().order_by('-timestamp')[:limit]

    def get_success_rate(self):
        """
        Calculates the success rate of this procedure's calibration sessions
        """
        total_sessions = self.sessions.count()
        if total_sessions == 0:
            return 0

        passed_sessions = self.sessions.filter(overall_pass=True).count()
        return (passed_sessions / total_sessions) * 100

    def get_average_duration(self):
        """
        Returns average duration of calibration sessions using this procedure
        """
        durations = self.sessions.annotate(
            duration=models.F('timestamp') - models.F('created_at')
        ).values_list('duration', flat=True)

        if not durations:
            return None

        total_seconds = sum(d.total_seconds() for d in durations if d)
        return total_seconds / len(durations)

    def clone_procedure(self, new_name=None, created_by=None):
        """
        Creates a clone of this procedure with all its parameters and sub-parameters
        """
        if not new_name:
            new_name = f"Copy of {self.name}"

        if not created_by:
            created_by = self.created_by

        # Create new procedure
        new_procedure = CalibrationProcedure.objects.create(
            name=new_name,
            description=self.description,
            created_by=created_by,
            temperature=self.temperature,
            humidity=self.humidity,
            pressure=self.pressure
        )

        # Clone parameters
        for param in self.parameters.all():
            new_param = CalibrationParameter.objects.create(
                procedure=new_procedure,
                name=param.name,
                unit=param.unit,
                num_readings=param.num_readings,
                standard_reference=param.standard_reference,
                reference_uncertainty=param.reference_uncertainty,
                coverage_factor=param.coverage_factor,
                tolerance=param.tolerance,
                order=param.order
            )

            # Clone sub-parameters
            for sub_param in param.sub_parameters.all():
                SubParameter.objects.create(
                    parameter=new_param,
                    name=sub_param.name,
                    tolerance=sub_param.tolerance,
                    order=sub_param.order
                )

            # Clone set values
            for set_value in param.set_values.all():
                SetValue.objects.create(
                    parameter=new_param,
                    sub_parameter=None,  # Will be set below if needed
                    value=set_value.value,
                    order=set_value.order
                )

                # For set values with sub-parameters, we need to set them after creation
                if set_value.sub_parameter:
                    corresponding_sub_param = new_param.sub_parameters.get(
                        name=set_value.sub_parameter.name
                    )
                    set_value.sub_parameter = corresponding_sub_param
                    set_value.save()

        return new_procedure

    def validate_environmental_conditions(self, temperature, humidity, pressure):
        """
        Validates if environmental conditions meet procedure requirements
        Returns list of warning messages if conditions are outside recommended ranges
        """
        warnings = []

        if self.temperature and temperature:
            temp_diff = abs(float(temperature) - float(self.temperature))
            if temp_diff > 2.0:
                warnings.append(
                    f"Temperature deviation: {temp_diff:.1f}°C from recommended {self.temperature}°C"
                )

        if self.humidity and humidity:
            humidity_diff = abs(float(humidity) - float(self.humidity))
            if humidity_diff > 10.0:
                warnings.append(
                    f"Humidity deviation: {humidity_diff:.1f}%RH from recommended {self.humidity}%RH"
                )

        if self.pressure and pressure:
            pressure_diff = abs(float(pressure) - float(self.pressure))
            if pressure_diff > 1.0:
                warnings.append(
                    f"Pressure deviation: {pressure_diff:.3f}kPa from recommended {self.pressure}kPa"
                )

        return warnings

    def get_calibration_interval(self):
        """
        Returns the typical calibration interval for equipment using this procedure
        """
        mappings = self.equipment_mappings.all()
        if not mappings:
            return None

        # Get the most common interval
        intervals = mappings.values_list('calibration_period', flat=True)
        return max(set(intervals), key=intervals.count)

    def get_uncertainty_budget(self):
        """
        Returns an uncertainty budget analysis for this procedure
        """
        budget = []

        for param in self.parameters.all():
            param_data = {
                'parameter': param.name,
                'unit': param.unit,
                'reference_uncertainty': param.reference_uncertainty,
                'type_a_sources': [],
                'type_b_sources': []
            }

            # Type A sources (from historical data)
            if param.standard_reference:
                param_data['type_b_sources'].append({
                    'source': 'Reference Standard',
                    'value': param.reference_uncertainty
                })

            # Add resolution as Type B source
            param_data['type_b_sources'].append({
                'source': 'Instrument Resolution',
                'value': None  # Will be set during actual calibration
            })

            budget.append(param_data)

        return budget


class CalibrationParameter(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)
    procedure = models.ForeignKey('CalibrationProcedure', on_delete=models.CASCADE, related_name='parameters')
    name = models.CharField(max_length=100)
    unit = models.CharField(max_length=20, blank=True)
    num_readings = models.PositiveIntegerField(default=5, validators=[MinValueValidator(3), MaxValueValidator(20)])
    standard_reference = models.CharField(max_length=200, help_text="Reference standard identification")
    reference_uncertainty = models.DecimalField(max_digits=10, decimal_places=6, default=0.001, help_text="k=2")
    coverage_factor = models.DecimalField(max_digits=3, decimal_places=1, default=2.0)
    tolerance = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True, help_text="±tolerance")
    order = models.PositiveIntegerField(default=0)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)


    syncable = True  # <- important, so sync task knows to sync this model
    class Meta:

        ordering = ['order', 'name']

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.unit})"


class SubParameter(models.Model):
    """Sub-parameters (e.g., systolic/diastolic) within a calibration parameter"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parameter = models.ForeignKey(CalibrationParameter, on_delete=models.CASCADE, related_name='sub_parameters')
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)
    name = models.CharField(max_length=100)
    tolerance = models.DecimalField(max_digits=10, decimal_places=6, default=1.0)
    order = models.PositiveIntegerField(default=0)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:

        ordering = ['order', 'id']

    @property
    def unit(self):
        """Derive unit from parent CalibrationParameter"""
        return self.parameter.unit

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.parameter.name} - {self.name}"


class SetValue(models.Model):
    """Set values (test points) for each parameter or sub-parameter"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parameter = models.ForeignKey(CalibrationParameter, on_delete=models.CASCADE, related_name='set_values')
    sub_parameter = models.ForeignKey(SubParameter, on_delete=models.CASCADE, related_name='set_values', null=True, blank=True)
    value = models.DecimalField(max_digits=15, decimal_places=6)
    order = models.PositiveIntegerField(default=0)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)


    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    syncable = True  # <- important, so sync task knows to sync this model
    class Meta:

        ordering = ['order', 'value']

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.parameter.name}: {self.value}"


class CalibrationSession(models.Model):
    """A complete calibration session"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(
        'calSchedules.CalibrationSchedule',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sessions'
    )
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    status = models.CharField(
        max_length=100,
        choices=[
            ('pending_review', 'Pending Review'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('approved_pending_certificate', 'Approved - Pending Certificate')
        ],
        default='pending_review'
    )
    approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="approved_sessions"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(
        max_length=100, blank=True, null=True,
        choices=[
            ('data_quality', 'Data Quality Issues'),
            ('procedure_not_followed', 'Procedure Not Followed'),
            ('equipment_error', 'Equipment Error'),
            ('environmental_conditions', 'Environmental Conditions'),
            ('other', 'Other')
        ]
    )
    rejection_comments = models.TextField(blank=True, null=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="rejected_sessions"
    )

    procedure = models.ForeignKey(CalibrationProcedure, on_delete=models.CASCADE, related_name='sessions')
    performed_by = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)

    device_model = models.CharField(max_length=200, blank=True)
    device_serial = models.CharField(max_length=100, blank=True)
    device_manufacturer = models.ForeignKey(
        'Inventory.Manufacturer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    device_description = models.ForeignKey(
        'Inventory.EquipmentDescription',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    Department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    Department_name = models.CharField(max_length=100, blank=True)
    workshop_name = models.CharField(max_length=100, blank=True)

    actual_temperature = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    actual_humidity = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    actual_pressure = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)

    overall_pass = models.BooleanField(default=False)
    certificate_number = models.CharField(max_length=500, unique=True, blank=True, null=True)

    next_calibration_due = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:
        ordering = ['-timestamp']
        permissions = [
            ("approve_session", "Can approve calibration sessions"),
        ]

    @classmethod
    def generate_certificate_number(cls):
        """Generate the next unique sequential certificate number safely.

        The DB read (locked, to serialise concurrent minting) stays here; the
        pure parse/increment lives in ``calSchedules.grouping.next_certificate_number``
        so it can be unit-tested without a database.
        """
        # Local import avoids a circular import: calSchedules.models imports
        # CalSoft.models, and grouping imports calSchedules.models.
        from calSchedules.grouping import next_certificate_number

        prefix_pattern = "BNH-"

        with transaction.atomic():
            last_cert = (
                cls.objects.select_for_update()
                .filter(certificate_number__startswith=prefix_pattern)
                .exclude(certificate_number__exact="")
                .order_by("-certificate_number")
                .values_list("certificate_number", flat=True)
                .first()
            )

            return next_certificate_number(last_cert, prefix=prefix_pattern)

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)


class SessionParameterResolution(models.Model):
    """Stores parameter-specific resolutions for a calibration session"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(CalibrationSession, on_delete=models.CASCADE, related_name='parameter_resolutions')
    parameter = models.ForeignKey(CalibrationParameter, on_delete=models.CASCADE)
    resolution = models.DecimalField(max_digits=10, decimal_places=6, help_text="Resolution for this parameter in this session")
    active_status = models.BooleanField(default=True)

    # offline sync


    pending_delete = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    syncable = True  # <- important, so sync task knows to sync this model
    class Meta:

        unique_together = ['session', 'parameter']

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.session} - {self.parameter.name}: {self.resolution}"


class CalibrationReading(models.Model):
    """Individual readings for each set value"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    active_status = models.BooleanField(default=True)
    session = models.ForeignKey('CalibrationSession', on_delete=models.CASCADE, related_name='readings')
    parameter = models.ForeignKey('CalibrationParameter', on_delete=models.CASCADE)
    sub_parameter = models.ForeignKey('SubParameter', on_delete=models.CASCADE, null=True, blank=True)
    set_value = models.ForeignKey('SetValue', on_delete=models.CASCADE)

    reading_1 = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    reading_2 = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    reading_3 = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    reading_4 = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    reading_5 = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    reading_6 = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    reading_7 = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    reading_8 = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    reading_9 = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    reading_10 = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)

    mean = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    standard_deviation = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    error = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)

    type_a_uncertainty = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    type_b_uncertainty = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    reference_uncertainty_component = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    combined_uncertainty = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    expanded_uncertainty = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)

    passes_tolerance = models.BooleanField(default=False)

    # offline sync
    pending_delete = models.BooleanField(default=False)
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    syncable = True  # <- important, so sync task knows to sync this model
    class Meta:

        unique_together = ['session', 'parameter', 'sub_parameter', 'set_value']

    def get_readings_list(self):
        """Return list of non-null readings as Decimal"""
        return [getattr(self, f'reading_{i}') for i in range(1, 11) if getattr(self, f'reading_{i}') is not None]

    def set_reading(self, index, value):
        try:
            decimal_value = Decimal(str(value))  # Handle both strings and numbers
            setattr(self, f'reading_{index}', decimal_value)
            self.save()  # Important: Save after each update!
        except (InvalidOperation, TypeError) as e:
            logger.error(f"Invalid reading_{index}: {value} - {str(e)}")

    def calculate_statistics(self):
        """Calculate all statistical measures and uncertainties using CalibrationCalculator"""
        calculator = CalibrationCalculator()
        readings = self.get_readings_list()

        if len(readings) < 2:
            logger.warning(f"Insufficient readings for reading {self.id}: {len(readings)} provided")
            self.mean = None
            self.standard_deviation = None
            self.error = None
            self.type_a_uncertainty = None
            self.type_b_uncertainty = None
            self.reference_uncertainty_component = None
            self.combined_uncertainty = None
            self.expanded_uncertainty = None
            self.passes_tolerance = False
            self.save()
            return False

        try:
            # Calculate statistics using CalibrationCalculator
            stats = calculator.calculate_statistics(readings)
            if not stats:
                logger.error(f"Statistics calculation failed for reading {self.id}")
                self.mean = None
                self.standard_deviation = None
                self.error = None
                self.type_a_uncertainty = None
                self.type_b_uncertainty = None
                self.reference_uncertainty_component = None
                self.combined_uncertainty = None
                self.expanded_uncertainty = None
                self.passes_tolerance = False
                self.save()
                return False

            self.mean = stats['mean'].quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
            self.standard_deviation = stats['std_dev'].quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
            self.error = (self.set_value.value - self.mean).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)

            # Get session-specific resolution
            try:
                session_resolution = self.session.parameter_resolutions.get(parameter=self.parameter).resolution
            except SessionParameterResolution.DoesNotExist:
                logger.error(f"No resolution defined for parameter {self.parameter.name} in session {self.session.id}")
                raise ValueError("No resolution defined for parameter in this session")

            # Calculate uncertainties
            self.type_a_uncertainty = calculator.calculate_type_a_uncertainty(
                self.standard_deviation, stats['count']
            ).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)

            self.type_b_uncertainty = calculator.calculate_type_b_uncertainty(
                session_resolution
            ).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)

            ref_unc = self.parameter.reference_uncertainty or Decimal('0')
            cov_factor = Decimal(str(self.parameter.coverage_factor or 2))
            self.reference_uncertainty_component = (ref_unc / cov_factor).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)

            self.combined_uncertainty = calculator.calculate_combined_uncertainty(
                self.type_a_uncertainty, self.type_b_uncertainty, self.reference_uncertainty_component
            ).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)

            self.expanded_uncertainty = calculator.calculate_expanded_uncertainty(
                self.combined_uncertainty, cov_factor
            ).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)

            # Check tolerance
            tolerance = self.sub_parameter.tolerance if self.sub_parameter else self.parameter.tolerance
            if tolerance is None:
                logger.error(f"No tolerance defined for parameter {self.parameter.name}")
                raise ValueError("Tolerance must be defined for parameters")
            self.passes_tolerance = calculator.check_tolerance(self.error, tolerance)

            self.save()
            logger.info(f"Statistics calculated for reading {self.id}: mean={self.mean}, expanded_uncertainty={self.expanded_uncertainty}")
            return True

        except Exception as e:
            logger.error(f"Error calculating statistics for reading {self.id}: {str(e)}", exc_info=True)
            self.mean = None
            self.standard_deviation = None
            self.error = None
            self.type_a_uncertainty = None
            self.type_b_uncertainty = None
            self.reference_uncertainty_component = None
            self.combined_uncertainty = None
            self.expanded_uncertainty = None
            self.passes_tolerance = False
            self.save()
            return False

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.parameter.name} @ {self.set_value.value}: {self.mean}"


class CalibrationReport(models.Model):
    """Generated calibration reports"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.OneToOneField(CalibrationSession, on_delete=models.CASCADE, related_name='report')
    generated_by = models.ForeignKey(User, on_delete=models.CASCADE)
    generated_date = models.DateTimeField(auto_now_add=True)
    active_status = models.BooleanField(default=True)

    report_content = models.TextField()
    report_type = models.CharField(max_length=20, choices=[
        ('html', 'HTML Report'),
        ('pdf', 'PDF Certificate'),
        ('csv', 'CSV Data Export'),
        ('json', 'JSON Data Export'),
    ], default='html')

    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_reports')
    approved_date = models.DateTimeField(null=True, blank=True)

    # offline sync
    pending_delete = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    syncable = True  # <- important, so sync task knows to sync this model

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Report for {self.session.certificate_number}"


class HistoricalCalibration(models.Model):
    """Track calibration history for drift analysis"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    active_status = models.BooleanField(default=True)
    device_serial = models.CharField(max_length=100)
    parameter_name = models.CharField(max_length=100)
    sub_parameter_name = models.CharField(max_length=100, blank=True)
    set_value = models.DecimalField(max_digits=15, decimal_places=6)
    measured_value = models.DecimalField(max_digits=15, decimal_places=6)
    error = models.DecimalField(max_digits=15, decimal_places=6)
    uncertainty = models.DecimalField(max_digits=15, decimal_places=6)
    calibration_date = models.DateTimeField()
    pending_delete = models.BooleanField(default=False)


    # offline sync

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    syncable = True  # <- important, so sync task knows to sync this model
    class Meta:

        ordering = ['device_serial', 'parameter_name', 'sub_parameter_name', 'calibration_date']
        indexes = [
            models.Index(fields=['device_serial', 'parameter_name', 'sub_parameter_name']),
            models.Index(fields=['calibration_date']),
        ]

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.device_serial} - {self.parameter_name} ({self.calibration_date.date()})"


class ParameterCategory(models.Model):
    """Category for parameters (e.g., Electrical, Pressure, Temperature)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)


    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:

        verbose_name_plural = "Parameter Categories"

    def __str__(self):
        return self.name


class Parameter(models.Model):
    """Measurement parameters that can be used in standards"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=10, blank=True)
    unit = models.CharField(max_length=20)
    description = models.TextField(blank=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    category = models.ForeignKey(ParameterCategory, on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:

        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.unit})"


class StandardType(models.Model):
    """Type of standard (e.g., Multifunction Calibrator, Pressure Standard)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    pending_delete = models.BooleanField(default=False)

    description = models.TextField(blank=True)
    parameters = models.ManyToManyField(Parameter, blank=True)
    active_status = models.BooleanField(default=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    syncable = True  # <- important, so sync task knows to sync this model


    def __str__(self):
        return self.name


class Standard(models.Model):
    """Reference standard equipment"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)
    model_number = models.CharField(max_length=100)
    serial_number = models.CharField(max_length=100, unique=True)
    manufacturer = models.CharField(max_length=100)
    certificate_number = models.CharField(max_length=100)
    calibration_date = models.DateField()
    calibration_due_date = models.DateField()
    calibration_agency = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)

    # offline sync

    updated_at = models.DateTimeField(auto_now=True)
    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:

        ordering = ['name', 'serial_number']

    def save(self, *args, **kwargs):
        if not self.certificate_number:
            self.certificate_number = f"STD-{self.serial_number}-{self.calibration_date.strftime('%Y%m%d')}"
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.model_number} - {self.serial_number})"


class StandardParameter(models.Model):
    """Parameters associated with a calibration standard"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    standard = models.ForeignKey(Standard, on_delete=models.CASCADE)
    parameter = models.ForeignKey(Parameter, on_delete=models.CASCADE)
    uncertainty = models.DecimalField(max_digits=10, decimal_places=6)
    active_status = models.BooleanField(default=True)

    # offline sync
    pending_delete = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:

        ordering = ['parameter__name']
        unique_together = ['standard', 'parameter']

    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.standard.name} - {self.parameter.name}"


class CalibrationSchedule(models.Model):
    """Schedule for equipment calibration"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name='calsoft_schedules')
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='calsoft_schedules', help_text="Workshop managing this calibration schedule")
    calibration_procedure = models.ForeignKey(CalibrationProcedure, on_delete=models.SET_NULL, null=True, blank=True, related_name='schedules')
    calibration_session = models.ForeignKey(CalibrationSession, on_delete=models.SET_NULL, null=True, blank=True, related_name='schedules')
    scheduled_month = models.DateField()
    status = models.CharField(max_length=20, choices=[
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('pushed', 'Pushed'),
        ('in_progress', 'In Progress'),
    ], default='pending')
    planning_logic = models.CharField(
        max_length=50,
        choices=[
            ('description_based', 'Description Based'),
            ('date_based', 'Date Based'),
        ],
        null=True,
        blank=True,
        help_text="Logic used to plan this schedule (e.g., description-based)"
    )
    calibration_period = models.PositiveIntegerField(
        choices=[
            (6, '6 Months'),
            (12, '12 Months'),
        ],
        default=12,
        help_text="Calibration interval in months"
    )
    estimated_duration = models.DurationField(null=True, blank=True, help_text="Estimated duration for the calibration procedure")
    created_at = models.DateTimeField(auto_now_add=True)

    active_status = models.BooleanField(default=True)
    # offline sync
    pending_delete = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)
    syncable = True  # <- important, so sync task knows to sync this model

    class Meta:

        unique_together = ['equipment', 'scheduled_month']
        indexes = [
            models.Index(fields=['workshop', 'scheduled_month']),
            models.Index(fields=['equipment', 'status']),
        ]

    def save(self, *args, **kwargs):
        if not self.workshop_id and self.equipment_id:
            self.workshop = self.equipment.workshop
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    @property
    def manufacturer(self):
        return self.equipment.manufacturer

    @property
    def model(self):
        return self.equipment.model

    @property
    def serial_number(self):
        return self.equipment.serial_number

    @property
    def days_until_due(self):
        return (self.scheduled_month - now().date()).days if self.scheduled_month else None

    def __str__(self):
        return f"Calibration Schedule for {self.equipment.description} on {self.scheduled_month}"


class CalibrationAuditLog(models.Model):
    """Audit log for calibration-related actions"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calsoft_audit_logs"   # 👈 unique name
    )
    action = models.CharField(max_length=50)
    description = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    schedule = models.ForeignKey('calSchedules.CalibrationSchedule', on_delete=models.SET_NULL, null=True, blank=True, related_name='calsoft_audit_logs')
    equipment = models.ForeignKey(Equipment, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    session = models.ForeignKey('CalibrationSession', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    active_status = models.BooleanField(default=True)

    pending_delete = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    syncable = True  # <- important, so sync task knows to sync this model



    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.action} - {self.timestamp}"


class EquipmentCalibrationProcedure(models.Model):
    """Mapping between equipment and calibration procedures"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name='calsoft_procedure_mappings')
    calibration_procedure = models.ForeignKey(CalibrationProcedure, on_delete=models.CASCADE, related_name='equipment_mappings')
    active_status = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    estimated_duration = models.DurationField(null=True, blank=True)
    required_standards = models.ManyToManyField(Standard, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='calsoft_equipment_procedure_mappings')
    created_at = models.DateTimeField(auto_now_add=True)

    # offline sync
    pending_delete = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)
    syncable = True  # <- important, so sync task knows to sync this model


    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.equipment.description} - {self.calibration_procedure.name}"


class CalibrationWorkflow(models.Model):
    """Workflow steps for a calibration schedule"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(CalibrationSchedule, on_delete=models.CASCADE, related_name='workflow_steps')
    step_name = models.CharField(max_length=100)
    step_order = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=[
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('skipped', 'Skipped'),
        ('failed', 'Failed'),
    ], default='pending')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='calsoft_workflow_steps')
    active_status = models.BooleanField(default=True)
    # offline sync
    pending_delete = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    syncable = True  # <- important, so sync task knows to sync this model


    def save(self, *args, **kwargs):
        self.needs_sync = True  # mark for sync
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.step_name} for Schedule {self.schedule.id}"


class CalibrationNotification(models.Model):
    """Notifications for calibration events"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='calsoft_notifications')
    notification_type = models.CharField(max_length=50)
    title = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    action_url = models.URLField(max_length=500, blank=True)

    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)
    syncable = True  # <- important, so sync task knows to sync this model


    def __str__(self):
        return f"{self.title} for {self.recipient.username}"


#=====================generate certificate number online only logic =============
class PendingCertificate(models.Model):
    """Track sessions approved offline that need certificate numbers from HQ"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.OneToOneField(
        CalibrationSession,
        on_delete=models.CASCADE,
        related_name='pending_certificate'
    )
    machine_id = models.CharField(max_length=100)
    sync_status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('processing', 'Processing'),
            ('completed', 'Completed'),
            ('failed', 'Failed')
        ],
        default='pending'
    )
    retry_count = models.IntegerField(default=0)
    last_attempt = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)  # Keep only one
    updated_at = models.DateTimeField(auto_now=True)  # Critical for last-write-wins

    syncable = True

    class Meta:
        db_table = 'pending_certificates'
        ordering = ['-updated_at']  # Order by updated_at for conflict resolution

    def __str__(self):
        return f"Pending Cert for {self.session.id} - {self.sync_status}"
