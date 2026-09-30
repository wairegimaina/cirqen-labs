"""Treat names that differ only in case, spacing or punctuation as the same.

"PatienT MONITOR", "patient- Monitor" and "Patient Monitor" all have the key
"patientmonitor", so a new equipment description, manufacturer, model or
part name that matches an existing one by key reuses it instead of adding a
near-duplicate to the drop-downs.
"""
import re

_NOT_LETTER_OR_DIGIT = re.compile(r'[\W_]+')


def name_key(value):
    """Lower case, with everything but letters and digits removed."""
    return _NOT_LETTER_OR_DIGIT.sub('', (value or '').casefold())


def tidy(value):
    """Trim and collapse runs of spaces: '  Patient   Monitor ' -> 'Patient Monitor'."""
    return ' '.join((value or '').split())


def find_same(queryset, value, field='name'):
    """The first row of ``queryset`` whose ``field`` has the same key as
    ``value``, or None. The name tables are small (a few thousand rows at
    most), so the comparison is done here rather than in SQL."""
    key = name_key(value)
    if not key:
        return None
    for pk, existing in queryset.values_list('pk', field):
        if name_key(existing) == key:
            return queryset.model._default_manager.get(pk=pk)
    return None


def get_or_create_named(model, value, field='name', **defaults):
    """Reuse the row whose name matches ``value`` by key, or create one.
    Returns (row, created) like ``get_or_create``."""
    existing = find_same(model._default_manager.all(), value, field)
    if existing is not None:
        revive(existing)
        return existing, False
    return model._default_manager.create(**{field: tidy(value)}, **defaults), True


def revive(row):
    """Undo a soft delete (the name was removed, then added again)."""
    fields = [f for f, deleted in (('active_status', False), ('pending_delete', True))
              if hasattr(row, f) and getattr(row, f) == deleted]
    if fields:
        for f in fields:
            setattr(row, f, not getattr(row, f))
        row.save()
    return row


def same_spelling(values, value):
    """The spelling in ``values`` with the same key as ``value`` (so a model
    typed as 'mx-450' is saved as the 'MX 450' already in use), else ``value``
    tidied."""
    key = name_key(value)
    for existing in values:
        if existing and name_key(existing) == key:
            return existing
    return tidy(value)


def check_unique(instance, label, field='name'):
    """Raise ValidationError when another row already has this name by key.

    Only checked when the row is new or its name's key changed, so near-
    duplicates that were saved before this rule still save for other edits.
    """
    from django.core.exceptions import ValidationError

    value = getattr(instance, field)
    model = type(instance)
    if not instance._state.adding:
        before = model._default_manager.filter(pk=instance.pk).values_list(field, flat=True).first()
        if before is not None and name_key(before) == name_key(value):
            return
    same = find_same(model._default_manager.exclude(pk=instance.pk), value, field)
    if same is not None:
        raise ValidationError({field: f"{label} '{getattr(same, field)}' already exists."})


def is_live(row):
    """Not soft-deleted."""
    return getattr(row, 'active_status', True) and not getattr(row, 'pending_delete', False)
