"""Pure data-shaping helpers shared by the reporthub views."""
import calendar
from datetime import date, timedelta

from workshop.models import Workshop
from ..utils import (
    get_report_data, get_month_dates,
    get_workshop_performance_data, get_cross_workshop_analysis,
)


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
