"""HOD report views — per-workshop detail and the landing overview."""
import calendar
import logging
from datetime import date

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse

from workshop.models import Workshop
from users.models import UserProfile
from ..models import Report
from ..utils import (
    get_month_dates, get_quarter_dates,
    get_workshop_performance_data, get_workshop_comparison_data,
)
from .filters import ReportFilterForm

logger = logging.getLogger(__name__)


@login_required
def hod_reports_view(request, workshop_id):
    """View for HOD to see reports for a specific workshop with updated logic and annual support"""
    try:
        profile = request.user.userprofile
        if profile.role != 'HOD':
            logger.warning(f"Unauthorized access attempt by {request.user.username} to HOD reports")
            return HttpResponse("Unauthorized access.", status=403)
    except UserProfile.DoesNotExist:
        logger.warning(f"User profile not found for {request.user.username}")
        return HttpResponse("User profile not found.", status=400)

    # Get the workshop
    try:
        workshop = Workshop.objects.get(id=workshop_id)
    except Workshop.DoesNotExist:
        logger.error(f"Workshop with id {workshop_id} not found")
        return HttpResponse("Workshop not found.", status=404)

    # Initialize form with defaults
    initial_data = {'report_type': 'weekly', 'year': date.today().year}
    form = ReportFilterForm(request.GET or initial_data, user=request.user, initial_workshop=workshop)

    # Base query - reports for this specific workshop
    reports = Report.objects.filter(workshop=workshop)
    report_type = 'weekly'
    year = date.today().year
    start_date = None
    end_date = None
    period_label = 'Week'
    workshop_performance = None

    if form.is_valid():
        report_type = form.cleaned_data.get('report_type', 'weekly')
        year = form.cleaned_data.get('year')

        # Safeguard: ensure year is never None
        if not year:
            year = date.today().year

        if report_type == 'weekly':
            week = form.cleaned_data.get('week')
            if week:
                week_num = int(week)
                start_date = date.fromisocalendar(year, week_num, 1)  # Monday
                end_date = date.fromisocalendar(year, week_num, 7)    # Sunday
                period_label = f"Week {week_num} ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
                reports = reports.filter(period_type='weekly', period_start=start_date)
            else:
                today = date.today()
                week_num = today.isocalendar().week
                start_date = date.fromisocalendar(today.year, week_num, 1)
                end_date = date.fromisocalendar(today.year, week_num, 7)
                period_label = f"Current Week ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
                reports = reports.filter(period_type='weekly', period_start=start_date)

        elif report_type == 'monthly':
            month = form.cleaned_data.get('month')
            if month:
                start_date, end_date = get_month_dates(year, month)
                period_label = f"Monthly ({calendar.month_name[month]} {year})"
                reports = reports.filter(period_type='monthly', period_start=start_date)
            else:
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)
                period_label = f"Yearly ({year})"
                reports = reports.filter(period_type='monthly', period_start__year=year)

        elif report_type == 'quarterly':
            quarter = form.cleaned_data.get('quarter')
            if quarter:
                start_date, end_date = get_quarter_dates(year, quarter)
                period_label = f"Quarterly (Q{quarter} {year})"
                reports = reports.filter(period_type='quarterly', period_start=start_date)
            else:
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)
                period_label = f"All Quarters {year}"
                reports = reports.filter(period_type='quarterly', period_start__year=year)

        elif report_type == 'annual':
            start_date = date(year, 1, 1)
            end_date = date(year, 12, 31)
            period_label = f"Annual Report ({year})"
            reports = reports.filter(period_type='annual', period_start=start_date)
    else:
        # Form invalid - set defaults to current week
        today = date.today()
        year = today.year
        week_num = today.isocalendar().week
        start_date = date.fromisocalendar(year, week_num, 1)
        end_date = date.fromisocalendar(year, week_num, 7)
        period_label = f"Current Week ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
        reports = reports.filter(period_type='weekly', period_start=start_date)

    # Get workshop performance data for the selected period
    if start_date and end_date and report_type != 'annual':
        workshop_performance = get_workshop_performance_data(workshop, start_date, end_date)

    context = {
        'form': form,
        'reports': reports,
        'workshop': workshop,
        'report_type': report_type,
        'period_label': period_label,
        'week_start': start_date,
        'week_end': end_date,
        'workshop_performance': workshop_performance,
        'show_sidebar': True,  # Added sidebar context
    }
    return render(request, 'Reporthub/hod_report.html', context)


@login_required
def hod_reports_landing(request):
    """Landing page for HOD reports overview with updated workshop logic and annual support"""
    try:
        profile = request.user.userprofile
        if profile.role != 'HOD':
            logger.warning(f"Unauthorized access attempt by {request.user.username} to HOD reports landing")
            return HttpResponse("Unauthorized access.", status=403)
    except UserProfile.DoesNotExist:
        logger.warning(f"User profile not found for {request.user.username}")
        return HttpResponse("User profile not found.", status=400)

    form = ReportFilterForm(request.GET or None, user=request.user)
    reports = Report.objects.all().select_related('workshop', 'submitted_by')

    report_type = 'weekly'
    year = date.today().year
    period_label = 'Week'
    start_date, end_date = None, None
    workshop_comparison = None
    selected_workshop = None

    if form.is_valid():
        report_type = form.cleaned_data.get('report_type', 'weekly')
        year = form.cleaned_data.get('year')

        # Safeguard: ensure year is never None
        if not year:
            year = date.today().year

        selected_workshop = form.cleaned_data.get('workshop') if 'workshop' in form.cleaned_data else None

        if selected_workshop:
            reports = reports.filter(workshop=selected_workshop)

        if report_type == 'weekly':
            week = form.cleaned_data.get('week')
            if week:
                week_num = int(week)
                start_date = date.fromisocalendar(year, week_num, 1)  # Monday
                end_date = date.fromisocalendar(year, week_num, 7)    # Sunday
                period_label = f"Week {week_num} ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
                reports = reports.filter(period_type='weekly', period_start=start_date)
            else:
                today = date.today()
                week_num = today.isocalendar().week
                start_date = date.fromisocalendar(today.year, week_num, 1)
                end_date = date.fromisocalendar(today.year, week_num, 7)
                period_label = f"Current Week ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
                reports = reports.filter(period_type='weekly', period_start=start_date)

        elif report_type == 'monthly':
            month = form.cleaned_data.get('month')
            if month:
                start_date, end_date = get_month_dates(year, month)
                period_label = f"Monthly ({calendar.month_name[month]} {year})"
                reports = reports.filter(period_type='monthly', period_start=start_date)
            else:
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)
                period_label = f"Yearly ({year})"
                reports = reports.filter(period_type='monthly', period_start__year=year)

        elif report_type == 'quarterly':
            quarter = form.cleaned_data.get('quarter')
            if quarter:
                start_date, end_date = get_quarter_dates(year, quarter)
                period_label = f"Quarterly (Q{quarter} {year})"
                reports = reports.filter(period_type='quarterly', period_start=start_date)
            else:
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)
                period_label = f"All Quarters {year}"
                reports = reports.filter(period_type='quarterly', period_start__year=year)

        elif report_type == 'annual':
            start_date = date(year, 1, 1)
            end_date = date(year, 12, 31)
            period_label = f"Annual Report ({year})"
            reports = reports.filter(period_type='annual', period_start=start_date)
    else:
        # Form invalid - set defaults to current week
        today = date.today()
        year = today.year
        week_num = today.isocalendar().week
        start_date = date.fromisocalendar(year, week_num, 1)
        end_date = date.fromisocalendar(year, week_num, 7)
        period_label = f"Current Week ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
        reports = reports.filter(period_type='weekly', period_start=start_date)

    # Get workshop comparison data if viewing all workshops
    if not selected_workshop and start_date and end_date:
        workshops = Workshop.objects.all()
        workshop_comparison = get_workshop_comparison_data(workshops, start_date, end_date)

    context = {
        'form': form,
        'reports': reports,
        'workshop': selected_workshop,
        'report_type': report_type,
        'period_label': period_label,
        'week_start': start_date,
        'week_end': end_date,
        'workshop_comparison': workshop_comparison,
        'show_sidebar': True,  # Added sidebar context
    }
    return render(request, 'Reporthub/hod_report.html', context)
