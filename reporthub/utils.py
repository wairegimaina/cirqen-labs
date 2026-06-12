# utils.py

from django.db.models import Count
from django.contrib.auth import get_user_model  # ✅ Changed this line
from jobcard.models import jobcard
from datetime import timedelta, date
from dateutil.relativedelta import relativedelta
from users.models import UserProfile

# ✅ Get the custom user model
User = get_user_model()


def is_hod_user(user):
    """
    Checks if the given user is a Head of department (HOD).
    """
    return user.is_authenticated and hasattr(user, 'userprofile') and user.userprofile.role == 'HOD'


def get_report_data(workshop, start_date, end_date):
    """
    Extract report data for a given date range.
    Updated to use the new workshop field logic from jobcards.
    """
    # If workshop is None, it means the HOD is requesting an aggregate report
    if workshop is None:
        # Aggregate report: Get all job cards performed by technicians across all workshops
        job_cards = jobcard.objects.filter(
            performed_by__userprofile__role='Tech',
            status__in=['Waiting Approval', 'Approved', 'Declined'],  # Updated status names
            date_issued__range=[start_date, end_date]
        )
        technicians = User.objects.filter(
            userprofile__role='Tech',
            userprofile__workshop__isnull=False
        )
    else:
        # Workshop-specific report: Get job cards performed BY this workshop
        # This aligns with the new jobcard logic where workshop field tracks who performed the work
        job_cards = jobcard.objects.filter(
            workshop=workshop,  # Job cards performed BY this workshop
            performed_by__userprofile__role='Tech',
            status__in=['Waiting Approval', 'Approved', 'Declined'],  # Updated status names
            date_issued__range=[start_date, end_date]
        )
        # Technicians from this workshop
        technicians = User.objects.filter(
            userprofile__role='Tech',
            userprofile__workshop=workshop
        )

    # Initialize user data structure
    users = {}
    for tech in technicians:
        username = tech.username
        users[username] = {
            'Approved': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0},
            'Waiting_Approval': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0},
            'Declined': {'PPM': 0, 'Calibration': 0, 'Repair': 0, 'Others': 0, 'total': 0}  # Added Declined status
        }

    # Aggregate job card data
    report_data = job_cards.values(
        'performed_by__username',
        'status',
        'action_taken'
    ).annotate(
        count=Count('id')
    ).order_by('performed_by__username', 'status', 'action_taken')

    # Process the aggregated data
    for entry in report_data:
        username = entry['performed_by__username']
        if not username or username not in users:
            continue

        status = entry['status']
        action = entry['action_taken'] or 'Others'
        count = entry['count']

        # Map status to the correct key
        if status == 'Approved':
            status_key = 'Approved'
        elif status == 'Waiting Approval':
            status_key = 'Waiting_Approval'
        elif status == 'Declined':
            status_key = 'Declined'
        else:
            continue

        # Categorize action types
        if action not in ['PPM', 'Calibration', 'Repair']:
            action = 'Others'

        users[username][status_key][action] = count
        users[username][status_key]['total'] += count

    return users


def get_workshop_performance_data(workshop, start_date, end_date):
    """
    Get performance metrics for a specific workshop.
    This provides additional insights for workshop management.
    """
    if workshop is None:
        return {}

    # Job cards performed BY this workshop
    job_cards = jobcard.objects.filter(
        workshop=workshop,
        date_issued__range=[start_date, end_date]
    )

    performance_data = {
        'total_jobs': job_cards.count(),
        'approved_jobs': job_cards.filter(status='Approved').count(),
        'waiting_jobs': job_cards.filter(status='Waiting Approval').count(),
        'declined_jobs': job_cards.filter(status='Declined').count(),
        'departments_served': job_cards.values_list('department__name', flat=True).distinct().count(),
        'equipment_types_serviced': job_cards.values_list('equipment__description__name', flat=True).distinct().count(),
        'average_approval_rate': 0,
        'technician_productivity': {}
    }

    # Calculate approval rate
    total_processed = performance_data['approved_jobs'] + performance_data['declined_jobs']
    if total_processed > 0:
        performance_data['average_approval_rate'] = (performance_data['approved_jobs'] / total_processed) * 100

    # Technician productivity
    technicians = job_cards.values_list('performed_by__username', flat=True).distinct()
    for tech in technicians:
        if tech:
            tech_jobs = job_cards.filter(performed_by__username=tech)
            performance_data['technician_productivity'][tech] = {
                'total_jobs': tech_jobs.count(),
                'approved_jobs': tech_jobs.filter(status='Approved').count(),
                'waiting_jobs': tech_jobs.filter(status='Waiting Approval').count(),
                'declined_jobs': tech_jobs.filter(status='Declined').count()
            }

    return performance_data


def get_cross_workshop_analysis(start_date, end_date):
    """
    Analyze work done across workshops, particularly useful for understanding
    calibration center work vs regular workshop work.
    """
    # Regular workshop work (excluding calibration centers)
    regular_work = jobcard.objects.filter(
        date_issued__range=[start_date, end_date],
        workshop__category__in=['biomedical', 'engineering']  # Assuming these are regular workshop categories
    ).exclude(workshop__category='calibration_center')

    # Calibration center work
    calibration_work = jobcard.objects.filter(
        date_issued__range=[start_date, end_date],
        workshop__category='calibration_center'
    )

    analysis = {
        'regular_workshops': {
            'total_jobs': regular_work.count(),
            'approved_jobs': regular_work.filter(status='Approved').count(),
            'waiting_jobs': regular_work.filter(status='Waiting Approval').count(),
            'declined_jobs': regular_work.filter(status='Declined').count(),
            'workshops_involved': regular_work.values_list('workshop__name', flat=True).distinct().count()
        },
        'calibration_centers': {
            'total_jobs': calibration_work.count(),
            'approved_jobs': calibration_work.filter(status='Approved').count(),
            'waiting_jobs': calibration_work.filter(status='Waiting Approval').count(),
            'declined_jobs': calibration_work.filter(status='Declined').count(),
            'departments_served': calibration_work.values_list('department__name', flat=True).distinct().count(),
            'equipment_calibrated': calibration_work.values_list('equipment__description__name', flat=True).distinct().count()
        },
        'cross_workshop_insights': {}
    }

    # Identify departments that received work from multiple workshop types
    departments_with_both = set()
    regular_depts = set(regular_work.values_list('department__name', flat=True))
    calibration_depts = set(calibration_work.values_list('department__name', flat=True))
    departments_with_both = regular_depts.intersection(calibration_depts)

    analysis['cross_workshop_insights'] = {
        'departments_receiving_both_types': len(departments_with_both),
        'calibration_coverage_percentage': (len(calibration_depts) / max(1, len(regular_depts))) * 100,
        'workshop_collaboration_indicator': len(departments_with_both) / max(1, len(regular_depts)) * 100
    }

    return analysis


def get_current_week_dates():
    """Get current week start (Monday) and end (Sunday)."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def get_month_dates(year=None, month=None):
    """Get start and end dates for a given month (defaults to current month)."""
    if year is None or month is None:
        today = date.today()
        year, month = today.year, today.month
    start_date = date(year, month, 1)
    end_date = (start_date + relativedelta(months=1) - timedelta(days=1))
    return start_date, end_date


def get_quarter_dates(year=None, quarter=None):
    """Get start and end dates for a given quarter (defaults to current quarter)."""
    today = date.today()
    if year is None:
        year = today.year
    if quarter is None:
        quarter = (today.month - 1) // 3 + 1

    if quarter == 1:
        start_date = date(year, 1, 1)
        end_date = date(year, 3, 31)
    elif quarter == 2:
        start_date = date(year, 4, 1)
        end_date = date(year, 6, 30)
    elif quarter == 3:
        start_date = date(year, 7, 1)
        end_date = date(year, 9, 30)
    else:  # quarter == 4
        start_date = date(year, 10, 1)
        end_date = date(year, 12, 31)

    return start_date, end_date


def get_all_quarters_dates(year=None):
    """Get start and end dates for all four quarters in a given year."""
    if year is None:
        year = date.today().year

    quarters = []
    for q in range(1, 5):
        start, end = get_quarter_dates(year, q)
        quarters.append((f"Q{q}", start, end))

    return quarters


def validate_workshop_access(user, workshop):
    """
    Validate if a user has access to view reports for a specific workshop.
    """
    try:
        profile = user.userprofile

        # HODs can access all workshops
        if profile.role == 'HOD':
            return True

        # Technicians can only access their own workshop
        if profile.role == 'Tech':
            return profile.workshop == workshop

        # Other roles have no access
        return False

    except UserProfile.DoesNotExist:
        return False


def get_workshop_comparison_data(workshops, start_date, end_date):
    """
    Compare performance across multiple workshops.
    Useful for HOD dashboard views.
    """
    comparison_data = {}

    for workshop in workshops:
        workshop_data = get_report_data(workshop, start_date, end_date)
        performance_data = get_workshop_performance_data(workshop, start_date, end_date)

        comparison_data[workshop.name] = {
            'workshop_id': workshop.id,
            'category': workshop.category,
            'technician_count': len(workshop_data),
            'total_jobs': performance_data.get('total_jobs', 0),
            'approval_rate': performance_data.get('average_approval_rate', 0),
            'productivity_score': sum(
                tech_data['Approved']['total'] + tech_data['Waiting_Approval']['total']
                for tech_data in workshop_data.values()
            ) / max(1, len(workshop_data)),  # Average jobs per technician
            'specialization': 'Calibration' if workshop.category == 'calibration_center' else 'Maintenance'
        }

    return comparison_data
