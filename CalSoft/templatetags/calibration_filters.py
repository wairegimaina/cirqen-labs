# CalSoft/templatetags/calibration_filters.py
from django import template
from django.template.defaultfilters import floatformat

register = template.Library()

@register.filter
def lookup(dictionary, key):
    """
    Template filter to look up a dictionary value by key.
    Usage: {{ dictionary|lookup:key }}
    """
    if dictionary and hasattr(dictionary, '__getitem__'):
        try:
            return dictionary.get(key, None)
        except (AttributeError, KeyError, TypeError):
            return None
    return None

@register.filter
def get_item(dictionary, key):
    """
    Alternative dictionary lookup filter.
    Usage: {{ dictionary|get_item:key }}
    """
    return dictionary.get(key) if dictionary else None

@register.filter
def multiply(value, multiplier):
    """
    Multiply a value by a multiplier.
    Usage: {{ value|multiply:multiplier }}
    """
    try:
        return float(value) * float(multiplier)
    except (ValueError, TypeError):
        return 0

@register.filter
def percentage(value, total):
    """
    Calculate percentage of value from total.
    Usage: {{ value|percentage:total }}
    """
    try:
        if float(total) == 0:
            return 0
        return round((float(value) / float(total)) * 100, 1)
    except (ValueError, TypeError, ZeroDivisionError):
        return 0

@register.filter
def abs_value(value):
    """
    Return absolute value.
    Usage: {{ value|abs_value }}
    """
    try:
        return abs(float(value))
    except (ValueError, TypeError):
        return 0

@register.filter
def format_uncertainty(value, digits=6):
    """
    Format uncertainty values with appropriate decimal places.
    Usage: {{ uncertainty|format_uncertainty:digits }}
    """
    try:
        if value is None:
            return "N/A"
        float_val = float(value)
        if float_val == 0:
            return "0"
        return f"{float_val:.{digits}f}".rstrip('0').rstrip('.')
    except (ValueError, TypeError):
        return "N/A"

@register.filter
def scientific_notation(value, precision=2):
    """
    Format number in scientific notation.
    Usage: {{ value|scientific_notation:precision }}
    """
    try:
        if value is None:
            return "N/A"
        float_val = float(value)
        if float_val == 0:
            return "0"
        return f"{float_val:.{precision}e}"
    except (ValueError, TypeError):
        return "N/A"

@register.filter
def reading_exists(reading_obj, reading_number):
    """
    Check if a specific reading exists.
    Usage: {{ reading|reading_exists:1 }}
    """
    try:
        attr_name = f"reading_{reading_number}"
        return hasattr(reading_obj, attr_name) and getattr(reading_obj, attr_name) is not None
    except (AttributeError, TypeError):
        return False

@register.filter
def get_reading(reading_obj, reading_number):
    """
    Get a specific reading value.
    Usage: {{ reading|get_reading:1 }}
    """
    try:
        attr_name = f"reading_{reading_number}"
        if hasattr(reading_obj, attr_name):
            value = getattr(reading_obj, attr_name)
            return value if value is not None else "-"
        return "-"
    except (AttributeError, TypeError):
        return "-"

@register.filter
def tolerance_status(reading_obj):
    """
    Get tolerance status with icon.
    Usage: {{ reading|tolerance_status }}
    """
    try:
        if reading_obj.passes_tolerance:
            return '<i class="fas fa-check text-success"></i> PASS'
        else:
            return '<i class="fas fa-times text-danger"></i> FAIL'
    except AttributeError:
        return '<i class="fas fa-question text-warning"></i> N/A'

@register.filter
def status_class(passes_tolerance):
    """
    Get CSS class for tolerance status.
    Usage: {{ passes_tolerance|status_class }}
    """
    return "tolerance-pass" if passes_tolerance else "tolerance-fail"

@register.filter
def default_if_none(value, default):
    """
    Return default value if the original value is None.
    Usage: {{ value|default_if_none:"N/A" }}
    """
    return default if value is None else value

@register.simple_tag
def reading_range(num_readings):
    """
    Generate range for reading columns.
    Usage: {% reading_range num_readings %}
    """
    return range(1, int(num_readings) + 1)

@register.inclusion_tag('calibration/partials/uncertainty_component.html')
def uncertainty_component(label, value, unit=""):
    """
    Render uncertainty component.
    Usage: {% uncertainty_component "Type A" reading.type_a_uncertainty "V" %}
    """
    return {
        'label': label,
        'value': value,
        'unit': unit
    }

@register.inclusion_tag('calibration/partials/reading_row.html')
def reading_row(reading, parameter):
    """
    Render a reading table row.
    Usage: {% reading_row reading parameter %}
    """
    return {
        'reading': reading,
        'parameter': parameter,
        'reading_numbers': range(1, parameter.num_readings + 1)
    }

@register.filter
def dict_lookup(dictionary, key):
    """
    Lookup dictionary value by key, handling string keys.
    Usage: {{ dictionary|dict_lookup:key }}
    """
    if not dictionary:
        return None
    
    # Handle different key types
    if isinstance(key, str):
        return dictionary.get(key)
    else:
        return dictionary.get(str(key))

@register.filter
def split_parameter_key(key):
    """
    Split parameter key to get parameter and sub-parameter names.
    Usage: {{ key|split_parameter_key }}
    """
    try:
        parts = key.split('_')
        if len(parts) >= 2:
            return {
                'parameter': parts[0],
                'sub_parameter': parts[1] if parts[1] != 'Default' else None
            }
        return {'parameter': key, 'sub_parameter': None}
    except (AttributeError, IndexError):
        return {'parameter': str(key), 'sub_parameter': None}

@register.filter
def format_duration(seconds):
    """
    Format duration in seconds to human-readable format.
    Usage: {{ duration_seconds|format_duration }}
    """
    try:
        seconds = int(seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    except (ValueError, TypeError):
        return "N/A"

@register.filter
def chart_color(index, alpha=0.7):
    """
    Generate chart colors based on index.
    Usage: {{ forloop.counter0|chart_color }}
    """
    colors = [
        f"rgba(255, 99, 132, {alpha})",
        f"rgba(54, 162, 235, {alpha})",
        f"rgba(255, 205, 86, {alpha})",
        f"rgba(75, 192, 192, {alpha})",
        f"rgba(153, 102, 255, {alpha})",
        f"rgba(255, 159, 64, {alpha})",
        f"rgba(199, 199, 199, {alpha})",
        f"rgba(83, 102, 255, {alpha})"
    ]
    return colors[int(index) % len(colors)]

@register.simple_tag
def get_linearity_data(linearity_analysis, parameter_key):
    """
    Get linearity analysis data for a specific parameter.
    Usage: {% get_linearity_data linearity_analysis parameter_key %}
    """
    if linearity_analysis and parameter_key in linearity_analysis:
        return linearity_analysis[parameter_key]
    return None