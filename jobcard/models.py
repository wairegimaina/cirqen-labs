import uuid
import logging
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from decimal import Decimal

User = get_user_model()
logger = logging.getLogger(__name__)

from parts_tools.models import Accessories
from Inventory.models import Department, Equipment
from workshop.models import Workshop


class jobcard(models.Model):
    PRIORITY_CHOICES = [
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High'),
        ('Urgent', 'Urgent'),
    ]

    ACTION_CHOICES = [
        ('PPM', 'PPM'),
        ('Calibration', 'Calibration'),
        ('Repair', 'Repair'),
        ('Others', 'Others'),
    ]

    STATUS_CHOICES = [
        ('Waiting Approval', 'Waiting Approval'),
        ('Approved', 'Approved'),
        ('Declined', 'Declined'),
    ]

    department = models.ForeignKey(Department, on_delete=models.CASCADE)
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workshop = models.ForeignKey(
        Workshop,
        on_delete=models.CASCADE,
        help_text="Workshop that performed this job (may differ from department's workshop for calibration centers)"
    )
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    priority_level = models.CharField(max_length=20, choices=PRIORITY_CHOICES)
    action_taken = models.CharField(max_length=50, choices=ACTION_CHOICES)
    job_description = models.TextField()

    time_started = models.TimeField(null=True, blank=True)
    time_completed = models.TimeField(null=True, blank=True)

    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_jobcards')

    technician_signed_date = models.DateTimeField(null=True, blank=True)

    tech_signature = models.TextField(
        blank=True,
        null=True,
        help_text="Technician signature data (base64 encoded image)"
    )
    verified_signature = models.TextField(
        blank=True,
        null=True,
        help_text="Nurse signature data (base64 encoded image)"
    )

    decline_reason = models.TextField(blank=True, null=True)
    nurse_signed_date = models.DateTimeField(null=True, blank=True)
    nurse_name = models.CharField(max_length=100, null=True, blank=True)
    verified_by_nurse = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_jobcards',
        help_text="The nurse who verified/approved this job card"
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Waiting Approval')
    date_issued = models.DateField(auto_now_add=True)
    stock_deducted = models.BooleanField(default=False, help_text="Whether stock has been deducted for this job card")

    # Cost tracking fields
    labor_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Cost of labor for this job"
    )
    total_parts_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Total cost of all spare parts used (auto-calculated)"
    )
    additional_costs = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Any additional costs (travel, external services, etc.)"
    )
    additional_costs_description = models.TextField(
        blank=True,
        null=True,
        help_text="Description of additional costs"
    )

    # ✅ NEW FIELD: PPM Schedule Link
    related_ppm_schedule = models.ForeignKey(
        'ppms.PPMSchedule',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='job_cards',
        help_text="Link to PPM schedule if this job card is for scheduled preventive maintenance"
    )

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    syncable = True

    def calculate_total_parts_cost(self):
        """Calculate total cost of all spare parts used"""
        total = Decimal('0.00')
        for spare_part in self.spare_parts.all():
            total += spare_part.get_total_cost()
        return total

    def get_total_cost(self):
        """Calculate total cost of job card (parts + labor + additional)"""
        return self.total_parts_cost + self.labor_cost + self.additional_costs

    def update_costs(self):
        """Update total parts cost based on spare parts"""
        self.total_parts_cost = self.calculate_total_parts_cost()
        self.save(update_fields=['total_parts_cost', 'updated_at'])

    def deduct_stock(self):
        """Deduct stock for all spare parts when job card is approved"""
        if self.stock_deducted:
            return

        for spare_part in self.spare_parts.all():
            if spare_part.part and spare_part.quantity > 0:
                accessory = spare_part.part
                if accessory.stock_count >= spare_part.quantity:
                    accessory.stock_count -= spare_part.quantity
                    accessory.save(update_fields=['stock_count', 'updated_at'])
                else:
                    raise ValidationError(
                        f"Insufficient stock for {accessory.name}. "
                        f"Available: {accessory.stock_count}, Required: {spare_part.quantity}"
                    )

        # Update costs when approving
        self.update_costs()
        self.stock_deducted = True
        self.save(update_fields=['stock_deducted', 'updated_at'])

    def restore_stock(self):
        """Restore stock if job card is declined after being approved"""
        if not self.stock_deducted:
            return

        for spare_part in self.spare_parts.all():
            if spare_part.part and spare_part.quantity > 0:
                accessory = spare_part.part
                accessory.stock_count += spare_part.quantity
                accessory.save(update_fields=['stock_count', 'updated_at'])

        self.stock_deducted = False
        self.save(update_fields=['stock_deducted', 'updated_at'])

    def approve_job_card(self, nurse_user, nurse_signature_data=None, nurse_name=None):
        """
        Approve the job card with nurse verification
        """
        if self.status == 'Approved':
            raise ValidationError("Job card is already approved.")

        if self.status == 'Declined':
            raise ValidationError("Cannot approve a declined job card.")

        # Deduct stock
        self.deduct_stock()

        # Update status
        self.status = 'Approved'
        self.verified_by_nurse = nurse_user
        self.nurse_signed_date = now()

        if nurse_signature_data:
            self.verified_signature = nurse_signature_data

        if nurse_name:
            self.nurse_name = nurse_name

        self.save()

        # ✅ Update linked PPM schedule if applicable
        self.update_ppm_status_if_applicable()

        logger.info(f"Job card #{self.id} approved by {nurse_user.get_full_name()}")
        return True

    def decline_job_card(self, nurse_user, decline_reason, nurse_signature_data=None, nurse_name=None):
        """
        Decline the job card with reason
        """
        if self.status == 'Declined':
            raise ValidationError("Job card is already declined.")

        if self.status == 'Approved':
            # If it was previously approved, restore stock
            self.restore_stock()

        # Update status
        self.status = 'Declined'
        self.decline_reason = decline_reason
        self.verified_by_nurse = nurse_user
        self.nurse_signed_date = now()

        if nurse_signature_data:
            self.verified_signature = nurse_signature_data

        if nurse_name:
            self.nurse_name = nurse_name

        self.save()

        logger.info(f"Job card #{self.id} declined by {nurse_user.get_full_name()}")
        return True

    # ✅ NEW METHOD: Update PPM Schedule Status
    def update_ppm_status_if_applicable(self):
        """
        Update linked PPM schedule status to 'completed' when job card is approved.
        This uses the direct foreign key relationship for accurate tracking.

        Returns:
            bool: True if PPM was updated, False otherwise
        """
        # Check if this job card is linked to a PPM schedule
        if not self.related_ppm_schedule:
            logger.debug(
                f"Job card #{self.id} has no linked PPM schedule. Skipping PPM update."
            )
            return False

        # Verify action is PPM (safety check)
        if self.action_taken != 'PPM':
            logger.warning(
                f"Job card #{self.id} is linked to PPM schedule {self.related_ppm_schedule.id} "
                f"but action is '{self.action_taken}', not 'PPM'. Updating anyway."
            )

        try:
            ppm_schedule = self.related_ppm_schedule

            # Only update if not already completed
            if ppm_schedule.status == 'completed':
                logger.info(
                    f"PPM schedule {ppm_schedule.id} is already completed. "
                    f"No update needed for job card #{self.id}."
                )
                return False

            # Update PPM schedule to completed
            old_status = ppm_schedule.status
            ppm_schedule.status = 'completed'
            ppm_schedule.completed_date = now().date()
            ppm_schedule.completed_by = self.performed_by
            ppm_schedule.updated_at = now()
            ppm_schedule.needs_sync = True

            # Link the job card to the PPM schedule
            ppm_schedule.related_job_card = self
            ppm_schedule.save()

            logger.info(
                f"✅ PPM Schedule {ppm_schedule.id} updated: {old_status} → completed\n"
                f"   Equipment: {self.equipment.description if self.equipment.description else 'N/A'} "
                f"(SN: {self.equipment.serial_number})\n"
                f"   Scheduled: {ppm_schedule.scheduled_month.strftime('%B %Y')}\n"
                f"   Workshop: {self.workshop.name}\n"
                f"   Job Card: #{self.id}\n"
                f"   Approved by: {self.verified_by_nurse.get_full_name() if self.verified_by_nurse else 'N/A'}\n"
                f"   Approved on: {self.nurse_signed_date.strftime('%Y-%m-%d %H:%M') if self.nurse_signed_date else 'N/A'}"
            )

            return True

        except Exception as e:
            logger.error(
                f"❌ Error updating PPM schedule {self.related_ppm_schedule.id} "
                f"for job card #{self.id}: {str(e)}",
                exc_info=True
            )
            return False

    def clean(self):
        """Validate job card data including PPM schedule linking"""
        super().clean()

        # If PPM schedule is linked, verify it matches the equipment
        if self.related_ppm_schedule and self.equipment:
            if self.related_ppm_schedule.equipment != self.equipment:
                raise ValidationError({
                    'related_ppm_schedule':
                    f"PPM schedule is for different equipment. "
                    f"Schedule equipment: {self.related_ppm_schedule.equipment.description}, "
                    f"Job card equipment: {self.equipment.description}"
                })

            # Verify workshop matches (if PPM schedule has workshop field)
            if hasattr(self.related_ppm_schedule, 'workshop') and self.related_ppm_schedule.workshop != self.workshop:
                raise ValidationError({
                    'related_ppm_schedule':
                    f"PPM schedule workshop doesn't match job card workshop. "
                    f"Schedule workshop: {self.related_ppm_schedule.workshop.name}, "
                    f"Job card workshop: {self.workshop.name}"
                })

        # If action is PPM, recommend linking to a schedule (warning, not error)
        if self.action_taken == 'PPM' and not self.related_ppm_schedule:
            logger.warning(
                f"Job card #{self.id} has action='PPM' but no linked PPM schedule. "
                f"Consider linking to track completion properly."
            )

        # Validate time completion
        if self.time_started and self.time_completed:
            if self.time_completed < self.time_started:
                raise ValidationError({
                    'time_completed': 'Time completed cannot be before time started.'
                })

        # Validate costs
        if self.labor_cost < 0:
            raise ValidationError({
                'labor_cost': 'Labor cost cannot be negative.'
            })

        if self.additional_costs < 0:
            raise ValidationError({
                'additional_costs': 'Additional costs cannot be negative.'
            })

        if self.total_parts_cost < 0:
            raise ValidationError({
                'total_parts_cost': 'Total parts cost cannot be negative.'
            })

    def save(self, *args, **kwargs):
        """Override save to ensure sync flag is set and validation runs"""
        self.needs_sync = True

        # Run validation
        self.clean()

        # Auto-calculate total parts cost if not set
        if not self.pk or 'total_parts_cost' not in kwargs.get('update_fields', []):
            self.total_parts_cost = self.calculate_total_parts_cost()

        super().save(*args, **kwargs)

    def get_linked_ppm_info(self):
        """
        Get information about linked PPM schedule if any

        Returns:
            dict: PPM schedule information or None
        """
        if not self.related_ppm_schedule:
            return None

        ppm = self.related_ppm_schedule
        return {
            'id': ppm.id,
            'scheduled_month': ppm.scheduled_month.strftime('%B %Y'),
            'status': ppm.status,
            'is_overdue': ppm.is_overdue(),
            'maintenance_period': ppm.maintenance_period if hasattr(ppm, 'maintenance_period') else None,
        }

    def __str__(self):
        status_str = f" - {self.status}"
        if self.related_ppm_schedule:
            status_str += f" [PPM: {self.related_ppm_schedule.scheduled_month.strftime('%b %Y')}]"
        return f"jobcard #{self.id} - {self.equipment.description}{status_str}"

    class Meta:
        ordering = ['-nurse_signed_date', '-date_issued']
        verbose_name = 'Job Card'
        verbose_name_plural = 'Job Cards'
        indexes = [
            models.Index(fields=['status', 'date_issued']),
            models.Index(fields=['equipment', 'status']),
            models.Index(fields=['department', 'status']),
            models.Index(fields=['related_ppm_schedule']),
        ]


class SparePartUsed(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_card = models.ForeignKey(jobcard, on_delete=models.CASCADE, related_name='spare_parts')
    part = models.ForeignKey(Accessories, on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField()
    remarks = models.CharField(max_length=200, blank=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    # Cost tracking
    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Cost per unit at time of use"
    )

    original_stock = models.PositiveIntegerField(null=True, blank=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    syncable = True

    def get_total_cost(self):
        """Calculate total cost for this spare part entry"""
        return Decimal(str(self.quantity)) * self.unit_cost

    def clean(self):
        """Validate that there's enough stock available"""
        if self.part and self.quantity:
            if not self.job_card.stock_deducted and self.part.stock_count < self.quantity:
                raise ValidationError(
                    f"Insufficient stock for {self.part.name}. "
                    f"Available: {self.part.stock_count}, Requested: {self.quantity}"
                )

            # Validate unit cost
            if self.unit_cost < 0:
                raise ValidationError({
                    'unit_cost': 'Unit cost cannot be negative.'
                })

            # Validate quantity
            if self.quantity <= 0:
                raise ValidationError({
                    'quantity': 'Quantity must be greater than 0.'
                })

    def save(self, *args, **kwargs):
        self.needs_sync = True

        # Run validation
        self.clean()

        # Store original stock count when creating
        if not self.pk and self.part:
            self.original_stock = self.part.stock_count

        super().save(*args, **kwargs)

        # Update job card costs when spare part changes
        if self.job_card:
            self.job_card.update_costs()

    def delete(self, *args, **kwargs):
        """Override delete to update job card costs"""
        job_card = self.job_card
        super().delete(*args, **kwargs)

        # Update job card costs after deletion
        if job_card:
            job_card.update_costs()

    def __str__(self):
        part_name = self.part.name if self.part else "Unknown Part"
        return f"{part_name} x {self.quantity} (KSh {self.get_total_cost():.2f}) - jobcard #{self.job_card.id}"

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Spare Part Used'
        verbose_name_plural = 'Spare Parts Used'
        indexes = [
            models.Index(fields=['job_card', 'part']),
            models.Index(fields=['part', 'created_at']),
        ]
