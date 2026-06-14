from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render, redirect
from django.http import JsonResponse
from django.db.models import Q
from django.contrib import messages
from django.db import transaction

from CalSoft.models import CalibrationSession, CalibrationSchedule, CalibrationAuditLog
from CalSoft.forms import CalibrationScheduleForm


def is_ajax(request):
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


@login_required
def assign_procedure_to_schedule(request, schedule_id):
    schedule = get_object_or_404(CalibrationSchedule, pk=schedule_id)

    if request.method == 'POST':
        form = CalibrationScheduleForm(request.POST, instance=schedule)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    CalibrationAuditLog.objects.create(
                        user=request.user, action='assign_procedure',
                        description=f'Assigned procedure to schedule {schedule.id}', schedule=schedule)
                    messages.success(request, 'Procedure assigned successfully.')
                    return redirect('calibration:cal-dashboard')
            except Exception as e:
                messages.error(request, f'Error assigning procedure: {str(e)}')
    else:
        form = CalibrationScheduleForm(instance=schedule)

    return render(request, 'Calibration/assign_procedure.html', {
        'schedule': schedule, 'form': form, 'show_sidebar': True
    })