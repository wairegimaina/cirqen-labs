"""Checklists module: reusable checklists (list, read-only detail, edit, Excel
upload) and the work order form's lookup of the checklists it can select.

Everyone with a role can read checklists; technologists and HODs write them.
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from openpyxl import Workbook

from core import excel_import as xl

from core.scoping import for_user
from Inventory.models import Equipment, EquipmentDescription
from users.control import get_user_role, role_required

from ..checklists import options_for, template_payload
from ..starter_checklists import STARTERS, choices as starter_choices
from ..models import ChecklistItem, ChecklistTemplate, WorkOrderChecklistEntry, jobcard

logger = logging.getLogger(__name__)

_EDITORS = ('Tech', 'HOD')
_VIEWERS = ('Tech', 'HOD', 'NIC')
_DENIED = "Only technologists and HODs can edit checklists."


@login_required
@require_GET
def checklist_for_work(request):
    """For the work order form.

    ``?template_id=`` returns that checklist with its steps. Otherwise
    ``?equipment_id=&action=`` returns the checklists to offer, the ones
    written for that device and task first.
    """
    template_id = request.GET.get('template_id')
    if template_id:
        try:
            template = ChecklistTemplate.usable().select_related('equipment_description').get(id=template_id)
        except (ChecklistTemplate.DoesNotExist, ValueError, ValidationError):
            return JsonResponse({'success': False, 'error': 'Checklist not found'}, status=404)
        return JsonResponse({'success': True, 'template': template_payload(template)})

    equipment_id = request.GET.get('equipment_id')
    action = request.GET.get('action', '')
    if not equipment_id or action not in dict(ChecklistTemplate.TASK_CHOICES):
        return JsonResponse({'success': True, 'suggested': [], 'others': []})

    profile = getattr(request.user, 'userprofile', None)
    equipment_qs = Equipment.objects.filter(active_status=True)
    # Calibration centres serve every workshop (same rule as work order creation).
    if not (profile and profile.role == 'Tech' and profile.workshop
            and profile.workshop.category == 'calibration_center'):
        equipment_qs = for_user(equipment_qs, request.user)
    try:
        equipment = equipment_qs.get(id=equipment_id)
    except (Equipment.DoesNotExist, ValueError, ValidationError):
        return JsonResponse({'success': False, 'error': 'Equipment not found'}, status=404)

    return JsonResponse({'success': True, **options_for(equipment, action)})


def _with_counts(queryset):
    return queryset.annotate(
        item_count=Count('items', filter=Q(items__active_status=True, items__pending_delete=False), distinct=True),
        use_count=Count('entries__job_card', filter=Q(entries__active_status=True), distinct=True),
    )


@login_required
@role_required(*_VIEWERS, redirect_to='jobcard:create_job_card', message="You do not have access to checklists.")
def checklist_list(request):
    """Reusable checklists: name, what they apply to, steps, status, author, uses."""
    description_filter = request.GET.get('description', '')
    status_filter = request.GET.get('status', '')
    q = request.GET.get('q', '').strip()
    templates = _with_counts(
        ChecklistTemplate.objects.filter(pending_delete=False).select_related('equipment_description', 'created_by')
    ).order_by('-active_status', 'title')
    if description_filter == 'all':
        templates = templates.filter(equipment_description__isnull=True)
    elif description_filter:
        templates = templates.filter(equipment_description_id=description_filter)
    if status_filter in ('active', 'inactive'):
        templates = templates.filter(active_status=status_filter == 'active')
    if q:
        templates = templates.filter(Q(title__icontains=q) | Q(equipment_description__name__icontains=q)
                                     | Q(instructions__icontains=q))
    can_edit = get_user_role(request.user) in _EDITORS
    return render(request, 'jobcard/checklist_settings.html', {
        'show_sidebar': True,
        'templates': templates,
        'descriptions': EquipmentDescription.objects.filter(active_status=True).order_by('name'),
        'task_choices': ChecklistTemplate.TASK_CHOICES,
        'description_filter': description_filter,
        'status_filter': status_filter,
        'q': q,
        'can_edit': can_edit,
        'starters': starter_choices() if can_edit else [],
        'checklist_template_url': reverse('jobcard:checklist_import_template'),
        'checklist_upload_url': reverse('jobcard:checklist_import_upload'),
    })


@login_required
@role_required(*_VIEWERS, redirect_to='jobcard:create_job_card', message="You do not have access to checklists.")
def checklist_detail(request, template_id):
    """Read-only view of one checklist: every step, and the work orders it was used on."""
    template = get_object_or_404(
        _with_counts(ChecklistTemplate.objects.filter(pending_delete=False))
        .select_related('equipment_description', 'created_by'),
        id=template_id,
    )
    work_orders = for_user(
        jobcard.objects.filter(checklist_entries__template=template, checklist_entries__active_status=True)
        .distinct().select_related('equipment__description', 'performed_by').order_by('-created_at'),
        request.user,
    )
    return render(request, 'jobcard/checklist_detail.html', {
        'show_sidebar': True,
        'template': template,
        'items': template.active_items(),
        'work_orders': work_orders[:50],
        'work_order_total': work_orders.count(),
        'can_edit': get_user_role(request.user) in _EDITORS,
    })


@login_required
@role_required(*_EDITORS, redirect_to='jobcard:create_job_card', message=_DENIED)
def checklist_edit(request, template_id=None):
    """Create or edit one template and its ordered steps on a single page."""
    template = get_object_or_404(ChecklistTemplate, id=template_id) if template_id else None

    if request.method == 'POST':
        post = request.POST
        title = post.get('title', '').strip()
        description_id = post.get('equipment_description')
        task_type = post.get('task_type', 'Any')
        errors = []
        if not title:
            errors.append('Title is required.')
        description = EquipmentDescription.objects.filter(id=description_id).first() if description_id else None
        if description_id and not description:
            errors.append('Pick the equipment this checklist is for, or "All equipment".')
        if task_type not in dict(ChecklistTemplate.TASK_CHOICES):
            errors.append('Invalid task type.')

        # Steps arrive as parallel lists (one entry per row); blank task rows are dropped.
        steps = []
        rows = zip(post.getlist('item_id'), post.getlist('item_task'), post.getlist('item_guidance'),
                   post.getlist('item_expected'), post.getlist('item_response_type'),
                   post.getlist('item_required'))
        for item_id, task, guidance, expected, response_type, required in rows:
            task = task.strip()
            if not task:
                continue
            if response_type not in dict(ChecklistItem.RESPONSE_CHOICES):
                response_type = 'check'
            steps.append({
                'id': item_id or None,
                'task': task[:255],
                'guidance': guidance.strip(),
                'expected_result': expected.strip()[:255],
                'response_type': response_type,
                'is_required': required == '1',
            })
        if not steps:
            errors.append('Add at least one step.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'jobcard/checklist_edit.html', {
                'show_sidebar': True, 'template': template, 'posted': post, 'steps': steps,
                'selected_description': description_id or '', 'selected_task': task_type,
                'descriptions': EquipmentDescription.objects.filter(active_status=True).order_by('name'),
                'task_choices': ChecklistTemplate.TASK_CHOICES,
                'response_choices': ChecklistItem.RESPONSE_CHOICES,
            })

        with transaction.atomic():
            if template is None:
                template = ChecklistTemplate(created_by=request.user)
            template.title = title[:200]
            template.equipment_description = description
            template.task_type = task_type
            template.instructions = post.get('instructions', '').strip()
            template.save()

            existing = {str(i.id): i for i in template.items.filter(pending_delete=False)}
            kept = set()
            for order, step in enumerate(steps, start=1):
                item = existing.get(step['id']) or ChecklistItem(template=template)
                item.order = order
                for field in ('task', 'guidance', 'expected_result', 'response_type', 'is_required'):
                    setattr(item, field, step[field])
                item.active_status = True
                item.save()
                kept.add(str(item.id))
            # Removed steps are deactivated, not deleted: past work orders point at them.
            for item_id, item in existing.items():
                if item_id not in kept and item.active_status:
                    item.active_status = False
                    item.save()

        logger.info("Checklist %s saved by %s (%d steps)", template.id, request.user.username, len(steps))
        messages.success(request, f"Checklist '{template.title}' saved.", extra_tags="success")
        return redirect('jobcard:checklist_detail', template_id=template.id)

    steps = []
    if template:
        steps = [
            {'id': str(i.id), 'task': i.task, 'guidance': i.guidance, 'expected_result': i.expected_result,
             'response_type': i.response_type, 'is_required': i.is_required}
            for i in template.items.filter(active_status=True, pending_delete=False).order_by('order')
        ]
    return render(request, 'jobcard/checklist_edit.html', {
        'show_sidebar': True,
        'template': template,
        'steps': steps,
        'selected_description': str(template.equipment_description_id) if template
                                else request.GET.get('description', ''),
        'selected_task': template.task_type if template else 'Any',
        'descriptions': EquipmentDescription.objects.filter(active_status=True).order_by('name'),
        'task_choices': ChecklistTemplate.TASK_CHOICES,
        'response_choices': ChecklistItem.RESPONSE_CHOICES,
    })


@login_required
@role_required(*_EDITORS, redirect_to='jobcard:create_job_card', message=_DENIED)
@require_POST
def checklist_toggle_active(request, template_id):
    template = get_object_or_404(ChecklistTemplate, id=template_id)
    template.active_status = not template.active_status
    template.save()
    state = 'enabled' if template.active_status else 'disabled'
    messages.success(request, f"Checklist '{template.title}' {state}.", extra_tags="success")
    if request.POST.get('next') == 'detail':
        return redirect('jobcard:checklist_detail', template_id=template.id)
    return redirect('jobcard:checklist_settings')


@login_required
@role_required(*_EDITORS, redirect_to='jobcard:create_job_card', message=_DENIED)
@require_POST
def checklist_from_starter(request):
    """Copy a starter checklist onto an equipment description, then open it for editing."""
    starter = STARTERS.get(request.POST.get('starter', ''))
    description_id = request.POST.get('equipment_description')
    description = EquipmentDescription.objects.filter(id=description_id).first() if description_id else None
    if not starter or not description:
        messages.error(request, "Pick a starter checklist and the equipment description to copy it to.")
        return redirect('jobcard:checklist_settings')
    with transaction.atomic():
        template = ChecklistTemplate.objects.create(
            equipment_description=description, task_type=starter['task_type'],
            title=f"{description.name} — {starter['task_type']}", instructions=starter['instructions'],
            created_by=request.user,
        )
        for order, (task, guidance, expected, response_type, required) in enumerate(starter['items'], start=1):
            ChecklistItem.objects.create(template=template, order=order, task=task, guidance=guidance,
                                         expected_result=expected, response_type=response_type, is_required=required)
    messages.success(request, f"Copied '{starter['label']}' to {description.name}. Review the steps against "
                              "the service manual and save.", extra_tags="success")
    return redirect('jobcard:checklist_edit', template_id=template.id)


# ── Excel import (same preview/commit contract as the inventory upload) ─────
#
# One row per step. Rows with the same Checklist Name, Applies To and Task
# Type form one checklist, in the order they appear (or by Step No.).

COLUMNS = [
    ('title', 'Checklist Name'),
    ('applies_to', 'Applies To'),
    ('task_type', 'Task Type'),
    ('instructions', 'Description'),
    ('order', 'Step No.'),
    ('task', 'Step'),
    ('guidance', 'Instructions'),
    ('expected_result', 'Expected Result'),
    ('response_type', 'Response Type'),
    ('is_required', 'Required'),
]
HEADER_ALIASES = {
    'checklistname': 'title', 'checklist': 'title', 'name': 'title', 'title': 'title',
    'appliesto': 'applies_to', 'equipment': 'applies_to', 'equipmentdescription': 'applies_to',
    'equipmenttype': 'applies_to',
    'tasktype': 'task_type', 'action': 'task_type',
    'description': 'instructions', 'checklistdescription': 'instructions', 'overview': 'instructions',
    'stepno': 'order', 'stepnumber': 'order', 'order': 'order', 'sequence': 'order',
    'step': 'task', 'item': 'task', 'checklistitem': 'task', 'steptask': 'task',
    'instructions': 'guidance', 'guidance': 'guidance', 'notes': 'guidance', 'howto': 'guidance',
    'expectedresult': 'expected_result', 'expected': 'expected_result', 'acceptance': 'expected_result',
    'responsetype': 'response_type', 'response': 'response_type', 'type': 'response_type',
    'required': 'is_required', 'mandatory': 'is_required',
}
_RESPONSE_BY_TEXT = {
    '': 'check', 'check': 'check', 'donenotdone': 'check', 'done': 'check', 'checkbox': 'check',
    'passfail': 'pass_fail', 'pass': 'pass_fail',
    'value': 'value', 'reading': 'value', 'readingorvalue': 'value', 'measurement': 'value',
}
_TASK_BY_TEXT = {xl.normalize(v): v for v, _ in ChecklistTemplate.TASK_CHOICES}
_TASK_BY_TEXT.update({'': 'Any', 'anytask': 'Any', 'all': 'Any', 'preventivemaintenance': 'PPM', 'other': 'Others'})


@login_required
@role_required(*_EDITORS, redirect_to='jobcard:checklist_settings', message=_DENIED)
@require_GET
def checklist_import_template(request):
    wb = Workbook()
    ws = wb.active
    ws.title = "Checklists"
    xl.write_header(ws, [label for _, label in COLUMNS])
    for row in (
        ["Patient Monitor PM", "Patient Monitor", "PPM", "Quarterly preventive maintenance", 1,
         "Inspect power cable", "Look for cuts, kinks and loose plugs", "No damage", "Done / Not done", "Yes"],
        ["Patient Monitor PM", "Patient Monitor", "PPM", "", 2, "Verify alarm functionality",
         "Trigger high/low HR alarms", "Audible and visual alarm", "Pass / Fail", "Yes"],
        ["Patient Monitor PM", "Patient Monitor", "PPM", "", 3, "Electrical safety test",
         "Earth leakage with the analyser", "< 500 µA", "Reading or value", "Yes"],
    ):
        ws.append(row)
    ranges = xl.write_reference(wb, [
        ("Equipment (Applies To)", list(EquipmentDescription.objects.filter(active_status=True)
                                        .order_by('name').values_list('name', flat=True))),
        ("Task Type", [v for v, _ in ChecklistTemplate.TASK_CHOICES]),
        ("Response Type", [label for _, label in ChecklistItem.RESPONSE_CHOICES]),
        ("Required", ["Yes", "No"]),
    ])
    xl.add_dropdown(ws, 2, ranges.get("Equipment (Applies To)"))
    xl.add_dropdown(ws, 3, ranges.get("Task Type"))
    xl.add_dropdown(ws, 9, ranges.get("Response Type"))
    xl.add_dropdown(ws, 10, ranges.get("Required"))
    xl.write_instructions(wb, [
        "How to use this template",
        "",
        "1. One row per checklist STEP on the 'Checklists' sheet. Rows 2-4 are an example; replace them.",
        "2. Rows with the same Checklist Name, Applies To and Task Type make one checklist.",
        "3. Checklist Name and Step are required. Description only needs to be on the checklist's first row.",
        "4. Applies To is an equipment description (see 'Reference'); leave it blank for all equipment.",
        "5. Task Type: Any, PPM, Calibration, Repair or Others (blank = Any).",
        "6. Response Type: 'Done / Not done' (default), 'Pass / Fail' or 'Reading or value'.",
        "7. Required: Yes (default) or No. Step No. sets the order; otherwise rows are taken in order.",
        "8. A checklist that already exists with the same name, equipment and task type is skipped.",
        f"9. Maximum {xl.MAX_ROWS} rows per upload.",
        "",
        "You will always see a preview of what will be imported before anything is saved.",
    ])
    return xl.workbook_response(wb, "checklist_import_template.xlsx")


@login_required
@role_required(*_EDITORS, redirect_to='jobcard:checklist_settings', message=_DENIED)
@require_POST
def checklist_import_upload(request):
    create_missing = request.POST.get('create_missing', 'false').lower() == 'true'
    return xl.run_import(
        request, what="Checklist",
        process=lambda workbook, report: _process_checklists(workbook, report, request.user, create_missing),
    )


def _process_checklists(workbook, report, user, create_missing):
    ws = xl.find_sheet(workbook, "Checklists", fallback_first=True)
    header_row, mapping = xl.locate_header(ws, HEADER_ALIASES, required=('title', 'task'))
    if not header_row:
        raise xl.ImportFileError(xl.header_error(['Checklist Name', 'Step']))

    # Read and validate every row, grouping steps into checklists.
    groups = {}
    for excel_row, values in xl.data_rows(ws, header_row, mapping, report):
        title = values.get('title', '')[:200]
        applies_to = values.get('applies_to', '')
        task = values.get('task', '')
        item = f"{title} · {task}" if title else task
        errors = []
        if not title:
            errors.append("Checklist Name is required.")
        if not task:
            errors.append("Step is required.")
        task_type = _TASK_BY_TEXT.get(xl.normalize(values.get('task_type', '')))
        if task_type is None:
            errors.append(f"Task Type '{values.get('task_type')}' is not one of: "
                          + ", ".join(v for v, _ in ChecklistTemplate.TASK_CHOICES) + ".")
        response_type = _RESPONSE_BY_TEXT.get(xl.normalize(values.get('response_type', '')))
        if response_type is None:
            errors.append(f"Response Type '{values.get('response_type')}' is not one of: Done / Not done, "
                          "Pass / Fail, Reading or value.")
        required_text = xl.normalize(values.get('is_required', ''))
        if required_text not in ('', 'yes', 'y', 'true', '1', 'no', 'n', 'false', '0'):
            errors.append("Required must be Yes or No.")
        order = values.get('order', '')
        if order and not order.isdigit():
            errors.append(f"Step No. '{order}' is not a whole number.")
        if len(task) > 255 or len(values.get('expected_result', '')) > 255:
            errors.append("Step and Expected Result are limited to 255 characters.")
        if errors:
            report.record('error', row=excel_row, item=item, messages=errors)
            continue
        key = (title.lower(), applies_to.lower(), task_type)
        group = groups.setdefault(key, {'title': title, 'applies_to': applies_to, 'task_type': task_type,
                                        'instructions': '', 'steps': [], 'first_row': excel_row})
        if values.get('instructions') and not group['instructions']:
            group['instructions'] = values['instructions']
        group['steps'].append({
            'row': excel_row, 'item': item,
            'order': int(order) if order else None,
            'task': task, 'guidance': values.get('guidance', ''),
            'expected_result': values.get('expected_result', ''),
            'response_type': response_type,
            'is_required': required_text not in ('no', 'n', 'false', '0'),
        })

    for group in groups.values():
        steps = sorted(group['steps'], key=lambda s: (s['order'] is None, s['order'] or 0, s['row']))
        description = None
        if group['applies_to']:
            description = EquipmentDescription.objects.filter(name__iexact=group['applies_to']).first()
            if description is None and not create_missing:
                for step in steps:
                    report.record('error', row=step['row'], item=step['item'], messages=[
                        f"Equipment '{group['applies_to']}' does not exist. Tick 'Create missing equipment "
                        "descriptions' or fix the name."])
                continue
        exists = ChecklistTemplate.objects.filter(
            title__iexact=group['title'], task_type=group['task_type'], pending_delete=False,
            equipment_description=description,
        ).exists() if (description or not group['applies_to']) else False
        if exists:
            for step in steps:
                report.record('skip', row=step['row'], item=step['item'],
                              messages=["This checklist already exists - not changed."])
            continue
        try:
            with transaction.atomic():
                saved = []
                if group['applies_to'] and description is None:
                    description = EquipmentDescription.objects.create(name=group['applies_to'])
                    saved.append(description)
                    report.add_created("Equipment descriptions", description.name)
                template = ChecklistTemplate.objects.create(
                    title=group['title'], equipment_description=description, task_type=group['task_type'],
                    instructions=group['instructions'], created_by=user,
                )
                saved.append(template)
                for position, step in enumerate(steps, start=1):
                    saved.append(ChecklistItem.objects.create(
                        template=template, order=position, task=step['task'], guidance=step['guidance'],
                        expected_result=step['expected_result'], response_type=step['response_type'],
                        is_required=step['is_required'],
                    ))
        except Exception as exc:
            for step in steps:
                report.record('error', row=step['row'], item=step['item'], messages=xl.error_messages(exc))
            continue
        report.saved.extend(saved)
        for position, step in enumerate(steps, start=1):
            report.record('create', row=step['row'], item=step['item'],
                          messages=[f"Step {position} of {len(steps)} in '{template.title}' ({template.applies_to}, "
                                    f"{template.task_type})"])
