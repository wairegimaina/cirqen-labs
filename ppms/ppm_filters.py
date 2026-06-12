from django import template
from datetime import datetime

register = template.Library()

@register.filter(name='date_month_name')
def date_month_name(month_number):
    """
    Convert a month number (1-12) to month name.
    Usage: {{ forloop.counter|date_month_name }}
    """
    try:
        month_num = int(month_number)
        if 1 <= month_num <= 12:
            # Create a date object and use Django's date formatting
            date_obj = datetime(2000, month_num, 1)
            return date_obj.strftime('%B')  # Returns full month name (e.g., "January")
        return month_number
    except (ValueError, TypeError):
        return month_number
