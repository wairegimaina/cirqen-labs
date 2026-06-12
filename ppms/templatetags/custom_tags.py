from django import template




register = template.Library()

@register.filter
def is_active(dept_id, selected_id):
    return dept_id == selected_id
