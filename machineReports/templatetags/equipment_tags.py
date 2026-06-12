from django import template

register = template.Library()

@register.filter
def percentage(value, total):
    try:
        if total == 0:
            return 0
        return round((value / total) * 100)
    except (ValueError, TypeError):
        return 0

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)