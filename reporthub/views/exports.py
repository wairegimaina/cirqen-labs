"""Report exports — CSV export and PDF download."""
import calendar
import csv
import logging
import traceback
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils import timezone

from workshop.models import Workshop
from users.models import UserProfile
from ..models import Report
from ..pdf_generators import PDFReportGenerator
from ..utils import (
    get_report_data, get_month_dates, get_quarter_dates, get_all_quarters_dates,
    get_workshop_performance_data,
)
from .filters import ReportFilterForm
from .helpers import get_annual_data, get_annual_periods

logger = logging.getLogger(__name__)


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
