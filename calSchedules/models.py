import uuid
import logging
from django.db import models
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)
from django.utils.timezone import now
from django.utils import timezone
from django.conf import settings
from Inventory.models import Equipment
from workshop.models import Workshop
from CalSoft.models import CalibrationProcedure
from core.eat import now_eat

STATUS_CHOICES = [
    ("pending", "Pending"),
    ("pushed", "Pushed"),
    ("in_progress", "In Progress"),
    ("pending_approval", "Pending Approval"),
    ("completed", "Completed"),
    ("overdue", "Overdue"),
]

PLANNING_LOGIC_CHOICES = [
    ("description_based", "Description Based"),
    ("date_based", "Date Based"),
    # Aliases used by instant_reconciliation and locker modules
    ("department", "Date Based (Department)"),
    ("description", "Description Based (Alias)"),
]

CALIBRATION_PERIOD_CHOICES = [
    (6, "6 Months"),
    (12, "12 Months"),
]

# ✅ NEW: Generation source choices for tracking how schedules are created
GENERATION_SOURCE_CHOICES = [
    ("manual", "Manual Creation"),
    ("normalization", "Created by Normalization"),
    ("signal", "Auto-created after Completion"),
    ("initialization", "Initial System Setup"),
    ("bulk_import", "Bulk Import"),
    ("auto_advance", "Auto-Advanced (Group-Aware)"),
    ("reconciliation", "Created by Reconciliation"),
    ("group_alignment", "Aligned to Group Month"),
    ("group_fix", "Fixed Group Alignment"),
    ("locker", "Created by Locker"),
    ("job_card", "Triggered by Job Card Completion"),
]


class CalibrationAuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calschedules_audit_logs"
    )

    action = models.CharField(max_length=50)
    description = models.TextField()
    pending_delete = models.BooleanField(default=False)

    timestamp = models.DateTimeField(auto_now_add=True)
    schedule = models.ForeignKey(
        "CalibrationSchedule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    needs_sync = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    active_status = models.BooleanField(default=True)
    syncable = True

    def save(self, *args, **kwargs):
        self.needs_sync = True
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.action} - {self.timestamp}"


class CalibrationSchedule(models.Model):
    """
    Enhanced Calibration Schedule with Generation Tracking

    This model tracks:
    - How each schedule was created (generation_source)
    - Parent-child relationships between schedules (parent_schedule)
    - Lock status to prevent modifications (is_locked)
    - Expected calibration dates (expected_calibration_date)

    These fields enable multi-year normalization while protecting signal-created schedules
    and maintaining proper calibration intervals.
    """

    # ============================================
    # EXISTING FIELDS
    # ============================================
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(
        Equipment, on_delete=models.CASCADE, related_name="calschedules_schedules"
    )
    workshop = models.ForeignKey(
        Workshop,
        on_delete=models.CASCADE,
        related_name="calschedules_schedules",
        help_text="Workshop managing this calibration schedule",
    )
    calibration_procedure = models.ForeignKey(
        CalibrationProcedure,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calschedules_schedules",
    )
    scheduled_month = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    planning_logic = models.CharField(
        max_length=50,
        choices=PLANNING_LOGIC_CHOICES,
        null=True,
        blank=True,
        help_text="Logic used to plan this schedule (e.g., description-based)",
    )
    calibration_period = models.PositiveIntegerField(
        choices=CALIBRATION_PERIOD_CHOICES, default=12, help_text="Calibration interval in months"
    )
    estimated_duration = models.DurationField(
        null=True, blank=True, help_text="Estimated duration for the calibration procedure"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    description = models.TextField(blank=True, help_text="Description of the calibration schedule")
    completed_date = models.DateField(null=True, blank=True)
    needs_sync = models.BooleanField(default=True)
    syncable = True

    # ============================================
    # NEW FIELDS FOR GENERATION TRACKING
    # ============================================

    generation_source = models.CharField(
        max_length=20,
        choices=GENERATION_SOURCE_CHOICES,
        default="manual",
        db_index=True,
        help_text="How this schedule was created - signal-created schedules are protected from normalization",
    )

    parent_schedule = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_schedules",
        help_text="The completed schedule that triggered auto-creation of this schedule (for signal-created)",
    )

    is_locked = models.BooleanField(
        default=False,
        db_index=True,
        help_text="If True, this schedule cannot be modified by normalization or bulk operations",
    )

    expected_calibration_date = models.DateField(
        null=True,
        blank=True,
        help_text="Expected calibration date calculated from parent completion (for signal-created schedules)",
    )

    generation_timestamp = models.DateTimeField(
        default=timezone.now, help_text="When this schedule was generated/created"
    )

    # ============================================
    # LOGIC CHANGE WARNING TRACKING
    # ============================================

    logic_change_warning = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Warning message set when planning_logic changes on an existing schedule. "
            "Cleared once the schedule is re-normalized under the new logic."
        ),
    )

    previous_planning_logic = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="The planning logic that was in use before the last logic change (for smart reorganizer)",
    )

    # ============================================
    # META AND CONSTRAINTS
    # ============================================

    class Meta:
        unique_together = ["equipment", "scheduled_month"]
        indexes = [
            models.Index(fields=["workshop", "scheduled_month"]),
            models.Index(fields=["equipment", "status"]),
            models.Index(fields=["generation_source", "status"]),
            models.Index(fields=["is_locked"]),
            models.Index(fields=["scheduled_month", "status"]),
        ]
        ordering = ["scheduled_month", "equipment"]
        verbose_name = "Calibration Schedule"
        verbose_name_plural = "Calibration Schedules"

    # ============================================
    # SAVE METHOD
    # ============================================

    def save(self, *args, **kwargs):
        """
        Enhanced save method that:
        1. Sets needs_sync flag
        2. Auto-assigns workshop from equipment
        3. Validates state transitions (explicit overdue state)
        4. Auto-locks completed schedules (historical records)
        5. Sets expected_calibration_date for signal-created schedules
        6. Detects planning_logic changes and sets a warning
        7. NEVER changes scheduled_month based on actual calibration date —
           the month stands as originally scheduled regardless of early/late completion
        """
        self.needs_sync = True

        # Set workshop from equipment department
        if self.equipment_id and self.equipment.department:
            self.workshop = self.equipment.department.workshop

        # Detect planning_logic change on existing schedules
        if self.pk:
            try:
                original = CalibrationSchedule.objects.get(pk=self.pk)
                if (
                    original.planning_logic
                    and self.planning_logic
                    and original.planning_logic != self.planning_logic
                ):
                    self.previous_planning_logic = original.planning_logic
                    self.logic_change_warning = (
                        f"⚠️ LOGIC CHANGE DETECTED: Planning logic changed from "
                        f"'{original.planning_logic}' → '{self.planning_logic}' "
                        f"on {now_eat().strftime('%Y-%m-%d %H:%M')}. "
                        f"Run smart_reorganize_on_logic_change to realign all schedules "
                        f"to the new logic. Scheduled month has NOT been changed — "
                        f"realignment is required."
                    )
            except CalibrationSchedule.DoesNotExist:
                pass

        # Validate state transitions
        if self.pk:
            try:
                original = CalibrationSchedule.objects.get(pk=self.pk)
                old_status = original.status
                new_status = self.status

                if old_status != new_status:
                    # Completed is terminal - no regress allowed
                    if old_status == "completed" and new_status != "completed":
                        raise ValidationError(
                            f"Cannot change status from 'Completed' to '{new_status}'. "
                            "Completed schedules are locked historical records."
                        )

                    # Overdue can be set from active states
                    if new_status == "overdue" and old_status not in (
                        "pending",
                        "pushed",
                        "in_progress",
                    ):
                        raise ValidationError(
                            f"Cannot set status to 'Overdue' from '{old_status}'. "
                            "Overdue applies only to active schedules."
                        )

                    # Overdue can be cleared back to active states (e.g., grace period applied)
                    if old_status == "overdue" and new_status not in (
                        "pending",
                        "pushed",
                        "in_progress",
                        "completed",
                    ):
                        raise ValidationError(
                            f"Cannot change status from 'Overdue' to '{new_status}'. "
                            "Use Pending, Pushed, In Progress, or Completed."
                        )

                    # Pushed should not come from completed
                    if new_status == "pushed" and old_status == "completed":
                        raise ValidationError(
                            "Cannot push a completed schedule. " "Create a new schedule instead."
                        )

                    # Log the transition
                    logger.info(
                        f"Schedule {self.id}: status transition "
                        f"'{old_status}' → '{new_status}' by user "
                        f"{getattr(self, '_changed_by_user', 'system')}"
                    )
            except CalibrationSchedule.DoesNotExist:
                pass

        # Auto-lock completed schedules (they become historical records)
        if self.status == "completed" and not self.is_locked:
            self.is_locked = True

        # ✅ Calculate expected_calibration_date for signal-created schedules
        if (
            self.generation_source == "signal"
            and self.parent_schedule
            and not self.expected_calibration_date
        ):
            self.expected_calibration_date = self.scheduled_month

        # ✅ CRITICAL: scheduled_month NEVER changes based on actual calibration date.
        # Whether the equipment was calibrated early or late, the scheduled_month
        # remains the month it was originally planned for. completed_date records
        # the actual calibration date without overriding scheduled_month.

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
        """The last day of the scheduled month — the official due date.

        A calibration is due within a month, so the whole month is available to
        schedule the visit. Shared with every other due-date calculation via
        ``calSchedules.grouping.month_end``.
        """
        from calSchedules.grouping import month_end

        return month_end(self.scheduled_month)

    @property
    def is_overdue(self):
        """
        True when this schedule is overdue.
        Overdue = last day of scheduled_month has passed, and the schedule
        is still active (pending / pushed / in_progress), or the status
        has already been explicitly marked as 'overdue'.
        """
        from django.utils import timezone

        if not self.scheduled_month:
            return False
        if self.status in ("completed", "pending_approval"):
            return False
        if self.status == "overdue":
            return True
        return timezone.localdate() > self.due_date

    @property
    def days_until_due(self):
        """Days remaining until the last day of the scheduled month."""
        if not self.scheduled_month:
            return None
        from django.utils import timezone

        return (self.due_date - timezone.localdate()).days

    @property
    def protection_status(self):
        """
        Get human-readable protection status for this schedule.

        Returns:
            str: Description of protection level
        """
        if self.status == "completed":
            return "🔒 PROTECTED: Completed (Historical Record)"
        elif self.is_locked:
            return "🔒 PROTECTED: Manually Locked"
        elif self.generation_source == "signal":
            return "🔒 PROTECTED: Signal-Created (Maintains Calibration Interval)"
        elif self.is_normalizable():
            return "✏️ NORMALIZABLE: Can be rescheduled"
        else:
            return "❓ Unknown protection status"

    # ============================================
    # INSTANCE METHODS
    # ============================================

    def is_normalizable(self):
        """
        Check if this schedule can be modified by normalization.

        Uses a denylist rather than an allowlist so that any new
        generation_source added in the future is automatically
        normalizable unless explicitly protected here.

        Protected sources (never moved):
          - signal    : auto-created after completion; maintains calibration interval
          - locker    : created by the locker module; should stay put
          - job_card  : triggered by actual job-card completion; interval matters

        Returns:
            bool: True if can be normalized, False otherwise
        """
        PROTECTED_SOURCES = {"signal", "locker", "job_card"}
        return (
            self.status in ["pending", "pushed"]
            and not self.is_locked
            and self.generation_source not in PROTECTED_SOURCES
        )

    def clear_logic_warning(self):
        """
        Clear the logic change warning after smart reorganization is complete.
        Call this after successfully running smart_reorganize_on_logic_change.
        """
        self.logic_change_warning = ""
        self.previous_planning_logic = ""
        self.save(update_fields=["logic_change_warning", "previous_planning_logic", "needs_sync"])

    def mark_as_signal_created(self, parent):
        """
        Mark this schedule as signal-created and link to parent.

        Args:
            parent (CalibrationSchedule): The completed schedule that triggered creation
        """
        self.generation_source = "signal"
        self.parent_schedule = parent
        self.expected_calibration_date = self.scheduled_month
        self.generation_timestamp = timezone.now()
        self.save(
            update_fields=[
                "generation_source",
                "parent_schedule",
                "expected_calibration_date",
                "generation_timestamp",
                "needs_sync",
            ]
        )

    def lock_schedule(self, reason=""):
        """
        Lock this schedule to prevent modifications.

        Args:
            reason (str): Optional reason for locking (for logging)
        """
        self.is_locked = True
        self.save(update_fields=["is_locked", "needs_sync"])

    def unlock_schedule(self):
        """
        Unlock this schedule to allow modifications.
        Only use if you're sure the schedule should be modifiable.

        Raises:
            ValueError: If trying to unlock a completed schedule
        """
        if self.status == "completed":
            raise ValueError("Cannot unlock completed schedules - they are historical records")
        self.is_locked = False
        self.save(update_fields=["is_locked", "needs_sync"])

    def get_calibration_chain(self):
        """
        Get the full chain of calibrations for this equipment.
        Useful for tracking calibration history.

        Returns:
            QuerySet of CalibrationSchedule objects for this equipment, ordered by date
        """
        return CalibrationSchedule.objects.filter(equipment=self.equipment).order_by(
            "scheduled_month"
        )

    # ============================================
    # CLASS METHODS (QUERIES)
    # ============================================

    @classmethod
    def get_normalizable_schedules(cls, start_date=None, end_date=None):
        """
        Get all schedules that can be normalized.

        Uses a denylist rather than an allowlist so that any new
        generation_source added in the future is automatically
        normalizable unless explicitly protected here.

        Protected sources (never moved):
          - signal    : auto-created after completion; maintains calibration interval
          - locker    : created by the locker module; should stay put
          - job_card  : triggered by actual job-card completion; interval matters

        Args:
            start_date (date): Optional start date filter
            end_date (date): Optional end date filter

        Returns:
            QuerySet: Schedules that can be normalized
        """
        PROTECTED_SOURCES = ["signal", "locker", "job_card"]

        query = cls.objects.filter(
            active_status=True,
            status__in=["pending", "pushed"],
            is_locked=False,
        ).exclude(generation_source__in=PROTECTED_SOURCES)

        if start_date:
            query = query.filter(scheduled_month__gte=start_date)
        if end_date:
            query = query.filter(scheduled_month__lte=end_date)

        return query

    @classmethod
    def get_signal_created_schedules(cls):
        """
        Get all signal-created schedules (protected from normalization).

        Returns:
            QuerySet: Signal-created schedules
        """
        return cls.objects.filter(generation_source="signal", status__in=["pending", "pushed"])

    @classmethod
    def get_protection_summary(cls):
        """
        Get summary of schedule protection status across all schedules.

        Returns:
            dict: Counts by protection category
        """
        from django.db.models import Count, Q

        return {
            "total": cls.objects.count(),
            "completed": cls.objects.filter(status="completed").count(),
            "locked_pending": cls.objects.filter(
                is_locked=True, status__in=["pending", "pushed"]
            ).count(),
            "signal_created": cls.objects.filter(
                generation_source="signal", status__in=["pending", "pushed"]
            ).count(),
            "normalizable": cls.get_normalizable_schedules().count(),
        }

    # ============================================
    # STRING REPRESENTATION
    # ============================================

    def __str__(self):
        return (
            f"Calibration Schedule for {self.equipment.description} "
            f"on {self.scheduled_month} ({self.generation_source})"
        )
