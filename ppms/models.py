import uuid
from datetime import datetime
from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings
from Inventory.models import Equipment
from django.utils.timezone import localdate, now
from workshop.models import Workshop
from core.eat import now_eat

STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('completed', 'Completed'),
    ('pushed', 'Pushed'),
]

PLANNING_LOGIC_CHOICES = [
    ('description_based', 'Description Based'),
    ('date_based', 'Date Based'),
    # Aliases used by tasks and signals modules
    ('department', 'Date Based (Department)'),
    ('description', 'Description Based (Alias)'),
]

# Generation source choices for tracking how schedules are created
GENERATION_SOURCE_CHOICES = [
    ('manual', 'Manual Creation'),
    ('normalization', 'Created by Normalization'),
    ('signal', 'Auto-created after Completion'),
    ('initialization', 'Initial System Setup'),
    ('bulk_import', 'Bulk Import'),
    ('auto_advance', 'Auto-Advanced (Group-Aware)'),
    ('reconciliation', 'Created by Reconciliation'),
    ('group_alignment', 'Aligned to Group Month'),
    ('group_fix', 'Fixed Group Alignment'),
    ('locker', 'Created by Locker'),
    ('job_card', 'Triggered by Job Card Completion'),
    ('plan', 'Scheduling Plan'),
]


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,           # custom user reference
        on_delete=models.CASCADE,
        related_name="ppms_audit_logs"      # give a unique related_name
    )
    action = models.CharField(max_length=50)
    description = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)


    # offline sync
    needs_sync = models.BooleanField(default=True)
    syncable = True  # important for sync tasks

    def __str__(self):
        return f"{self.user.username} - {self.action} - {self.timestamp}"



class PPMSchedule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE)
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, help_text="Workshop managing this schedule")
    scheduled_month = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    planning_logic = models.CharField(
        max_length=50,
        choices=PLANNING_LOGIC_CHOICES,
        null=True,
        blank=True,
        help_text="Logic used to plan this schedule (e.g., description-based)"
    )
    maintenance_period = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maintenance interval in months"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    syncable = True  # <- important, so sync task knows to sync this model

    # ============================================
    # SMART ORGANIZER FIELDS (new)
    # ============================================

    generation_source = models.CharField(
        max_length=20,
        choices=GENERATION_SOURCE_CHOICES,
        default='manual',
        db_index=True,
        help_text='How this schedule was created — signal-created schedules are protected from normalization'
    )

    parent_schedule = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='child_schedules',
        help_text='The completed schedule that triggered auto-creation of this schedule (for signal-created)'
    )

    is_locked = models.BooleanField(
        default=False,
        db_index=True,
        help_text='If True, this schedule cannot be modified by normalization or bulk operations'
    )

    expected_maintenance_date = models.DateField(
        null=True,
        blank=True,
        help_text='Expected maintenance date calculated from parent completion (for signal-created schedules)'
    )

    generation_timestamp = models.DateTimeField(
        default=now,
        help_text='When this schedule was generated/created'
    )

    logic_change_warning = models.TextField(
        blank=True,
        default='',
        help_text=(
            'Warning message set when planning_logic changes on an existing schedule. '
            'Cleared once the schedule is re-normalized under the new logic.'
        )
    )

    previous_planning_logic = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text='The planning logic that was in use before the last logic change (for smart reorganizer)'
    )

    completed_date = models.DateField(
        null=True, blank=True,
        help_text='The day the maintenance was actually done (scheduled_month is when it was due)'
    )

    # ============================================
    # SCHEDULING PLAN (scheduling app)
    # ============================================

    due_month = models.DateField(
        null=True, blank=True,
        help_text="The month this schedule is due in its cycle. A manual push moves "
                  "scheduled_month but not this, so the cycle continues from the slot.",
    )
    plan = models.ForeignKey(
        "scheduling.SchedulingPlan", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="%(app_label)s_schedules",
        help_text="The plan version that placed this schedule (blank: legacy scheduler)",
    )
    schedule_reason = models.JSONField(
        null=True, blank=True,
        help_text="Why this month: group, months, interval, previous schedule, rule applied",
    )

    # ============================================
    # META
    # ============================================

    class Meta:
        # Add unique constraint to prevent duplicate schedules
        unique_together = ['equipment', 'scheduled_month']
        indexes = [
            models.Index(fields=['workshop', 'scheduled_month']),
            models.Index(fields=['equipment', 'status']),
            models.Index(fields=['generation_source', 'status']),
            models.Index(fields=['is_locked']),
            models.Index(fields=['scheduled_month', 'status']),
        ]

    def save(self, *args, **kwargs):
        """
        Enhanced save method that:
        1. Sets needs_sync flag
        2. Auto-assigns workshop from equipment
        4. Auto-locks completed schedules (historical records)
        5. Sets expected_maintenance_date for signal-created schedules
        """
        self.needs_sync = True

        # Set workshop from equipment department
        if self.equipment_id and self.equipment.department:
            self.workshop = self.equipment.department.workshop

        # Completed schedules are historical records: their month and status
        # never change. Sync writes completions with raw SQL, so this only
        # guards the ORM paths (edit view, bulk actions, tasks).
        before = None
        if self.pk:
            before = PPMSchedule.objects.filter(pk=self.pk).values(
                'status', 'scheduled_month'
            ).first()
            if before and before['status'] == 'completed':
                if self.status != 'completed':
                    raise ValidationError(
                        f"Cannot change status from 'Completed' to '{self.status}'. "
                        "Completed schedules are locked historical records."
                    )
                month = self.scheduled_month
                if isinstance(month, datetime):
                    month = month.date()
                if month != before['scheduled_month']:
                    raise ValidationError(
                        "Cannot move a completed schedule to another month. "
                        "Completed schedules are locked historical records."
                    )

        # Auto-lock completed schedules (they become historical records)
        if self.status == 'completed' and not self.is_locked:
            self.is_locked = True

        # Record the day it was done, only at the moment it becomes completed:
        # re-saving an old completed row must not stamp today on it.
        newly_completed = not before or before['status'] != 'completed'
        if self.status == 'completed' and newly_completed and not self.completed_date:
            self.completed_date = localdate()
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'completed_date'}

        # Calculate expected_maintenance_date for signal-created schedules
        if self.generation_source == 'signal' and self.parent_schedule and not self.expected_maintenance_date:
            self.expected_maintenance_date = self.scheduled_month

        super().save(*args, **kwargs)

    # ============================================
    # PROPERTIES
    # ============================================

    @property
    def manufacturer(self):
        return self.equipment.manufacturer

    @property
    def model(self):
        return self.equipment.model

    @property
    def serial_number(self):
        return self.equipment.serial_number

    @property
    def due_date(self):
        """Returns the last day of the scheduled month as the official due date."""
        from calendar import monthrange
        if not self.scheduled_month:
            return None
        last_day = monthrange(self.scheduled_month.year, self.scheduled_month.month)[1]
        return self.scheduled_month.replace(day=last_day)

    @property
    def is_overdue(self):
        """True if the due date (last day of scheduled month) has passed and status is not completed."""
        from django.utils import timezone
        if not self.scheduled_month or self.status == 'completed':
            return False
        return timezone.localdate() > self.due_date

    # ============================================
    # CLASS METHODS (QUERIES)
    # ============================================

    @classmethod
    def open_schedules(cls):
        """Schedules still to be done: live rows that are not completed.

        An equipment is scheduled when it has one of these. Having only
        completed history means its chain broke and it needs a next schedule.
        """
        return cls.objects.filter(active_status=True, pending_delete=False).exclude(status='completed')

    def __str__(self):
        return f"Schedule for {self.equipment.description} on {self.scheduled_month} ({self.generation_source})"
