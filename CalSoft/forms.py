from django import forms
from django.forms import inlineformset_factory, formset_factory
from decimal import Decimal
import json
from .models import (
    CalibrationProcedure, CalibrationParameter, CalibrationSchedule, EquipmentCalibrationProcedure,Parameter, Standard, StandardParameter, StandardType, SubParameter, SetValue,
    CalibrationSession, CalibrationReading
)

class CalibrationProcedureForm(forms.ModelForm):
    """Form for creating/editing calibration procedures"""

    class Meta:
        model = CalibrationProcedure
        fields = ['name', 'description', 'temperature', 'humidity', 'pressure']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter procedure name'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Optional description'
            }),
            'temperature': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.1',
                'min': '-50',
                'max': '100'
            }),
            'humidity': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.1',
                'min': '0',
                'max': '100'
            }),
            'pressure': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.001',
                'min': '50',
                'max': '150'
            }),
        }

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if name:
            name = name.strip()
            if len(name) < 3:
                raise forms.ValidationError("Procedure name must be at least 3 characters long")
        return name

    def clean_humidity(self):
        humidity = self.cleaned_data.get('humidity')
        if humidity is not None and (humidity < 0 or humidity > 100):
            raise forms.ValidationError("Humidity must be between 0 and 100%")
        return humidity

class CalibrationParameterForm(forms.ModelForm):
    """Form for creating/editing calibration parameters"""

    class Meta:
        model = CalibrationParameter
        fields = [
            'name', 'unit', 'num_readings', 'standard_reference',
            'reference_uncertainty', 'coverage_factor', 'tolerance', 'order'
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Blood Pressure, Temperature'
            }),
            'unit': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., mmHg, °C, V'
            }),
            'num_readings': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '3',
                'max': '20',
                'value': '5'
            }),
            'standard_reference': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., NIST Standard XYZ'
            }),
            'reference_uncertainty': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.000001',
                'placeholder': '0.001'
            }),
            'coverage_factor': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.1',
                'value': '2.0'
            }),
            'tolerance': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.000001',
                'placeholder': '1.0'
            }),
            'order': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0'
            }),
        }

    def clean_num_readings(self):
        num_readings = self.cleaned_data.get('num_readings')
        if num_readings is not None and num_readings < 3:
            raise forms.ValidationError("Minimum 3 readings required for statistical analysis")
        return num_readings

    def clean_resolution(self):
        resolution = self.cleaned_data.get('resolution')
        if resolution is not None and resolution <= 0:
            raise forms.ValidationError("Resolution must be positive")
        return resolution

    def clean_tolerance(self):
        tolerance = self.cleaned_data.get('tolerance')
        if tolerance is not None and tolerance <= 0:
            raise forms.ValidationError("Tolerance must be positive")
        return tolerance

class SubParameterForm(forms.ModelForm):
    """Form for creating/editing sub-parameters"""

    class Meta:
        model = SubParameter
        fields = ['name', 'tolerance', 'order']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Systolic, Diastolic'
            }),
            'tolerance': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.000001',
                'placeholder': '1.0'
            }),
            'order': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0'
            }),
        }

class SetValueForm(forms.ModelForm):
    """Form for creating/editing set values"""

    class Meta:
        model = SetValue
        fields = ['value', 'order', 'sub_parameter']
        widgets = {
            'value': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.000001',
                'placeholder': 'Set value'
            }),
            'order': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0'
            }),
            'sub_parameter': forms.Select(attrs={
                'class': 'form-control'
            }),
        }

ParameterFormSet = inlineformset_factory(
    CalibrationProcedure,
    CalibrationParameter,
    form=CalibrationParameterForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True
)

SubParameterFormSet = inlineformset_factory(
    CalibrationParameter,
    SubParameter,
    form=SubParameterForm,
    extra=1,
    can_delete=True,
    min_num=0
)

SetValueFormSet = inlineformset_factory(
    CalibrationParameter,
    SetValue,
    form=SetValueForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True
)
class CalibrationSessionForm(forms.ModelForm):
    class Meta:
        model = CalibrationSession
        fields = ['device_model', 'device_serial', 'device_manufacturer', 'actual_temperature', 'actual_humidity', 'actual_pressure', 'notes']
        widgets = {
            'device_model': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter device model'}),
            'device_serial': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter serial number'}),
            'device_manufacturer': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter manufacturer'}),
            'actual_temperature': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'actual_humidity': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'actual_pressure': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Optional notes'}),
        }

class CalibrationReadingForm(forms.ModelForm):
    """Form for individual calibration readings"""
    resolution = forms.DecimalField(
        required=True,
        max_digits=10,
        decimal_places=6,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.000001',
            'placeholder': 'Enter resolution for this parameter'
        })
    )

    reading_1 = forms.DecimalField(
        required=False,
        max_digits=15,
        decimal_places=6,
        widget=forms.NumberInput(attrs={
            'class': 'form-control reading-input',
            'step': '0.000001',
            'placeholder': 'Reading 1'
        })
    )
    reading_2 = forms.DecimalField(
        required=False,
        max_digits=15,
        decimal_places=6,
        widget=forms.NumberInput(attrs={
            'class': 'form-control reading-input',
            'step': '0.000001',
            'placeholder': 'Reading 2'
        })
    )
    reading_3 = forms.DecimalField(
        required=False,
        max_digits=15,
        decimal_places=6,
        widget=forms.NumberInput(attrs={
            'class': 'form-control reading-input',
            'step': '0.000001',
            'placeholder': 'Reading 3'
        })
    )
    reading_4 = forms.DecimalField(
        required=False,
        max_digits=15,
        decimal_places=6,
        widget=forms.NumberInput(attrs={
            'class': 'form-control reading-input',
            'step': '0.000001',
            'placeholder': 'Reading 4'
        })
    )
    reading_5 = forms.DecimalField(
        required=False,
        max_digits=15,
        decimal_places=6,
        widget=forms.NumberInput(attrs={
            'class': 'form-control reading-input',
            'step': '0.000001',
            'placeholder': 'Reading 5'
        })
    )

    class Meta:
        model = CalibrationReading
        fields = [
            'resolution',
            'reading_1', 'reading_2', 'reading_3', 'reading_4', 'reading_5',
            'reading_6', 'reading_7', 'reading_8', 'reading_9', 'reading_10'
        ]

    def __init__(self, *args, **kwargs):
        parameter = kwargs.pop('parameter', None)
        sub_parameter = kwargs.pop('sub_parameter', None)
        super().__init__(*args, **kwargs)

        if parameter:
            num_readings = parameter.num_readings
            for i in range(1, 11):
                field_name = f'reading_{i}'
                if i <= num_readings:
                    self.fields[field_name].widget.attrs.update({
                        'required': True if i <= 3 else False
                    })
                else:
                    self.fields[field_name].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()

        resolution = cleaned_data.get('resolution')
        if resolution is None or resolution <= 0:
            raise forms.ValidationError("Resolution must be positive")

        readings = []
        for i in range(1, 11):
            field_name = f'reading_{i}'
            value = cleaned_data.get(field_name)
            if value is not None:
                readings.append(value)

        if len(readings) < 3:
            raise forms.ValidationError("At least 3 readings are required")

        return cleaned_data

class ProcedureSearchForm(forms.Form):
    """Form for searching procedures"""

    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search procedures...',
            'autocomplete': 'off'
        })
    )

    created_by = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label="All users",
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'class': 'form-control',
            'type': 'date'
        })
    )

    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'class': 'form-control',
            'type': 'date'
        })
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth.models import User
        self.fields['created_by'].queryset = User.objects.filter(
            calibrationprocedure__isnull=False
        ).distinct()

class SessionSearchForm(forms.Form):
    """Form for searching calibration sessions"""

    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search by device serial, model, or certificate number...'
        })
    )

    procedure = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label="All procedures",
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    performed_by = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label="All technicians",
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    pass_status = forms.ChoiceField(
        choices=[
            ('', 'All results'),
            ('pass', 'Passed only'),
            ('fail', 'Failed only'),
        ],
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'class': 'form-control',
            'type': 'date'
        })
    )

    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'class': 'form-control',
            'type': 'date'
        })
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth.models import User

        self.fields['procedure'].queryset = CalibrationProcedure.objects.filter(
            active_status=True
        ).order_by('name')

        self.fields['performed_by'].queryset = User.objects.filter(
            performed_sessions__isnull=False
        ).distinct().order_by('username')

class UncertaintyCalculationForm(forms.Form):
    """Form for manual uncertainty calculations"""

    readings = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Enter readings separated by commas (e.g., 10.001, 10.002, 9.999)'
        }),
        help_text="Enter measurement readings separated by commas"
    )

    resolution = forms.DecimalField(
        max_digits=10,
        decimal_places=6,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.000001',
            'placeholder': '0.001'
        }),
        help_text="Instrument resolution"
    )

    reference_uncertainty = forms.DecimalField(
        max_digits=10,
        decimal_places=6,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.000001',
            'placeholder': '0.001'
        }),
        help_text="Reference standard uncertainty (k=2)"
    )

    coverage_factor = forms.DecimalField(
        max_digits=3,
        decimal_places=1,
        initial=2.0,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.1',
            'value': '2.0'
        }),
        help_text="Coverage factor (typically 2 for 95% confidence)"
    )

    def clean_readings(self):
        readings_str = self.cleaned_data.get('readings', '')
        try:
            readings = [float(x.strip()) for x in readings_str.split(',') if x.strip()]

            if len(readings) < 3:
                raise forms.ValidationError("At least 3 readings are required")

            if len(readings) > 20:
                raise forms.ValidationError("Maximum 20 readings allowed")

            return readings
        except ValueError:
            raise forms.ValidationError("Invalid readings format. Use comma-separated numbers.")

class DataExportForm(forms.Form):
    """Form for data export options"""

    EXPORT_FORMATS = [
        ('csv', 'CSV (Comma Separated Values)'),
        ('json', 'JSON (JavaScript Object Notation)'),
        ('xlsx', 'Excel Spreadsheet'),
        ('pdf', 'PDF Report'),
    ]

    format = forms.ChoiceField(
        choices=EXPORT_FORMATS,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    include_raw_data = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Include individual measurement readings"
    )

    include_calculations = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Include calculated statistics and uncertainties"
    )

    include_metadata = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Include session metadata and environmental conditions"
    )

class QualityReviewForm(forms.Form):
    """Form for quality review and approval"""

    REVIEW_STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('needs_revision', 'Needs Revision'),
    ]

    review_status = forms.ChoiceField(
        choices=REVIEW_STATUS_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    review_comments = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Review comments and observations...'
        })
    )

    technical_review = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Technical review completed"
    )

    quality_review = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Quality review completed"
    )

    management_approval = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Management approval obtained"
    )

class BulkOperationForm(forms.Form):
    """Form for bulk operations on calibration data"""

    BULK_OPERATIONS = [
        ('export', 'Export Selected'),
        ('generate_reports', 'Generate Reports'),
        ('update_status', 'Update Status'),
        ('delete', 'Delete Selected'),
    ]

    operation = forms.ChoiceField(
        choices=BULK_OPERATIONS,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    selected_items = forms.CharField(
        widget=forms.HiddenInput(),
        help_text="Selected item IDs (comma-separated)"
    )

    confirmation = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="I confirm this bulk operation"
    )

class AdvancedAnalysisForm(forms.Form):
    """Form for advanced analysis options"""

    ANALYSIS_TYPES = [
        ('trend', 'Trend Analysis'),
        ('drift', 'Drift Analysis'),
        ('capability', 'Measurement Capability Study'),
        ('uncertainty_budget', 'Uncertainty Budget Analysis'),
        ('linearity', 'Linearity Analysis'),
        ('stability', 'Stability Analysis'),
    ]

    analysis_type = forms.ChoiceField(
        choices=ANALYSIS_TYPES,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    date_range_months = forms.IntegerField(
        initial=12,
        min_value=1,
        max_value=60,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '1',
            'max': '60'
        }),
        help_text="Analysis period in months"
    )

    confidence_level = forms.DecimalField(
        initial=95.0,
        max_digits=4,
        decimal_places=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.1',
            'min': '90.0',
            'max': '99.9'
        }),
        help_text="Confidence level for statistical analysis (%)"
    )

    include_environmental_factors = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Include environmental condition analysis"
    )

class CalibrationIntervalForm(forms.Form):
    """Form for calibration interval optimization"""

    target_reliability = forms.DecimalField(
        initial=95.0,
        max_digits=4,
        decimal_places=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.1',
            'min': '90.0',
            'max': '99.9'
        }),
        help_text="Target reliability percentage"
    )

    risk_tolerance = forms.ChoiceField(
        choices=[
            ('low', 'Low Risk (Conservative)'),
            ('medium', 'Medium Risk (Balanced)'),
            ('high', 'High Risk (Aggressive)'),
        ],
        initial='medium',
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    cost_per_calibration = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'placeholder': '0.00'
        }),
        help_text="Cost per calibration (optional for economic analysis)"
    )

    failure_cost = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'placeholder': '0.00'
        }),
        help_text="Cost of measurement failure (optional)"
    )

class TemplateImportForm(forms.Form):
    """Form for importing calibration templates"""

    template_file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': '.json,.csv,.xlsx'
        }),
        help_text="Upload JSON, CSV, or Excel file with procedure template"
    )

    import_options = forms.MultipleChoiceField(
        choices=[
            ('parameters', 'Import Parameters'),
            ('sub_parameters', 'Import Sub-Parameters'),
            ('set_values', 'Import Set Values'),
            ('environmental', 'Import Environmental Conditions'),
            ('uncertainties', 'Import Uncertainty Values'),
        ],
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        initial=['parameters', 'sub_parameters', 'set_values']
    )

    overwrite_existing = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Overwrite existing procedures with same name"
    )

def create_reading_formset(parameter, sub_parameter=None):
    """Create a formset for readings based on parameter requirements"""

    class ReadingForm(forms.Form):
        reading = forms.DecimalField(
            max_digits=15,
            decimal_places=6,
            widget=forms.NumberInput(attrs={
                'class': 'form-control reading-input',
                'step': str(sub_parameter.resolution if sub_parameter else parameter.resolution),
                'placeholder': 'Enter reading'
            })
        )

    ReadingFormSet = formset_factory(
        ReadingForm,
        extra=parameter.num_readings,
        max_num=parameter.num_readings,
        min_num=parameter.num_readings,
        validate_min=True,
        validate_max=True
    )

    return ReadingFormSet

class UncertaintyBudgetWidget(forms.Widget):
    """Custom widget for displaying uncertainty budget breakdown"""

    template_name = 'calibration/widgets/uncertainty_budget.html'

    def format_value(self, value):
        if value is None:
            return None

        try:
            components = json.loads(value) if isinstance(value, str) else value
            return components
        except (json.JSONDecodeError, TypeError):
            return None

    def render(self, name, value, attrs=None, renderer=None):
        context = {
            'widget': {
                'name': name,
                'value': self.format_value(value),
                'attrs': attrs,
            }
        }
        return self._render(self.template_name, context, renderer)

class CalibrationValidationMixin:
    """Mixin for common calibration validation methods"""

    def validate_readings_consistency(self, readings, tolerance):
        """Validate that readings are consistent within expected range"""
        if not readings or len(readings) < 2:
            return []

        errors = []
        mean_reading = sum(readings) / len(readings)

        for i, reading in enumerate(readings):
            deviation = abs(reading - mean_reading)
            if deviation > tolerance * 0.5:
                errors.append(f"Reading {i+1} ({reading}) deviates significantly from mean ({mean_reading:.6f})")

        return errors

    def validate_environmental_conditions(self, temperature, humidity, pressure):
        """Validate environmental conditions are within reasonable ranges"""
        errors = []

        if temperature is not None:
            if temperature < -40 or temperature > 85:
                errors.append("Temperature outside typical calibration range (-40°C to 85°C)")

        if humidity is not None:
            if humidity < 0 or humidity > 100:
                errors.append("Humidity must be between 0% and 100%")
            elif humidity > 95:
                errors.append("High humidity may affect measurement accuracy")

        if pressure is not None:
            if pressure < 80 or pressure > 120:
                errors.append("Atmospheric pressure outside typical range (80-120 kPa)")

        return errors

class ParameterForm(forms.ModelForm):
    class Meta:
        model = Parameter
        fields = ['name', 'symbol', 'unit']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'symbol': forms.TextInput(attrs={'class': 'form-control'}),
            'unit': forms.TextInput(attrs={'class': 'form-control'}),
        }

class StandardForm(forms.ModelForm):
    class Meta:
        model = Standard
        fields = [
            'name', 'model_number', 'serial_number', 'manufacturer',
            'certificate_number', 'calibration_date', 'calibration_due_date', 'calibration_agency',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'model_number': forms.TextInput(attrs={'class': 'form-control'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control'}),
            'manufacturer': forms.TextInput(attrs={'class': 'form-control'}),
            'certificate_number': forms.TextInput(attrs={'class': 'form-control'}),
            'calibration_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'calibration_due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'calibration_agency': forms.TextInput(attrs={'class': 'form-control'}),
        }

class StandardParameterForm(forms.ModelForm):
    """Form for creating/editing standard parameters with uncertainty"""

    class Meta:
        model = StandardParameter
        fields = ['parameter', 'uncertainty']
        widgets = {
            'parameter': forms.Select(attrs={
                'class': 'form-control parameter-select',
            }),
            'uncertainty': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.000001',
                'placeholder': 'Enter uncertainty (e.g., 0.001)'
            }),
        }

    def clean_uncertainty(self):
        uncertainty = self.cleaned_data.get('uncertainty')
        if uncertainty is not None and uncertainty <= 0:
            raise forms.ValidationError("Uncertainty must be positive")
        return uncertainty

StandardParameterFormSet = inlineformset_factory(
    Standard,
    StandardParameter,
    form=StandardParameterForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True
)

class CalibrationScheduleForm(forms.ModelForm):
    class Meta:
        model = CalibrationSchedule
        fields = ['calibration_procedure', 'estimated_duration']
        widgets = {
            'estimated_duration': forms.TimeInput(format='%H:%M:%S'),
        }

class EquipmentProcedureMappingForm(forms.ModelForm):
    class Meta:
        model = EquipmentCalibrationProcedure
        fields = ['equipment', 'calibration_procedure', 'is_default', 'estimated_duration', 'required_standards']
        widgets = {
            'estimated_duration': forms.TimeInput(format='%H:%M:%S'),
        }
