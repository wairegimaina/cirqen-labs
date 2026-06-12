from django import forms
from datetime import date, timedelta
import calendar
from workshop.models import Workshop
from users.models import UserProfile

# Helper function for weekly choices (can be moved to utils.py if not already there)
def get_weekly_choices(year):
    """Generate weekly choices (Week 1, Week 2, Week 3, Week 4) for a given year."""
    choices = []
    for month in range(1, 13):
        current_month_first_day = date(year, month, 1)
        days_to_monday = (0 - current_month_first_day.weekday() + 7) % 7
        first_monday_of_month = current_month_first_day + timedelta(days=days_to_monday)

        for week_num_in_month in range(1, 5):
            week_start_date = first_monday_of_month + timedelta(days=(week_num_in_month - 1) * 7)
            if week_start_date.month != month:
                break
            
            week_end_date = week_start_date + timedelta(days=6)
            if week_end_date.month != month:
                week_end_date = date(year, month, calendar.monthrange(year, month)[1])

            label = f"Week {week_num_in_month} ({week_start_date.strftime('%B %d')} - {week_end_date.strftime('%B %d, %Y')})"
            choices.append((f"{month}-{week_num_in_month}", label))
    return choices


class HODReportFilterForm(forms.Form):
    """
    Form for HODs to filter submitted reports.
    Allows filtering by workshop, report type, year, month, and quarter.
    """
    REPORT_TYPE_CHOICES = [
        ('', 'All Types'), # Added 'All Types' option
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
    ]
    report_type = forms.ChoiceField(
        choices=REPORT_TYPE_CHOICES,
        required=False, # Make it optional to view all types
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    workshop = forms.ModelChoiceField(
        queryset=Workshop.objects.all().order_by('name'), # HODs can see all workshops
        required=False,
        empty_label="All Workshops",
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    year = forms.IntegerField(
        required=False, # Make year optional for broader search
        min_value=2000,
        max_value=date.today().year + 1, # Allow next year for planning/future reports
        initial=date.today().year,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )

    month = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=12,
        widget=forms.Select(choices=[(i, f"{date(2000, i, 1).strftime('%B')}") for i in range(1, 13)], attrs={'class': 'form-control'})
    )
    
    quarter = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=4,
        widget=forms.Select(choices=[(i, f"Q{i}") for i in range(1, 5)], attrs={'class': 'form-control'})
    )

    week = forms.ChoiceField(
        choices=[],  # Populated dynamically
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate week choices based on the selected or current year
        current_year = self.data.get('year', date.today().year)
        self.fields['week'].choices = [('', 'All Weeks')] + get_weekly_choices(int(current_year))

        # Set initial values if not bound
        if not self.is_bound:
            self.fields['year'].initial = date.today().year
            # Set initial week to current week if report_type is not specified or is weekly
            if self.initial.get('report_type') in ['weekly', ''] or not self.initial.get('report_type'):
                today = date.today()
                # Calculate the current week number for the current date relative to the month
                first_day_of_month = date(today.year, today.month, 1)
                # Find the first Monday of the month
                days_to_monday = (0 - first_day_of_month.weekday() + 7) % 7
                first_monday_of_month = first_day_of_month + timedelta(days=days_to_monday)

                if today >= first_monday_of_month:
                    week_num = ((today - first_monday_of_month).days // 7) + 1
                    # Ensure week_num is within a reasonable range (e.g., up to 4 for monthly weeks)
                    week_num = max(1, min(week_num, 4))
                    self.fields['week'].initial = f"{today.month}-{week_num}"
                else:
                    # If today is before the first Monday of the month, default to the first week
                    self.fields['week'].initial = f"{today.month}-1"

            self.fields['month'].initial = date.today().month
            self.fields['quarter'].initial = (date.today().month - 1) // 3 + 1
