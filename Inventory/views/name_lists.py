"""Edit the drop-down lists of the Add Equipment form: equipment
descriptions, manufacturers and the models used under each description.

A description or manufacturer can be renamed any time, and deleted only when
nothing uses it (no equipment, deleted or not, and no other record points at
it). Deleting is the usual soft delete (``pending_delete``), which sync turns
into a delete at HQ and on every PC.

A model is not a table of its own, just the text on each device, so renaming
one rewrites that text on every device of that description; renaming it to a
model already in the list merges the two. A model always belongs to at least
one device, so it can be renamed but never deleted.
"""
import logging

from django.core.exceptions import ValidationError
from django.db.models import Count
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.names import same_spelling, tidy
from Inventory.models import Equipment, EquipmentDescription, Manufacturer
from users.control import role_required

logger = logging.getLogger(__name__)

KINDS = {'description': EquipmentDescription, 'manufacturer': Manufacturer}


def usage_counts(model, only=None):
    """{pk: number of rows in any table that point at it}, one query per
    table that has a foreign key to ``model`` (or just the ``only`` table)."""
    counts = {}
    for rel in model._meta.related_objects:
        if only is not None and rel.related_model is not only:
            continue
        field = rel.field.name
        rows = (rel.related_model._default_manager.filter(**{f'{field}__isnull': False})
                .values(field).annotate(n=Count('pk')))
        for row in rows:
            counts[row[field]] = counts.get(row[field], 0) + row['n']
    return counts


def _live(model):
    return model.objects.filter(active_status=True, pending_delete=False).order_by('name')


def _kind(kind):
    model = KINDS.get(kind)
    if model is None:
        raise ValueError(kind)
    return model


@login_required
@role_required('Tech', 'HOD', json=True, message='Only technologists and the HOD can edit these lists.')
@require_GET
def name_lists(request):
    """Descriptions and manufacturers, each with how many records use it
    (``uses``, which decides whether it can be deleted) and how many of
    those are devices."""
    data = {}
    for kind, model in KINDS.items():
        used = usage_counts(model)
        devices = usage_counts(model, only=Equipment)
        data[kind + 's'] = [{'id': str(pk), 'name': name, 'uses': used.get(pk, 0), 'devices': devices.get(pk, 0)}
                            for pk, name in _live(model).values_list('pk', 'name')]
    return JsonResponse({'success': True, **data})


@login_required
@role_required('Tech', 'HOD', json=True, message='Only technologists and the HOD can edit these lists.')
@require_POST
def rename_name(request, kind, pk):
    try:
        model = _kind(kind)
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Unknown list.'}, status=404)
    row = get_object_or_404(model, pk=pk)
    name = tidy(request.POST.get('name'))
    if not name:
        return JsonResponse({'success': False, 'error': 'The name cannot be empty.'}, status=400)
    old = row.name
    row.name = name
    try:
        row.save()  # clean() refuses a name already in the list, by case/spacing/punctuation
    except ValidationError as exc:
        return JsonResponse({'success': False, 'error': ' '.join(exc.messages)}, status=400)
    logger.info("%s renamed %s '%s' -> '%s'", request.user.username, kind, old, row.name)
    return JsonResponse({'success': True, 'id': str(row.pk), 'name': row.name})


@login_required
@role_required('Tech', 'HOD', json=True, message='Only technologists and the HOD can edit these lists.')
@require_POST
def delete_name(request, kind, pk):
    try:
        model = _kind(kind)
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Unknown list.'}, status=404)
    row = get_object_or_404(model, pk=pk)
    uses = usage_counts(model).get(row.pk, 0)
    if uses:
        return JsonResponse({
            'success': False,
            'error': f"'{row.name}' is used by {uses} record{'s' if uses != 1 else ''} and cannot be deleted.",
        }, status=400)
    row.active_status = False
    row.pending_delete = True
    row.save()
    logger.info("%s deleted %s '%s'", request.user.username, kind, row.name)
    return JsonResponse({'success': True})


@login_required
@role_required('Tech', 'HOD', json=True, message='Only technologists and the HOD can edit these lists.')
@require_GET
def model_list(request, description_id):
    """Models used under one description, each with its number of devices."""
    description = get_object_or_404(EquipmentDescription, pk=description_id)
    rows = (Equipment.objects.filter(description=description).exclude(model='')
            .values('model').annotate(n=Count('pk')).order_by('model'))
    return JsonResponse({'success': True, 'models': [{'name': r['model'], 'uses': r['n']} for r in rows]})


@login_required
@role_required('Tech', 'HOD', json=True, message='Only technologists and the HOD can edit these lists.')
@require_POST
def rename_model(request, description_id):
    description = get_object_or_404(EquipmentDescription, pk=description_id)
    old = request.POST.get('old', '')
    new = tidy(request.POST.get('name'))
    if not new:
        return JsonResponse({'success': False, 'error': 'The model cannot be empty.'}, status=400)
    if len(new) > Equipment._meta.get_field('model').max_length:
        return JsonResponse({'success': False, 'error': 'The model name is too long.'}, status=400)
    devices = Equipment.objects.filter(description=description)
    # Renaming to a model already in the list (by case/spacing/punctuation) merges into it.
    new = same_spelling(devices.exclude(model=old).values_list('model', flat=True).distinct(), new)
    changed = devices.filter(model=old).update(model=new, needs_sync=True, updated_at=timezone.now())
    logger.info("%s renamed model '%s' -> '%s' under '%s' (%d devices)",
                request.user.username, old, new, description.name, changed)
    return JsonResponse({'success': True, 'name': new, 'changed': changed})
