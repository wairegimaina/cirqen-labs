"""Scheduling plans: the explicit rules that decide when equipment is due.

A plan belongs to one workshop and one program (PPM or calibration) and
groups equipment by ONE logic, department or description. For each group it
names the months that group is worked in. Separately, each equipment
description has an interval (how often that kind of device is due), with a
plan-wide default.

Plans are versioned. Editing happens on a draft; activating the draft
supersedes the previous version, and only then are open schedules that no
longer fit reviewed and moved. Completed schedules are never touched.

The engine that turns a plan into schedules is ``scheduling.engine`` (pure)
and ``scheduling.planner`` (database).
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from Inventory.models import Department, EquipmentDescription
from workshop.models import Workshop

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def months_to_mask(months):
    """{2, 8} -> bit mask. Months are 1-12."""
    mask = 0
    for m in months:
        m = int(m)
        if not 1 <= m <= 12:
            raise ValueError(f"Month out of range: {m}")
        mask |= 1 << (m - 1)
    return mask


def mask_to_months(mask):
    """Bit mask -> sorted list of months 1-12."""
    return [m for m in range(1, 13) if mask & (1 << (m - 1))]


def months_label(months):
    return ", ".join(MONTH_NAMES[m - 1] for m in months) or "none"


class SyncedModel(models.Model):
    """Fields every synced table carries (see ``config.sync_tables``)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    active_status = models.BooleanField(default=True)
    pending_delete = models.BooleanField(default=False)
    needs_sync = models.BooleanField(default=True)

    syncable = True

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.needs_sync = True
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"needs_sync", "updated_at"}
        super().save(*args, **kwargs)


class SchedulingPlan(SyncedModel):
    PROGRAM_PPM = "ppm"
    PROGRAM_CALIBRATION = "calibration"
    PROGRAM_CHOICES = [
        (PROGRAM_PPM, "Planned preventive maintenance"),
        (PROGRAM_CALIBRATION, "Calibration"),
    ]

    LOGIC_DEPARTMENT = "department"
    LOGIC_DESCRIPTION = "description"
    LOGIC_CHOICES = [
        (LOGIC_DEPARTMENT, "By department"),
        (LOGIC_DESCRIPTION, "By equipment description"),
    ]

    STATE_DRAFT = "draft"
    STATE_ACTIVE = "active"
    STATE_SUPERSEDED = "superseded"
    STATE_CHOICES = [
        (STATE_DRAFT, "Draft"),
        (STATE_ACTIVE, "Active"),
        (STATE_SUPERSEDED, "Superseded"),
    ]

    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name="scheduling_plans")
    program = models.CharField(max_length=20, choices=PROGRAM_CHOICES)
    logic = models.CharField(max_length=20, choices=LOGIC_CHOICES)
    version = models.PositiveIntegerField()
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=STATE_DRAFT)
    default_interval_months = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Interval for descriptions without their own. Blank: such equipment is "
                  "reported as unschedulable instead of guessed.",
    )
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="scheduling_plans_created",
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="scheduling_plans_activated",
    )

    class Meta:
        ordering = ["workshop__name", "program", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["workshop", "program", "version"], name="scheduling_plan_version_unique"
            ),
            models.UniqueConstraint(
                fields=["workshop", "program"], condition=Q(state="active"),
                name="scheduling_one_active_plan",
            ),
        ]

    def __str__(self):
        return f"{self.workshop} {self.get_program_display()} v{self.version} ({self.state})"

    @property
    def is_department(self):
        return self.logic == self.LOGIC_DEPARTMENT


class SchedulingRule(SyncedModel):
    """The months one group (a department or a description) is worked in."""
    plan = models.ForeignKey(SchedulingPlan, on_delete=models.CASCADE, related_name="rules")
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, null=True, blank=True, related_name="scheduling_rules"
    )
    description = models.ForeignKey(
        EquipmentDescription, on_delete=models.CASCADE, null=True, blank=True,
        related_name="scheduling_rules",
    )
    month_mask = models.PositiveSmallIntegerField(
        default=0, help_text="Bit n-1 set = month n allowed (Jan = bit 0)."
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["plan", "department"], name="scheduling_rule_dept_unique"),
            models.UniqueConstraint(fields=["plan", "description"], name="scheduling_rule_desc_unique"),
            models.CheckConstraint(
                condition=Q(department__isnull=True) ^ Q(description__isnull=True),
                name="scheduling_rule_one_group",
            ),
        ]

    @property
    def months(self):
        return mask_to_months(self.month_mask)

    @months.setter
    def months(self, value):
        self.month_mask = months_to_mask(value)

    @property
    def group_id(self):
        return self.department_id or self.description_id

    @property
    def group_name(self):
        return str(self.department or self.description)

    def clean(self):
        if self.plan_id:
            wants_department = self.plan.is_department
            if wants_department and not self.department_id:
                raise ValidationError("This plan groups by department: choose a department.")
            if not wants_department and not self.description_id:
                raise ValidationError("This plan groups by description: choose a description.")

    def __str__(self):
        return f"{self.group_name}: {months_label(self.months)}"


class SchedulingInterval(SyncedModel):
    """How often one kind of equipment is due, in months, under a plan."""
    plan = models.ForeignKey(SchedulingPlan, on_delete=models.CASCADE, related_name="intervals")
    description = models.ForeignKey(
        EquipmentDescription, on_delete=models.CASCADE, related_name="scheduling_intervals"
    )
    interval_months = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["plan", "description"], name="scheduling_interval_unique"),
            models.CheckConstraint(
                condition=Q(interval_months__gte=1) & Q(interval_months__lte=60),
                name="scheduling_interval_range",
            ),
        ]

    def __str__(self):
        return f"{self.description}: every {self.interval_months} months"
