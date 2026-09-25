"""Checklist on a work order: what to offer, what to accept, what to save.

The technician selects one checklist on the work order form (the ones written
for the device's description and the task are suggested) and may add steps of
their own for this one job. The form posts::

    checklist_template             id of the selected checklist, or blank
    checklist_result_<item id>     done | not_done | pass | fail | na
    checklist_value_<item id>      reading, for 'value' items
    checklist_note_<item id>       remarks
    checklist_at_<item id>         when the step was answered (ISO, set by the browser)

    custom_keys                    one key per added step
    custom_task_<key>, custom_result_<key>, custom_note_<key>, custom_at_<key>

Kept free of view code so the rules can be tested on their own.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive, make_aware, now

from .models import ChecklistTemplate, WorkOrderChecklistEntry

CUSTOM_RESULTS = WorkOrderChecklistEntry.RESULTS_FOR['check']
MAX_CUSTOM_STEPS = 50
# A step's completion time comes from the browser; anything outside this
# window (a wrong clock, a form left open for days) is recorded as "now".
ANSWER_WINDOW = timedelta(days=7)


class ChecklistIncomplete(Exception):
    """A required step was not answered, or an answer is not allowed."""


def _choices(response_type):
    labels = dict(WorkOrderChecklistEntry.RESULT_CHOICES)
    return [{'value': v, 'label': labels[v]} for v in WorkOrderChecklistEntry.RESULTS_FOR[response_type]]


def template_payload(template):
    """One checklist, with its steps, in the shape the work order form renders."""
    return {
        'id': str(template.id),
        'title': template.title,
        'task_type': template.task_type,
        'applies_to': template.applies_to,
        'instructions': template.instructions,
        'items': [
            {
                'id': str(item.id),
                'order': item.order,
                'task': item.task,
                'guidance': item.guidance,
                'expected_result': item.expected_result,
                'response_type': item.response_type,
                'is_required': item.is_required,
                'choices': _choices(item.response_type),
            }
            for item in template.active_items()
        ],
    }


def options_for(equipment, action_taken):
    """Checklists to offer: suggested ones (fit the device and task) first, then the rest."""
    suggested = list(ChecklistTemplate.for_work(equipment, action_taken).select_related('equipment_description'))
    suggested_ids = {t.id for t in suggested}
    others = [t for t in ChecklistTemplate.usable().select_related('equipment_description')
              .order_by('title') if t.id not in suggested_ids]

    def brief(t):
        return {'id': str(t.id), 'title': t.title, 'task_type': t.task_type, 'applies_to': t.applies_to}

    return {
        'suggested': [brief(t) for t in suggested],
        'others': [brief(t) for t in others],
        'custom_choices': _choices('check'),
    }


def _answered_at(raw, fallback):
    parsed = parse_datetime(raw or '')
    if parsed is None:
        return fallback
    if is_naive(parsed):
        parsed = make_aware(parsed)
    if not (fallback - ANSWER_WINDOW <= parsed <= fallback + timedelta(minutes=5)):
        return fallback
    return min(parsed, fallback)


def read_answers(post):
    """Validate the posted checklist.

    Returns ``(template, answers)`` where ``answers`` is a list of dicts ready
    for :func:`save_answers`. Raises ChecklistIncomplete naming every step that
    needs attention, so the technician sees them all at once.
    """
    submitted_at = now()
    answers, problems = [], []

    template = None
    template_id = (post.get('checklist_template') or '').strip()
    if template_id:
        try:
            template = ChecklistTemplate.usable().get(id=template_id)
        except (ChecklistTemplate.DoesNotExist, ValueError, ValidationError):
            raise ChecklistIncomplete("The selected checklist is no longer available. Select another.")

        for item in template.active_items():
            key = str(item.id)
            result = (post.get(f'checklist_result_{key}') or '').strip()
            value = (post.get(f'checklist_value_{key}') or '').strip()[:100]
            note = (post.get(f'checklist_note_{key}') or '').strip()[:500]

            if result and result not in WorkOrderChecklistEntry.RESULTS_FOR[item.response_type]:
                problems.append(f"'{item.task}': invalid answer")
                continue
            if not result:
                if item.is_required:
                    problems.append(f"'{item.task}' is required")
                continue  # optional step left blank: nothing to record
            if item.response_type == 'value' and result in ('pass', 'fail') and not value:
                problems.append(f"'{item.task}': enter the reading")
                continue
            if result in ('fail', 'not_done') and not note:
                problems.append(f"'{item.task}': say why it failed or was not done")
                continue
            answers.append({
                'template': template, 'item': item, 'template_title': template.title, 'order': item.order,
                'task': item.task, 'guidance': item.guidance, 'expected_result': item.expected_result,
                'response_type': item.response_type, 'is_required': item.is_required,
                'result': result, 'value': value, 'note': note,
                'completed_at': _answered_at(post.get(f'checklist_at_{key}'), submitted_at),
            })

    keys = [k for k in post.getlist('custom_keys') if k][:MAX_CUSTOM_STEPS]
    for position, key in enumerate(keys, start=1):
        task = (post.get(f'custom_task_{key}') or '').strip()[:255]
        result = (post.get(f'custom_result_{key}') or '').strip()
        note = (post.get(f'custom_note_{key}') or '').strip()[:500]
        if not task:
            if result or note:
                problems.append(f"Added step {position}: describe the step")
            continue
        if result not in CUSTOM_RESULTS:
            problems.append(f"'{task}': mark it done, not done or N/A")
            continue
        if result == 'not_done' and not note:
            problems.append(f"'{task}': say why it was not done")
            continue
        answers.append({
            'template': None, 'item': None, 'template_title': WorkOrderChecklistEntry.CUSTOM_TITLE,
            'order': 1000 + position, 'task': task, 'guidance': '', 'expected_result': '',
            'response_type': 'check', 'is_required': True, 'result': result, 'value': '', 'note': note,
            'completed_at': _answered_at(post.get(f'custom_at_{key}'), submitted_at),
        })

    if problems:
        raise ChecklistIncomplete("Checklist incomplete: " + "; ".join(problems))
    return template, answers


def save_answers(job_card, answers, user):
    """Store each answer with a copy of the step's wording, who completed it and when.

    Saved one by one (not bulk_create) so post_save fires and core.hq_link
    pushes the rows to HQ with the work order.
    """
    for answer in answers:
        WorkOrderChecklistEntry.objects.create(job_card=job_card, completed_by=user, **answer)


def entries_by_checklist(job_card):
    """A work order's entries grouped for display: [(title, [entries])], selected checklist first."""
    groups = {}
    entries = (job_card.checklist_entries.filter(active_status=True, pending_delete=False)
               .select_related('completed_by', 'template').order_by('order', 'created_at'))
    for entry in entries:
        groups.setdefault(entry.template_title, []).append(entry)
    return sorted(groups.items(), key=lambda g: g[0] == WorkOrderChecklistEntry.CUSTOM_TITLE)
