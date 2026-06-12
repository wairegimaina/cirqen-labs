from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django import forms
import logging
import csv
import traceback  # ADD THIS IMPORT
from reportlab.lib.utils import ImageReader
from jobcard.models import jobcard
from workshop.models import Workshop
from users.models import UserProfile
from .models import Report
from .pdf_generators import PDFReportGenerator
from .utils import (
    get_report_data, get_month_dates, get_quarter_dates, get_all_quarters_dates,
    get_workshop_performance_data, get_cross_workshop_analysis, get_workshop_comparison_data,
    validate_workshop_access
)
from datetime import date, timedelta
import calendar
import json

logger = logging.getLogger(__name__)

class ReportFilterForm(forms.Form):
    REPORT_TYPE_CHOICES = [
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('annual', 'Annual'),
    ]

    report_type = forms.ChoiceField(
        choices=REPORT_TYPE_CHOICES,
        required=True,
        initial='weekly',
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    week = forms.ChoiceField(
        choices=[],
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    month = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=12,
        widget=forms.Select(choices=[(i, f"{date(2000, i, 1).strftime('%B')}") for i in range(1, 13)], attrs={'class': 'form-control'})
    )

    year = forms.IntegerField(
        required=True,
        min_value=2000,
        max_value=date.today().year,
        initial=date.today().year,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'required': 'required'})
    )

    quarter = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=4,
        widget=forms.Select(choices=[(i, f"Q{i}") for i in range(1, 5)], attrs={'class': 'form-control'})
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        initial_workshop = kwargs.pop('initial_workshop', None)
        super().__init__(*args, **kwargs)

        # Determine if the user is an HOD
        is_hod = False
        if user:
            try:
                profile = user.userprofile
                if profile.role == 'HOD':
                    is_hod = True
            except UserProfile.DoesNotExist:
                pass

        # Only add the workshop field if the user is an HOD
        if is_hod:
            self.fields['workshop'] = forms.ModelChoiceField(
                queryset=Workshop.objects.all().order_by('name'),
                required=False,
                empty_label="All Workshops",
                widget=forms.Select(attrs={'class': 'form-control'})
            )
            if initial_workshop:
                self.fields['workshop'].initial = initial_workshop

        # Set default week choices for the current year
        year_value = self.data.get('year') if self.data else None
        if not year_value:
            year = date.today().year
        else:
            try:
                year = int(year_value)
            except (ValueError, TypeError):
                year = date.today().year

        self.fields['week'].choices = get_weekly_choices(year)

        # Set default to current week if not provided
        if not self.data.get('week') and not self.initial.get('week'):
            today = date.today()
            current_week = today.isocalendar().week
            self.fields['week'].initial = str(current_week)

def get_weekly_choices(year):
    """Return weekly choices for the whole year with ISO weeks and correct dates."""
    choices = []
    d = date(year, 1, 1)

    # Move to first Monday of the year
    while d.weekday() != 0:
        d += timedelta(days=1)

    while d.year == year:
        start = d
        end = d + timedelta(days=6)
        if end.year != year:
            end = date(year, 12, 31)

        iso_week = start.isocalendar().week
        label = f"Week {iso_week} ({start.strftime('%b %d')} - {end.strftime('%b %d, %Y')})"
        choices.append((str(iso_week), label))

        d += timedelta(days=7)

    return choices

def get_annual_data(workshop, year):
    """Get comprehensive annual data for reports"""
    annual_data = {
        'year': year,
        'total_waiting': 0,
        'total_approved': 0,
        'total_declined': 0,
        'active_technicians': 0,
        'monthly_breakdown': {},
        'peak_month': None,
        'insights': '',
        'recommendations': '',
        'workshop_performance': None,
        'cross_workshop_analysis': None
    }

    monthly_totals = {}
    all_technicians = set()

    # Process each month
    for month in range(1, 13):
        start_date, end_date = get_month_dates(year, month)

        if workshop:
            month_data = get_report_data(workshop, start_date, end_date)
        else:
            month_data = {}
            for ws in Workshop.objects.all():
                ws_data = get_report_data(ws, start_date, end_date)
                for tech, tech_data in ws_data.items():
                    if tech not in month_data:
                        month_data[tech] = {
                            'Waiting_Approval': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0},
                            'Approved': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0},
                            'Declined': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0}
                        }
                    for status in ['Waiting_Approval', 'Approved', 'Declined']:
                        for job_type in ['PPM', 'Calibration', 'Repair', 'Others', 'total']:
                            month_data[tech][status][job_type] += tech_data[status][job_type]

        # Calculate monthly totals
        month_waiting = sum(tech_data['Waiting_Approval']['total'] for tech_data in month_data.values())
        month_approved = sum(tech_data['Approved']['total'] for tech_data in month_data.values())
        month_declined = sum(tech_data['Declined']['total'] for tech_data in month_data.values())
        month_total = month_waiting + month_approved + month_declined

        monthly_totals[month] = month_total
        annual_data['monthly_breakdown'][month] = {
            'waiting': month_waiting,
            'approved': month_approved,
            'declined': month_declined,
            'total': month_total
        }

        annual_data['total_waiting'] += month_waiting
        annual_data['total_approved'] += month_approved
        annual_data['total_declined'] += month_declined
        all_technicians.update(month_data.keys())

    annual_data['active_technicians'] = len(all_technicians)

    # Find peak month
    if monthly_totals:
        peak_month_num = max(monthly_totals, key=monthly_totals.get)
        annual_data['peak_month'] = calendar.month_name[peak_month_num]

    # Generate insights
    total_jobs = annual_data['total_waiting'] + annual_data['total_approved'] + annual_data['total_declined']
    if total_jobs > 0:
        approval_rate = (annual_data['total_approved'] / total_jobs) * 100
        decline_rate = (annual_data['total_declined'] / total_jobs) * 100

        if approval_rate > 80:
            annual_data['insights'] = f"Excellent approval rate of {approval_rate:.1f}% with low decline rate of {decline_rate:.1f}%"
        elif approval_rate > 60:
            annual_data['insights'] = f"Good approval rate of {approval_rate:.1f}% with {decline_rate:.1f}% decline rate"
        else:
            annual_data['insights'] = f"Approval rate of {approval_rate:.1f}% suggests workflow review needed. Decline rate: {decline_rate:.1f}%"

        if annual_data['total_waiting'] > annual_data['total_approved'] * 0.3:
            annual_data['recommendations'] = "Consider reviewing approval processes to reduce backlog."
        elif decline_rate > 15:
            annual_data['recommendations'] = "High decline rate indicates need for quality improvement in job card preparation."
        else:
            annual_data['recommendations'] = "Maintain current operational efficiency levels."

    # Get additional performance data
    if workshop:
        annual_data['workshop_performance'] = get_workshop_performance_data(
            workshop, date(year, 1, 1), date(year, 12, 31)
        )
    else:
        annual_data['cross_workshop_analysis'] = get_cross_workshop_analysis(
            date(year, 1, 1), date(year, 12, 31)
        )

    return annual_data

def get_annual_periods(year):
    """Generate all monthly periods for annual report"""
    periods = []
    for month in range(1, 13):
        start_date, end_date = get_month_dates(year, month)
        period_name = f"{calendar.month_name[month]} {year}"
        periods.append((period_name, start_date, end_date))
    return periods

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
        context['is_hod'] = (profile.role == 'HOD')
        context['is_engineer_incharge'] = (profile.role == 'Tech' and profile.level == 'Engineer Incharge')

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

@login_required
def download_pdf_report(request):
    """Download report as PDF file with proper filter handling"""
    try:
        profile = request.user.userprofile
        is_hod = (profile.role == 'HOD')

        workshop = None
        if is_hod:
            form = ReportFilterForm(request.GET or None, user=request.user)
            if form.is_valid() and 'workshop' in form.cleaned_data:
                workshop = form.cleaned_data.get('workshop')
        else:
            workshop = profile.workshop

        if not is_hod and workshop is None:
            logger.warning(f"User {request.user.username} (non-HOD) has no assigned workshop, cannot download PDF.")
            return HttpResponse("User profile error: No workshop assigned for PDF report generation.", status=400)

    except UserProfile.DoesNotExist:
        logger.warning(f"User profile not found for {request.user.username}")
        return HttpResponse("User profile not found.", status=400)

    # Parse form with GET parameters
    form = ReportFilterForm(request.GET or None, user=request.user)
    report_type = 'weekly'
    start_date = None
    end_date = None
    period_label = 'Week'
    all_periods = []
    year = date.today().year

    if form.is_valid():
        report_type = form.cleaned_data.get('report_type', 'weekly')
        if is_hod and 'workshop' in form.cleaned_data:
            workshop = form.cleaned_data.get('workshop')
        year = form.cleaned_data.get('year', date.today().year)

        if report_type == 'weekly':
            week = form.cleaned_data.get('week')
            if week:
                week_num = int(week)
                start_date = date.fromisocalendar(year, week_num, 1)
                end_date = date.fromisocalendar(year, week_num, 7)
                period_label = f"Week {week_num} ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
                all_periods = [(period_label, start_date, end_date)]
            else:
                today = date.today()
                week_num = today.isocalendar().week
                start_date = date.fromisocalendar(today.year, week_num, 1)
                end_date = date.fromisocalendar(today.year, week_num, 7)
                period_label = f"Current Week ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
                all_periods = [(period_label, start_date, end_date)]

        elif report_type == 'monthly':
            month = form.cleaned_data.get('month')
            if month:
                start_date, end_date = get_month_dates(year, month)
                period_label = f"Monthly ({calendar.month_name[month]} {year})"
            else:
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)
                period_label = f"Yearly ({year})"
            all_periods = [(period_label, start_date, end_date)]

        elif report_type == 'quarterly':
            quarter = form.cleaned_data.get('quarter')
            if quarter:
                start_date, end_date = get_quarter_dates(year, quarter)
                period_label = f"Quarterly (Q{quarter} {year})"
                all_periods = [(period_label, start_date, end_date)]
            else:
                all_periods = get_all_quarters_dates(year)
                period_label = f"All Quarters {year}"

        elif report_type == 'annual':
            all_periods = get_annual_periods(year)
            period_label = f"Annual Report ({year})"
            start_date = date(year, 1, 1)
            end_date = date(year, 12, 31)
    else:
        # Form invalid - use current week defaults
        today = date.today()
        year = today.year
        week_num = today.isocalendar().week
        start_date = date.fromisocalendar(year, week_num, 1)
        end_date = date.fromisocalendar(year, week_num, 7)
        period_label = f"Current Week ({start_date.strftime('%b %d, %Y')} - {end_date.strftime('%b %d, %Y')})"
        all_periods = [(period_label, start_date, end_date)]

    try:
        remarks = ""
        annual_data = None
        workshop_performance = None
        report = None  # Initialize report variable

        # Annual special handling
        if report_type == 'annual':
            annual_data = get_annual_data(workshop, year)

        # Get report object and remarks for non-annual reports
        if workshop and start_date and report_type != 'annual':
            try:
                report = Report.objects.get(workshop=workshop, period_type=report_type, period_start=start_date)
                remarks = report.remarks or ""

                # Debug logging
                logger.info(f"Report found for {workshop.name}, {report_type}, {start_date}")
                logger.info(f"Report submitted_by: {report.submitted_by}")
                logger.info(f"Report submitted_at: {report.submitted_at}")
            except Report.DoesNotExist:
                report = None
                remarks = ""
                logger.info(f"No report found for {workshop.name if workshop else 'None'}, {report_type}, {start_date}")

        # Get workshop performance data for non-annual reports
        if workshop and start_date and end_date and report_type != 'annual':
            workshop_performance = get_workshop_performance_data(workshop, start_date, end_date)

        # Generate PDF with updated context
        pdf_generator = PDFReportGenerator(
            workshop=workshop,
            periods=all_periods,
            report_type=report_type,
            context={
                'workshop': workshop,
                'generated_by': request.user.get_full_name() or request.user.username,
                'period_label': period_label,
                'remarks': remarks,
                'total_waiting_approval': 0,
                'total_approved': 0,
                'total_declined': 0,
                'annual_data': annual_data,
                'workshop_performance': workshop_performance,
                'report': report,  # Pass the report object
                'show_sidebar': True,  # Added sidebar context
            }
        )
        buffer = pdf_generator.generate_pdf()

        # Response with updated filename logic
        response = HttpResponse(content_type='application/pdf')

        # Filename logic
        workshop_name = workshop.name.replace(' ', '_') if workshop else 'All_Workshops'

        if report_type == 'annual':
            filename = f"Annual_Report_{workshop_name}_{year}.pdf"
        elif report_type == 'monthly':
            month = form.cleaned_data.get('month') if form.is_valid() else None
            if month:
                filename = f"Monthly_Report_{workshop_name}_{calendar.month_name[month]}_{year}.pdf"
            else:
                filename = f"Yearly_Report_{workshop_name}_{year}.pdf"
        elif report_type == 'quarterly':
            quarter = form.cleaned_data.get('quarter') if form.is_valid() else None
            if quarter:
                filename = f"Quarterly_Report_{workshop_name}_Q{quarter}_{year}.pdf"
            else:
                filename = f"All_Quarters_Report_{workshop_name}_{year}.pdf"
        else:  # weekly
            week = form.cleaned_data.get('week') if form.is_valid() else None
            if week and start_date and end_date:
                filename = f"Weekly_Report_{workshop_name}_Week{week}_{year}.pdf"
            elif start_date and end_date:
                filename = f"Weekly_Report_{workshop_name}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.pdf"
            else:
                filename = f"Weekly_Report_{workshop_name}_Current.pdf"

        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response.write(buffer.getvalue())
        buffer.close()

        logger.info(f"PDF report generated: {filename} for user {request.user.username}")
        return response

    except Exception as e:
        logger.error(f"PDF generation failed: {str(e)}\n{traceback.format_exc()}")
        return HttpResponse(f"An error occurred while generating the PDF: {str(e)}", status=500)

@login_required
def export_report(request):
    """Export report as CSV file with proper filter handling"""
    try:
        profile = request.user.userprofile
        is_hod = (profile.role == 'HOD')

        workshop = None
        if is_hod:
            form = ReportFilterForm(request.GET or None, user=request.user)
            if form.is_valid() and 'workshop' in form.cleaned_data:
                workshop = form.cleaned_data.get('workshop')
        else:
            workshop = profile.workshop

        if not is_hod and workshop is None:
            logger.warning(f"User {request.user.username} (non-HOD) has no assigned workshop, cannot export CSV.")
            return HttpResponse("User profile error: No workshop assigned for CSV export.", status=400)

    except (UserProfile.DoesNotExist, AttributeError):
        logger.warning(f"User profile error for {request.user.username}")
        return HttpResponse("User profile error", status=400)

    # Parse form with GET parameters
    form = ReportFilterForm(request.GET or None, user=request.user)
    report_type = 'weekly'
    start_date = None
    end_date = None
    users = {}
    year = date.today().year

    if form.is_valid():
        report_type = form.cleaned_data.get('report_type', 'weekly')
        if is_hod and 'workshop' in form.cleaned_data:
            workshop = form.cleaned_data.get('workshop')
        year = form.cleaned_data.get('year', date.today().year)

        if report_type == 'weekly':
            week = form.cleaned_data.get('week')
            if week:
                week_num = int(week)
                start_date = date.fromisocalendar(year, week_num, 1)
                end_date = date.fromisocalendar(year, week_num, 7)
            else:
                today = date.today()
                week_num = today.isocalendar().week
                start_date = date.fromisocalendar(today.year, week_num, 1)
                end_date = date.fromisocalendar(today.year, week_num, 7)

        elif report_type == 'monthly':
            month = form.cleaned_data.get('month')
            if month:
                start_date, end_date = get_month_dates(year, month)
            else:
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)

        elif report_type == 'quarterly':
            quarter = form.cleaned_data.get('quarter')
            if quarter:
                start_date, end_date = get_quarter_dates(year, quarter)
            else:
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)

        elif report_type == 'annual':
            start_date = date(year, 1, 1)
            end_date = date(year, 12, 31)
    else:
        # Form invalid - use current week defaults
        today = date.today()
        year = today.year
        week_num = today.isocalendar().week
        start_date = date.fromisocalendar(year, week_num, 1)
        end_date = date.fromisocalendar(year, week_num, 7)

    # Collect user data based on filters
    try:
        if report_type == 'annual':
            users = {}
            if workshop is None and is_hod:
                # HOD aggregate annual data
                for ws in Workshop.objects.all():
                    for month in range(1, 13):
                        month_start, month_end = get_month_dates(year, month)
                        month_data = get_report_data(ws, month_start, month_end)
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
            elif workshop is not None:
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
            # Non-annual reports
            if workshop is None and is_hod:
                for ws in Workshop.objects.all():
                    users.update(get_report_data(ws, start_date, end_date))
            elif workshop is not None:
                users = get_report_data(workshop, start_date, end_date)
            else:
                users = {}
    except Exception as e:
        logger.error(f"Failed to retrieve report data for CSV export: {str(e)}\n{traceback.format_exc()}")
        return HttpResponse(f"Error retrieving report data: {str(e)}", status=500)

    # Build CSV response
    response = HttpResponse(content_type='text/csv')

    # Filename logic based on filters
    workshop_name = workshop.name.replace(' ', '_') if workshop else 'All_Workshops'

    if report_type == 'annual':
        filename = f"Annual_Report_{workshop_name}_{year}.csv"
    elif report_type == 'monthly':
        month = form.cleaned_data.get('month') if form.is_valid() else None
        if month:
            filename = f"Monthly_Report_{workshop_name}_{calendar.month_name[month]}_{year}.csv"
        else:
            filename = f"Yearly_Report_{workshop_name}_{year}.csv"
    elif report_type == 'quarterly':
        quarter = form.cleaned_data.get('quarter') if form.is_valid() else None
        if quarter:
            filename = f"Quarterly_Report_{workshop_name}_Q{quarter}_{year}.csv"
        else:
            filename = f"All_Quarters_Report_{workshop_name}_{year}.csv"
    else:  # weekly
        week = form.cleaned_data.get('week') if form.is_valid() else None
        if week and start_date and end_date:
            filename = f"Weekly_Report_{workshop_name}_Week{week}_{year}.csv"
        elif start_date and end_date:
            filename = f"Weekly_Report_{workshop_name}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"
        else:
            filename = f"Weekly_Report_{workshop_name}_Current.csv"

    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)

    # CSV Header with metadata
    writer.writerow(['Report Type', report_type.upper()])
    writer.writerow(['Workshop', workshop_name])
    writer.writerow(['Period', f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}" if start_date and end_date else f"Year {year}"])
    writer.writerow(['Generated By', request.user.get_full_name() or request.user.username])
    writer.writerow(['Generated On', timezone.now().strftime('%Y-%m-%d %H:%M:%S')])
    writer.writerow([])  # Empty row

    # Data headers
    writer.writerow([
        'Technician',
        'PPM_Waiting', 'Cal_Waiting', 'Repair_Waiting', 'Others_Waiting', 'Total_Waiting',
        'PPM_Approved', 'Cal_Approved', 'Repair_Approved', 'Others_Approved', 'Total_Approved',
        'PPM_Declined', 'Cal_Declined', 'Repair_Declined', 'Others_Declined', 'Total_Declined'
    ])

    # Data rows
    for username, data in users.items():
        writer.writerow([
            username,
            data['Waiting_Approval']['PPM'],
            data['Waiting_Approval']['Calibration'],
            data['Waiting_Approval']['Repair'],
            data['Waiting_Approval']['Others'],
            data['Waiting_Approval']['total'],
            data['Approved']['PPM'],
            data['Approved']['Calibration'],
            data['Approved']['Repair'],
            data['Approved']['Others'],
            data['Approved']['total'],
            data.get('Declined', {}).get('PPM', 0),
            data.get('Declined', {}).get('Calibration', 0),
            data.get('Declined', {}).get('Repair', 0),
            data.get('Declined', {}).get('Others', 0),
            data.get('Declined', {}).get('total', 0)
        ])

    logger.info(f"CSV report generated: {filename} for user {request.user.username}")
    return response
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
