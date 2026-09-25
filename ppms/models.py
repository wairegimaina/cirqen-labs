import uuid
from datetime import datetime
from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings
from Inventory.models import Equipment
from django.utils.timezone import now
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
        3. Detects planning_logic changes and sets a warning
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
        if self.pk:
            original = PPMSchedule.objects.filter(pk=self.pk).values(
                'status', 'scheduled_month'
            ).first()
            if original and original['status'] == 'completed':
                if self.status != 'completed':
                    raise ValidationError(
                        f"Cannot change status from 'Completed' to '{self.status}'. "
                        "Completed schedules are locked historical records."
                    )
                month = self.scheduled_month
                if isinstance(month, datetime):
                    month = month.date()
                if month != original['scheduled_month']:
                    raise ValidationError(
                        "Cannot move a completed schedule to another month. "
                        "Completed schedules are locked historical records."
                    )

        # Detect planning_logic change on existing schedules
        if self.pk:
            try:
                original = PPMSchedule.objects.get(pk=self.pk)
                if (
                    original.planning_logic and
                    self.planning_logic and
                    original.planning_logic != self.planning_logic
                ):
                    self.previous_planning_logic = original.planning_logic
                    self.logic_change_warning = (
                        f"Logic change detected: planning logic changed from "
                        f"'{original.planning_logic}' to '{self.planning_logic}' "
                        f"on {now_eat().strftime('%Y-%m-%d %H:%M')}. "
                        f"Run smart_reorganize_ppm_schedules to realign all schedules "
                        f"to the new logic. Scheduled month has NOT been changed — "
                        f"realignment is required."
                    )
            except PPMSchedule.DoesNotExist:
                pass

        # Auto-lock completed schedules (they become historical records)
        if self.status == 'completed' and not self.is_locked:
            self.is_locked = True

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

    @property
    def protection_status(self):
        """
        Get human-readable protection status for this schedule.
        """
        if self.status == 'completed':
            return "PROTECTED: Completed (Historical Record)"
        elif self.is_locked:
            return "PROTECTED: Manually Locked"
        elif self.generation_source == 'signal':
            return "PROTECTED: Signal-Created (Maintains Maintenance Interval)"
        elif self.is_normalizable():
            return "NORMALIZABLE: Can be rescheduled"
        else:
            return "Unknown protection status"

    # ============================================
    # INSTANCE METHODS
    # ============================================

    def is_normalizable(self):
        """
        Check if this schedule can be modified by normalization.
        """
        return (
            self.status in ['pending', 'pushed'] and
            not self.is_locked and
            self.generation_source in ['manual', 'normalization', 'initialization', 'bulk_import']
        )

    def clear_logic_warning(self):
        """
        Clear the logic change warning after smart reorganization is complete.
        Call this after successfully running smart_reorganize_ppm_schedules.
        """
        self.logic_change_warning = ''
        self.previous_planning_logic = ''
        self.save(update_fields=['logic_change_warning', 'previous_planning_logic', 'needs_sync'])

    def mark_as_signal_created(self, parent):
        """
        Mark this schedule as signal-created and link to parent.
        """
        self.generation_source = 'signal'
        self.parent_schedule = parent
        self.expected_maintenance_date = self.scheduled_month
        self.generation_timestamp = now()
        self.save(update_fields=[
            'generation_source',
            'parent_schedule',
            'expected_maintenance_date',
            'generation_timestamp',
            'needs_sync'
        ])

    def lock_schedule(self, reason=""):
        """
        Lock this schedule to prevent modifications.
        """
        self.is_locked = True
        self.save(update_fields=['is_locked', 'needs_sync'])

    def unlock_schedule(self):
        """
        Unlock this schedule to allow modifications.
        Only use if you're sure the schedule should be modifiable.

        Raises:
            ValueError: If trying to unlock a completed schedule
        """
        if self.status == 'completed':
            raise ValueError("Cannot unlock completed schedules - they are historical records")
        self.is_locked = False
        self.save(update_fields=['is_locked', 'needs_sync'])

    def get_maintenance_chain(self):
        """
        Get the full chain of maintenance schedules for this equipment.
        """
        return PPMSchedule.objects.filter(
            equipment=self.equipment
        ).order_by('scheduled_month')

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

    @classmethod
    def get_normalizable_schedules(cls, start_date=None, end_date=None):
        """
        Get all schedules that can be normalized.
        """
        query = cls.objects.filter(
            active_status=True,
            status__in=['pending', 'pushed'],
            is_locked=False,
            generation_source__in=['manual', 'normalization', 'initialization', 'bulk_import']
        )

        if start_date:
            query = query.filter(scheduled_month__gte=start_date)
        if end_date:
            query = query.filter(scheduled_month__lte=end_date)

        return query

    @classmethod
    def get_signal_created_schedules(cls):
        """
        Get all signal-created schedules (protected from normalization).
        """
        return cls.objects.filter(
            generation_source='signal',
            status__in=['pending', 'pushed']
        )

    @classmethod
    def get_protection_summary(cls):
        """
        Get summary of schedule protection status across all schedules.
        """
        from django.db.models import Count, Q

        return {
            'total': cls.objects.count(),
            'completed': cls.objects.filter(status='completed').count(),
            'locked_pending': cls.objects.filter(
                is_locked=True,
                status__in=['pending', 'pushed']
            ).count(),
            'signal_created': cls.objects.filter(
                generation_source='signal',
                status__in=['pending', 'pushed']
            ).count(),
            'normalizable': cls.get_normalizable_schedules().count(),
        }

    def __str__(self):
        return f"Schedule for {self.equipment.description} on {self.scheduled_month} ({self.generation_source})"
