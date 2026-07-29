"""The report filter form shared by the reporthub views.

Note: distinct from the app-level ``reporthub/forms.py``; this form is internal
to the views package (adds an HOD-only workshop field and per-year week choices).
"""
from datetime import date

from django import forms

from workshop.models import Workshop
from users.models import UserProfile
from .helpers import get_weekly_choices


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
