import logging
from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.db.models import Count

from CalSoft.models import CalibrationSession
from CalSoft.forms import TemplateImportForm

logger = logging.getLogger(__name__)


@login_required
def analytics_dashboard(request):
    """Display analytics and trends dashboard."""
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)
    one_year_ago = now - timedelta(days=365)

    total_sessions = CalibrationSession.objects.count()
    sessions_30_days = CalibrationSession.objects.filter(
        timestamp__gte=thirty_days_ago
    ).count()

    passed_sessions = CalibrationSession.objects.filter(
        overall_pass=True).count()
    pass_rate = (passed_sessions / total_sessions *
                 100) if total_sessions > 0 else 0

    top_equipment = CalibrationSession.objects.values(
        'device_model', 'device_manufacturer'
    ).annotate(
        count=Count('id')
    ).order_by('-count')[:10]

    top_procedures = CalibrationSession.objects.values(
        'procedure__name'
    ).annotate(
        count=Count('id')
    ).order_by('-count')[:10]

    monthly_data = []
    for i in range(12):
        month_start = (now.replace(day=1) -
                       timedelta(days=30*i)).replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)

        month_sessions = CalibrationSession.objects.filter(
            timestamp__gte=month_start,
            timestamp__lt=next_month
        ).count()

        monthly_data.append({
            'month': month_start.strftime('%Y-%m'),
            'sessions': month_sessions
        })

    monthly_data.reverse()

    context = {
        'total_sessions': total_sessions,
        'sessions_30_days': sessions_30_days,
        'pass_rate': round(pass_rate, 1),
        'top_equipment': top_equipment,
        'top_procedures': top_procedures,
        'monthly_data': monthly_data,
        'show_sidebar': True,
    }

    return render(request, 'Calibration/analytics_dashboard.html', context)


@login_required
def trend_analysis(request):
    """Display trend analysis for calibration data."""
    context = {
        'trends': [],
        'show_sidebar': True,
    }
    return render(request, 'calibration/trend_analysis.html', context)


@login_required
def performance_analysis(request):
    """Display performance analysis for calibration activities."""
    context = {
        'performance_metrics': {},
        'show_sidebar': True,
    }
    return render(request, 'calibration/performance_analysis.html', context)


@login_required
def reports_dashboard(request):
    """Display dashboard for generating and viewing reports."""
    context = {
        'reports': [],
        'show_sidebar': True,
    }
    return render(request, 'Calibration/reports_dashboard.html', context)