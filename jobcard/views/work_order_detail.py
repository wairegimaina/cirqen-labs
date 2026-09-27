"""One work order on screen, with the checklist completed on it."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from core.scoping import get_for_user_or_404

from ..checklists import entries_by_checklist
from ..models import jobcard


@login_required
@require_GET
def work_order_detail(request, jobcard_id):
    card = get_for_user_or_404(
        jobcard.objects.select_related('equipment__description', 'equipment__manufacturer', 'department',
                                       'workshop', 'performed_by', 'verified_by_nurse', 'related_ppm_schedule')
        .prefetch_related('spare_parts__part__name'),
        request.user, id=jobcard_id,
    )
    groups = entries_by_checklist(card)
    entries = [e for _, group in groups for e in group]
    templates = {e.template for e in entries if e.template_id}
    return render(request, 'jobcard/work_order_detail.html', {
        'show_sidebar': True,
        'card': card,
        'checklist_groups': groups,
        'checklist_templates': sorted(templates, key=lambda t: t.title),
        'checklist_total': len(entries),
        'checklist_problems': sum(1 for e in entries if e.is_problem),
        'spare_parts': [sp for sp in card.spare_parts.all() if sp.active_status],
    })
