import logging
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from CalSoft.models import Standard

logger = logging.getLogger(__name__)


@login_required
def StandardsParameters_lists(request):
    standards = Standard.objects.all().order_by("name")
    return render(
        request,
        "Calibrition/standards_parameters_list.html",
        {"standards": standards, "show_sidebar": True},
    )
