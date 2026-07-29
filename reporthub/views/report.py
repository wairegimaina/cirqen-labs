"""Main report dashboard page and the weekly-choices JSON endpoint."""
import calendar
import logging
import traceback
from datetime import date

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone

from workshop.models import Workshop
from users.models import UserProfile
from users.control import get_user_role
from ..models import Report
from ..utils import (
    get_report_data, get_month_dates, get_quarter_dates,
    get_workshop_performance_data,
)
from .filters import ReportFilterForm
from .helpers import get_annual_data, get_weekly_choices

logger = logging.getLogger(__name__)


@login_required
def get_weekly_choices_json(request):
    """Returns weekly choices as a JSON response for a given year."""
    year = request.GET.get('year')
    if not year:
        return JsonResponse({'error': 'Year parameter is required'}, status=400)

    try:
        year = int(year)
    except ValueError:
        return JsonResponse({'error': 'Invalid year parameter'}, status=400)

    choices = get_weekly_choices(year)
    return JsonResponse(choices, safe=False)


@login_required
def report_hub(request):
    """
    Main report dashboard view with improved AJAX handling
    """
    # Early AJAX detection
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    # Base context with show_sidebar
    context = {
        'form': None,
        'users': {},
        'workshop': None,
        'workshops': Workshop.objects.all().order_by('name'),
        'total_approved': 0,
        'total_waiting_approval': 0,
        'total_declined': 0,
        'total_cards': 0,
        'is_engineer_incharge': False,
        'is_hod': False,
        'report': None,
        'remarks': "",
        'week_start': None,
        'week_end': None,
        'period_label': 'Week',
        'report_type': 'weekly',
        'has_no_assigned_workshop': False,
        'profile_error': None,
        'annual_data': None,
        'workshop_performance': None,
        'show_sidebar': True,  # Added sidebar context
    }

    # User profile check
    try:
        profile = request.user.userprofile
        context['is_hod'] = (get_user_role(request.user) == 'HOD')
        context['is_engineer_incharge'] = (get_user_role(request.user) == 'Tech' and profile.level == 'Engineer Incharge')

        if not profile.workshop and not context['is_hod']:
            context['profile_error'] = "No workshop assigned to your profile. Please contact administrator."
            context['has_no_assigned_workshop'] = True
            context['form'] = ReportFilterForm(user=request.user)

            if is_ajax:
                return JsonResponse({
                    'error': context['profile_error'],
                    'table_html': '<tr><td colspan="11" class="text-center text-muted"><i class="fas fa-info-circle"></i> ' + context['profile_error'] + '</td></tr>',
                    'summary_html': '<div class="alert alert-warning">' + context['profile_error'] + '</div>'
                }, status=400)
            return render(request, 'Reporthub/report.html', context)

    except UserProfile.DoesNotExist:
        error_msg = "User profile not found. Please contact administrator."
        logger.warning(f"User profile not found for {request.user.username}")

        if is_ajax:
            return JsonResponse({'error': error_msg}, status=400)

        context['profile_error'] = error_msg
        context['form'] = ReportFilterForm(user=request.user)
        return render(request, 'Reporthub/report.html', context)

    # Determine workshop
    workshop = None
    if context['is_hod']:
        form = ReportFilterForm(request.POST if request.method == 'POST' else request.GET, user=request.user)
        if form.is_valid() and 'workshop' in form.cleaned_data:
            workshop = form.cleaned_data.get('workshop')
    else:
        workshop = profile.workshop

    context['workshop'] = workshop

    # Initialize form
    form = ReportFilterForm(request.POST if request.method == 'POST' else request.GET, user=request.user)
    context['form'] = form

    # Default values
    report_type = 'weekly'
    start_date = None
    end_date = None
    period_label = 'Week'
    users = {}

    # Handle filters
    if form.is_valid():
        report_type = form.cleaned_data.get('report_type', 'weekly')
        context['report_type'] = report_type
        year = form.cleaned_data.get('year', date.today().year)

        try:
            if report_type == 'weekly':
                week = form.cleaned_data.get('week')
                if week:
                    week_num = int(week)
                    start_date = date.fromisocalendar(year, week_num, 1)
                    end_date = date.fromisocalendar(year, week_num, 7)
                    period_label = f"Week {week_num} ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
                else:
                    today = date.today()
                    week_num = today.isocalendar().week
                    start_date = date.fromisocalendar(today.year, week_num, 1)
                    end_date = date.fromisocalendar(today.year, week_num, 7)
                    period_label = f"Current Week ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"

            elif report_type == 'monthly':
                month = form.cleaned_data.get('month')
                if month:
                    start_date, end_date = get_month_dates(year, month)
                    period_label = f"Monthly ({calendar.month_name[month]} {year})"
                else:
                    start_date = date(year, 1, 1)
                    end_date = date(year, 12, 31)
                    period_label = f"Yearly ({year})"

            elif report_type == 'quarterly':
                quarter = form.cleaned_data.get('quarter')
                if quarter:
                    start_date, end_date = get_quarter_dates(year, quarter)
                    period_label = f"Quarterly (Q{quarter} {year})"
                else:
                    start_date = date(year, 1, 1)
                    end_date = date(year, 12, 31)
                    period_label = f"All Quarters {year}"

            elif report_type == 'annual':
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)
                period_label = f"Annual Report ({year})"
                context['annual_data'] = get_annual_data(workshop, year)

        except Exception as e:
            error_msg = f"Error processing date parameters: {str(e)}"
            logger.error(error_msg)
            if is_ajax:
                return JsonResponse({'error': error_msg}, status=500)
            context['error'] = error_msg
            return render(request, 'Reporthub/report.html', context)
    else:
        # Default to current week
        today = date.today()
        year = today.year
        week_num = today.isocalendar().week
        start_date = date.fromisocalendar(today.year, week_num, 1)
        end_date = date.fromisocalendar(today.year, week_num, 7)
        period_label = f"Current Week ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
        report_type = 'weekly'
        context['report_type'] = report_type

    context.update({
        'week_start': start_date,
        'week_end': end_date,
        'period_label': period_label,
        'report_type': report_type
    })

    # Get user data
    if start_date and end_date:
        try:
            if report_type == 'annual':
                year = form.cleaned_data.get('year', date.today().year)
                users = {}

                if workshop is None and context['is_hod']:
                    # HOD: aggregate across all workshops
                    for ws in Workshop.objects.all():
                        ws_annual_data = {}
                        for month in range(1, 13):
                            month_start, month_end = get_month_dates(year, month)
                            month_data = get_report_data(ws, month_start, month_end)
                            for tech, tech_data in month_data.items():
                                if tech not in ws_annual_data:
                                    ws_annual_data[tech] = {
                                        'Waiting_Approval': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0},
                                        'Approved': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0},
                                        'Declined': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0}
                                    }
                                for status in ['Waiting_Approval', 'Approved', 'Declined']:
                                    for job_type in ['PPM', 'Calibration', 'Repair', 'Others', 'total']:
                                        ws_annual_data[tech][status][job_type] += tech_data.get(status, {}).get(job_type, 0)
                        users.update(ws_annual_data)
                else:
                    # Single workshop annual data
                    for month in range(1, 13):
                        month_start, month_end = get_month_dates(year, month)
                        month_data = get_report_data(workshop, month_start, month_end)
                        for tech, tech_data in month_data.items():
                            if tech not in users:
                                users[tech] = {
                                    'Waiting_Approval': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0},
                                    'Approved': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0},
                                    'Declined': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0}
                                }
                            for status in ['Waiting_Approval', 'Approved', 'Declined']:
                                for job_type in ['PPM', 'Calibration', 'Repair', 'Others', 'total']:
                                    users[tech][status][job_type] += tech_data.get(status, {}).get(job_type, 0)
            else:
                # Weekly, monthly, quarterly
                if workshop is None and context['is_hod']:
                    for ws in Workshop.objects.all():
                        users.update(get_report_data(ws, start_date, end_date))
                elif workshop is not None:
                    users = get_report_data(workshop, start_date, end_date)

                    if report_type != 'annual':
                        context['workshop_performance'] = get_workshop_performance_data(
                            workshop, start_date, end_date
                        )

        except Exception as e:
            error_msg = f"Failed to retrieve report data: {str(e)}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")

            if is_ajax:
                return JsonResponse({
                    'error': error_msg,
                    'table_html': f'<tr><td colspan="11" class="text-center text-danger"><i class="fas fa-exclamation-triangle"></i> {error_msg}</td></tr>',
                    'summary_html': f'<div class="alert alert-danger">{error_msg}</div>'
                }, status=500)

            context['error'] = error_msg
            return render(request, 'Reporthub/report.html', context)

    context['users'] = users
    context['total_waiting_approval'] = sum(user_data.get('Waiting_Approval', {}).get('total', 0) for user_data in users.values())
    context['total_approved'] = sum(user_data.get('Approved', {}).get('total', 0) for user_data in users.values())
    context['total_declined'] = sum(user_data.get('Declined', {}).get('total', 0) for user_data in users.values())
    context['total_cards'] = context['total_waiting_approval'] + context['total_approved'] + context['total_declined']

    # Handle remarks (only for non-annual reports)
    remarks = ""
    report = None
    if report_type != 'annual':
        try:
            if workshop and start_date:
                report = Report.objects.get(workshop=workshop, period_type=report_type, period_start=start_date)
                remarks = report.remarks
        except Report.DoesNotExist:
            if context['is_engineer_incharge'] and request.method == 'POST':
                remarks = request.POST.get('remarks', '')
                if remarks and workshop:
                    try:
                        report = Report.objects.create(
                            workshop=workshop,
                            period_type=report_type,
                            period_start=start_date,
                            remarks=remarks,
                            submitted_by=request.user,
                            submitted_at=timezone.now()
                        )
                        remarks = report.remarks
                    except Exception as e:
                        error_msg = f"Failed to save remarks: {str(e)}"
                        logger.error(error_msg)
                        if is_ajax:
                            return JsonResponse({'error': error_msg}, status=500)
                        context['error'] = error_msg

    context.update({
        'report': report,
        'remarks': remarks
    })

    if is_ajax:
        from django.template.loader import render_to_string
        from django.urls import reverse

        try:
            # Render partial templates
            table_html = render_to_string('Reporthub/partials/report_table.html', context, request=request)
            summary_html = render_to_string('Reporthub/partials/report_summary.html', context, request=request)
            filter_html = render_to_string('Reporthub/partials/report_filters.html', context, request=request)
            chart_html = render_to_string('Reporthub/partials/report_charts.html', context, request=request)

            # Build download URLs
            query_params = request.GET.copy()
            download_pdf_url = reverse('report_hub:download_pdf_report') + '?' + query_params.urlencode()
            export_csv_url = reverse('report_hub:export_report') + '?' + query_params.urlencode()

            response_data = {
                'table_html': table_html,
                'summary_html': summary_html,
                'chart_html': chart_html,
                'filter_html': filter_html,
                'download_pdf': download_pdf_url,
                'export_csv': export_csv_url,
                'chart_data': {
                    'total_waiting': context['total_waiting_approval'],
                    'total_approved': context['total_approved'],
                    'total_declined': context['total_declined'],  # ADD THIS LINE
                }
            }

            # Add annual breakdown if present
            if context.get('annual_data') and context['annual_data'].get('monthly_breakdown'):
                response_data['chart_data']['annual_breakdown'] = context['annual_data']['monthly_breakdown']

            return JsonResponse(response_data)

        except Exception as e:
            error_msg = f"Error rendering AJAX templates: {str(e)}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            return JsonResponse({
                'error': error_msg,
                'table_html': '<div class="alert alert-danger"><i class="fas fa-exclamation-triangle"></i> Error generating report content</div>',
                'summary_html': '<div class="alert alert-danger">Error generating report content</div>'
            }, status=500)

    return render(request, 'Reporthub/report.html', context)
