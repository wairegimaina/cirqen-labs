import uuid
from django.db import models
from django.core.exceptions import ValidationError
from workshop.models import Workshop
from users.models import UserProfile
from core.names import check_unique, tidy

# Tools Models
class ToolsManufacturer(models.Model):
    name = models.CharField(max_length=150, unique=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    pending_delete = models.BooleanField(default=False)

    syncable = True

    def clean(self):
        if not self.name:
            raise ValidationError({"name": "Manufacturer name cannot be empty."})
        self.name = tidy(self.name).title()
        check_unique(self, "Manufacturer")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Toolname(models.Model):
    name = models.CharField(max_length=150, unique=True)
    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    syncable = True

    def clean(self):
        if not self.name:
            raise ValidationError({"name": "Tool name cannot be empty."})
        self.name = tidy(self.name).title()
        check_unique(self, "Tool name")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Tools(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.ForeignKey(
        Toolname,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tools"
    )
    manufacturer = models.ForeignKey(
        ToolsManufacturer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tools"
    )
    model = models.CharField(max_length=100, blank=True, null=True)
    serial_number = models.CharField(max_length=100, unique=True, blank=True, null=True)
    workshop = models.ForeignKey(
        Workshop,
        on_delete=models.CASCADE,
        blank=True,
        null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)
    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    syncable = True

    def __str__(self):
        tool_name = self.name.name if self.name else "Unnamed Tool"
        manufacturer_name = self.manufacturer.name if self.manufacturer else "Unknown Manufacturer"
        return f"{tool_name} ({manufacturer_name})"

    class Meta:
        verbose_name_plural = "Tools"


# Accessories Models
class AccessoriesManufacturer(models.Model):
    name = models.CharField(max_length=150, unique=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    syncable = True

    def clean(self):
        if not self.name:
            raise ValidationError({"name": "Manufacturer name cannot be empty."})
        self.name = tidy(self.name).title()
        check_unique(self, "Manufacturer")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Accessoriesname(models.Model):
    name = models.CharField(max_length=150, unique=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    active_status = models.BooleanField(default=True)
    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    pending_delete = models.BooleanField(default=False)

    syncable = True

    def clean(self):
        if not self.name:
            raise ValidationError({"name": "Accessory name cannot be empty."})
        self.name = tidy(self.name).title()
        check_unique(self, "Accessory")

    def save(self, *args, **kwargs):
        self.needs_sync = True
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Accessories(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.ForeignKey(
        Accessoriesname,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accessories"
    )
    active_status = models.BooleanField(default=True)
    manufacturer = models.ForeignKey(
        AccessoriesManufacturer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accessories"
    )
    equipment_description = models.ForeignKey(
        'Inventory.EquipmentDescription',
        on_delete=models.CASCADE,
        related_name="accessories"
    )
    note = models.TextField(max_length=100, blank=True, null=True)
    stock_count = models.PositiveIntegerField(default=0)
    # The lower limit: at or below it the part is "running low" and everyone in
    # its workshop is emailed once (notifications.stock). 0 = no limit.
    reorder_level = models.PositiveIntegerField(default=0)
    # When that email went; cleared once the stock is back above the limit,
    # so each time the part runs low is mailed once, from one PC.
    low_stock_alerted_at = models.DateTimeField(null=True, blank=True)
    supplier = models.ForeignKey('Inventory.Supplier', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='accessories')

    # Unit cost for the accessory/spare part
    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Cost per unit in KSh"
    )

    workshop = models.ForeignKey(
        Workshop,
        on_delete=models.CASCADE,
        blank=True,
        null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    pending_delete = models.BooleanField(default=False)

    syncable = True

    def __str__(self):
        accessory_name = self.name.name if self.name else "Unnamed Accessory"
        equipment_name = self.equipment_description.name if self.equipment_description else "Unknown Equipment"
        return f"{accessory_name} for {equipment_name} (Stock: {self.stock_count})"

    @property
    def stock_state(self):
        """"out", "low" (at or below the lower limit) or "ok"."""
        if self.stock_count <= 0:
            return "out"
        if self.reorder_level and self.stock_count <= self.reorder_level:
            return "low"
        return "ok"

    @property
    def stock_label(self):
        return {"out": "Out of stock", "low": "Running low", "ok": "In stock"}[self.stock_state]

    @property
    def is_running_low(self):
        """At or below a set lower limit (out of stock included)."""
        return bool(self.reorder_level) and self.stock_count <= self.reorder_level

    @staticmethod
    def can_set_lower_limit(user, accessory=None):
        """The HOD, or the Engineer In-charge of the part's workshop."""
        profile = getattr(user, "userprofile", None)
        if profile is None:
            return False
        if profile.role == "HOD":
            return True
        if profile.role != "Tech" or profile.level != "Engineer Incharge":
            return False
        return accessory is None or accessory.workshop_id == profile.workshop_id

    class Meta:
        verbose_name_plural = "Accessories"


class AccessoryRequest(models.Model):
    """
    Request model for adding new accessories to the system.
    Workflow: User Request -> HOD Approval -> Workshop User Acceptance -> Accessory Created
    """
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Declined', 'Declined'),
        ('Accepted', 'Accepted'),  # New status when workshop user accepts the approved request
    ]

    REQUEST_TYPE_CHOICES = [
        ('new', 'New Accessory'),
        ('restock', 'Restock Existing'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Request type
    request_type = models.CharField(
        max_length=20,
        choices=REQUEST_TYPE_CHOICES,
        default='new',
        help_text="Type of request"
    )

    # For new accessories
    accessory_name = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="Name of the new accessory"
    )

    manufacturer_name = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        help_text="Manufacturer name"
    )

    # For restock requests
    existing_accessory = models.ForeignKey(
        Accessories,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Select existing accessory for restock",
        related_name="restock_requests"
    )

    equipment_description = models.ForeignKey(
        'Inventory.EquipmentDescription',
        on_delete=models.CASCADE,
        related_name="accessory_requests"
    )

    requested_quantity = models.PositiveIntegerField(default=1)
    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Cost per unit in KSh"
    )
    note = models.TextField(blank=True, null=True)

    # Request tracking
    requested_by = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='accessory_requests'
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    workshop = models.ForeignKey(
        Workshop,
        on_delete=models.CASCADE,
        related_name='accessory_requests'
    )

    # Status and workflow
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='Pending'
    )

    # HOD Approval Stage
    approved_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accessory_approvals'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_reason = models.TextField(
        blank=True,
        null=True,
        help_text="Reason for approval or decline by HOD"
    )

    # Workshop User Acceptance Stage
    accepted_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accessory_acceptances',
        help_text="Workshop user who received/accepted the accessory"
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    acceptance_note = models.TextField(
        blank=True,
        null=True,
        help_text="Note from the user who accepted the accessory"
    )

    # Link to created accessory (after acceptance)
    created_accessory = models.ForeignKey(
        Accessories,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='source_request',
        help_text="The accessory created from this request"
    )

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    syncable = True

    def clean(self):
        super().clean()
        if self.request_type == 'new':
            if not self.accessory_name:
                raise ValidationError("Accessory name is required for new accessory requests.")
        elif self.request_type == 'restock':
            if not self.existing_accessory:
                raise ValidationError("Existing accessory must be selected for restock requests.")

        # Ensure requested_by has a workshop
        if self.requested_by and not self.requested_by.workshop:
            raise ValidationError("Requester must be assigned to a workshop.")

    def save(self, *args, **kwargs):
        # Auto-set workshop from requester if not set
        if not self.workshop and self.requested_by:
            self.workshop = self.requested_by.workshop
        super().save(*args, **kwargs)

    def __str__(self):
        if self.request_type == 'new':
            return f"New Accessory Request: {self.accessory_name or 'Unnamed'} by {self.requested_by.user.first_name}"
        else:
            accessory_name = self.existing_accessory.name.name if self.existing_accessory and self.existing_accessory.name else "Unknown"
            return f"Restock Request: {accessory_name} ({self.requested_quantity} units) by {self.requested_by.user.last_name}"

    @property
    def can_be_approved(self):
        """Check if request can be approved by HOD"""
        return self.status == 'Pending'

    @property
    def can_be_accepted(self):
        """Check if request can be accepted by workshop user"""
        return self.status == 'Approved'

    @property
    def total_cost(self):
        """Calculate total cost"""
        return self.unit_cost * self.requested_quantity

    class Meta:
        verbose_name = "Accessory Request"
        verbose_name_plural = "Accessory Requests"
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['status', 'workshop']),
            models.Index(fields=['requested_by', 'status']),
        ]


class StockMovement(models.Model):
    """One change to a part's stock: rows are only ever added, never edited.

    A part's stock is the total of its movements (parts_tools.stock), so two
    PCs using the same part while apart both count when they sync: their rows
    are different rows, where a synced count would keep only the last one.
    ``Accessories.stock_count`` stays as the running total the screens read,
    and is corrected from the movements every minute.
    """
    OPENING, WORK_ORDER, WORK_ORDER_UNDONE, RECEIVED, ADJUSTMENT = (
        "opening", "work_order", "work_order_undone", "received", "adjustment")
    REASONS = [
        (OPENING, "Opening balance"),
        (WORK_ORDER, "Used on a work order"),
        (WORK_ORDER_UNDONE, "Work order declined: returned"),
        (RECEIVED, "Received"),
        (ADJUSTMENT, "Stock count corrected"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # No database constraints: sync can deliver a movement before its part,
    # work order or request, and a movement must never be lost for that.
    accessory = models.ForeignKey(Accessories, on_delete=models.DO_NOTHING, db_constraint=False,
                                  related_name="movements")
    change = models.IntegerField(help_text="+ into stock, - out of stock")
    reason = models.CharField(max_length=20, choices=REASONS)
    job_card = models.ForeignKey('jobcard.jobcard', on_delete=models.DO_NOTHING, db_constraint=False,
                                 null=True, blank=True, related_name="stock_movements")
    request = models.ForeignKey(AccessoryRequest, on_delete=models.DO_NOTHING, db_constraint=False,
                                null=True, blank=True, related_name="stock_movements")
    created_by = models.ForeignKey('accounts.CustomUser', on_delete=models.DO_NOTHING, db_constraint=False,
                                   null=True, blank=True, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    syncable = True

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["accessory", "reason"])]

    def __str__(self):
        return f"{self.change:+d} {self.get_reason_display()} ({self.accessory_id})"


class AccessoryRequestHistory(models.Model):
    """
    Track all actions on an accessory request for audit trail
    """
    ACTION_CHOICES = [
        ('created', 'Created'),
        ('approved', 'Approved'),
        ('declined', 'Declined'),
        ('accepted', 'Accepted'),
        ('modified', 'Modified'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        AccessoryRequest,
        on_delete=models.CASCADE,
        related_name='history'
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    performed_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        related_name='request_actions'
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)
    previous_status = models.CharField(max_length=20, blank=True, null=True)
    new_status = models.CharField(max_length=20, blank=True, null=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    syncable = True

    class Meta:
        verbose_name = "Request History"
        verbose_name_plural = "Request Histories"
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action.title()} by {self.performed_by.first_name if self.performed_by else 'System'} at {self.timestamp}"
