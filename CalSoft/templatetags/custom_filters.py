# CalSoft/templatetags/custom_filters.py
from django import template
from decimal import Decimal, InvalidOperation

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Safely get dictionary item or return None"""
    return dictionary.get(key)

@register.filter
def get_attr(obj, attr):
    """Safely get object attribute or return None"""
    return getattr(obj, attr, None)

@register.filter(name='mul')
def multiply(value, arg):
    """Multiply value by argument with Decimal support"""
    try:
        if isinstance(value, (Decimal, float, int)) and isinstance(arg, (Decimal, float, int)):
            return Decimal(str(value)) * Decimal(str(arg))
        return float(value) * float(arg)
    except (ValueError, TypeError, InvalidOperation):
        return value

@register.filter(name='div')
def divide(value, arg):
    """Divide value by argument with Decimal support"""
    try:
        if isinstance(value, (Decimal, float, int)) and isinstance(arg, (Decimal, float, int)):
            return Decimal(str(value)) / Decimal(str(arg))
        return float(value) / float(arg)
    except (ValueError, TypeError, ZeroDivisionError, InvalidOperation):
        return value

@register.filter(name='subtract')
def subtract(value, arg):
    """Subtract argument from value with Decimal support"""
    try:
        if isinstance(value, (Decimal, float, int)) and isinstance(arg, (Decimal, float, int)):
            return Decimal(str(value)) - Decimal(str(arg))
        return float(value) - float(arg)
    except (ValueError, TypeError, InvalidOperation):
        return value

@register.filter(name='add')
def add(value, arg):
    """Add value and argument with Decimal support"""
    try:
        if isinstance(value, (Decimal, float, int)) and isinstance(arg, (Decimal, float, int)):
            return Decimal(str(value)) + Decimal(str(arg))
        return float(value) + float(arg)
    except (ValueError, TypeError, InvalidOperation):
        return value

@register.filter(name='percentage')
def percentage(value, arg=100):
    """Calculate percentage of value"""
    try:
        if isinstance(value, (Decimal, float, int)) and isinstance(arg, (Decimal, float, int)):
            return (Decimal(str(value)) * Decimal(str(arg))) / Decimal('100')
        return (float(value) * float(arg)) / 100.0
    except (ValueError, TypeError, ZeroDivisionError, InvalidOperation):
        return value

@register.filter(name='round_decimal')
def round_decimal(value, places=2):
    """Round decimal to specified places"""
    try:
        if isinstance(value, Decimal):
            return value.quantize(Decimal(f'1.{"0"*places}'))
        return round(float(value), places)
    except (ValueError, TypeError, InvalidOperation):
        return value

@register.filter(name='default_if_none')
def default_if_none(value, default):
    """Return default if value is None"""
    return default if value is None else value

@register.filter(name='format_date')
def format_date(value, format_str='%Y-%m-%d'):
    """Format date using given format string"""
    if hasattr(value, 'strftime'):
        return value.strftime(format_str)
    return value