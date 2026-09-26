"""Failure-risk page: devices most likely to need corrective maintenance soon."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from core.scoping import for_user
from Inventory.models import Equipment
from users.control import role_required
from workshop.models import Workshop

from ..prediction import predict

HORIZONS = (30, 60, 90, 180)


@login_required
@role_required('Tech', 'HOD', 'NIC', redirect_to='dashboard:dashboard-main')
def failure_risk(request):
    try:
        horizon = int(request.GET.get('horizon', 90))
    except ValueError:
        horizon = 90
    if horizon not in HORIZONS:
        horizon = 90
    level = request.GET.get('level', '')

    equipment = for_user(Equipment.objects.filter(active_status=True, pending_delete=False), request.user)
    workshops = []
    workshop_filter = request.GET.get('workshop', '')
    if request.user.userprofile.role == 'HOD':
        workshops = Workshop.objects.filter(active_status=True).order_by('name')
        if workshop_filter:
            equipment = equipment.filter(department__workshop_id=workshop_filter)

    # Devices already down are corrective work in progress, not predictions.
    down = equipment.exclude(status='Working')
    predictions = predict(equipment.filter(status='Working'), horizon_days=horizon)
    counts = {'High': 0, 'Medium': 0, 'Low': 0}
    for p in predictions:
        counts[p.risk_level] += 1
    if level in counts:
        predictions = [p for p in predictions if p.risk_level == level]

    return render(request, 'Machine Reports/failure_risk.html', {
        'show_sidebar': True,
        'predictions': predictions[:200],
        'total': len(predictions),
        'counts': counts,
        'down': down.select_related('description', 'department').order_by('status')[:50],
        'down_count': down.count(),
        'horizon': horizon,
        'horizons': HORIZONS,
        'level': level,
        'workshops': workshops,
        'workshop_filter': workshop_filter,
    })
