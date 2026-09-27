"""Admin for scheduling plans: the interim editor until the plan screens exist.

Activating a plan moves open schedules, so it is not an admin action: use
``manage.py scheduling_plan preview`` and ``activate``, which show what will
move first.
"""
from django import forms
from django.contrib import admin

from .models import MONTH_NAMES, SchedulingInterval, SchedulingPlan, SchedulingRule, mask_to_months, \
    months_label, months_to_mask

MONTH_CHOICES = [(str(i), name) for i, name in enumerate(MONTH_NAMES, start=1)]


class RuleForm(forms.ModelForm):
    months = forms.MultipleChoiceField(
        choices=MONTH_CHOICES, widget=forms.CheckboxSelectMultiple, required=False,
    )

    class Meta:
        model = SchedulingRule
        fields = ["department", "description", "months"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["months"] = [str(m) for m in mask_to_months(self.instance.month_mask)]

    def save(self, commit=True):
        self.instance.month_mask = months_to_mask(self.cleaned_data.get("months") or [])
        return super().save(commit)


class RuleInline(admin.TabularInline):
    model = SchedulingRule
    form = RuleForm
    extra = 0


class IntervalInline(admin.TabularInline):
    model = SchedulingInterval
    fields = ["description", "interval_months"]
    extra = 0


@admin.register(SchedulingPlan)
class SchedulingPlanAdmin(admin.ModelAdmin):
    list_display = ["workshop", "program", "logic", "version", "state", "rule_summary", "activated_at"]
    list_filter = ["program", "state", "logic", "workshop"]
    readonly_fields = ["state", "activated_at", "activated_by", "created_by"]
    inlines = [RuleInline, IntervalInline]

    @admin.display(description="Rules")
    def rule_summary(self, plan):
        rules = list(plan.rules.all()[:3])
        text = "; ".join(f"{r.group_name}: {months_label(r.months)}" for r in rules)
        more = plan.rules.count() - len(rules)
        return f"{text} (+{more} more)" if more > 0 else text

    def has_change_permission(self, request, obj=None):
        # Active and superseded versions are the record of what was applied.
        if obj is not None and obj.state != SchedulingPlan.STATE_DRAFT:
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
