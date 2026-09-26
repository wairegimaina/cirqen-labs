import uuid
from django.db import models
from django.core.exceptions import ValidationError
from workshop.models import Workshop


class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
        # offline sync
    pending_delete = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    active_status = models.BooleanField(default=True)

    syncable = True  # <- important, so sync task knows to sync this model

    def clean(self):
        # Prevent duplicate department names within the same workshop
        duplicates = Department.objects.filter(
            name__iexact=self.name
        ).exclude(pk=self.pk)

        if duplicates.exists():
            dup = duplicates.first()
            if dup.workshop == self.workshop:
                raise ValidationError({
                    '__all__': f"Department with name '{self.name}' already exists in this workshop."
                })
            else:
                raise ValidationError({
                    '__all__': f"Department '{self.name}' already exists in another workshop: '{dup.workshop.name}'."
                })

    def to_payload(self):
        return {
            "id": str(self.id),
            "workshop": str(self.workshop.id) if self.workshop else None,
            "name": self.name,
            "needs_sync": self.needs_sync,
            "updated_at": self.updated_at.isoformat(),
        }

    def save(self, *args, **kwargs):
        self.full_clean()  # enforce validation before saving
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class EquipmentDescription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    active_status = models.BooleanField(default=True)
    # Only category, no "general/critical" anymore
    category = models.ForeignKey(
        'machineReports.EquipmentCategory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

        # offline sync
    needs_sync = models.BooleanField(default=True)
    pending_delete = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)


    syncable = True  # <- important, so sync task knows to sync this model

    def clean(self):
        """Prevent case-insensitive duplicates in name."""
        duplicates = EquipmentDescription.objects.filter(
            name__iexact=self.name
        ).exclude(pk=self.pk)

        if duplicates.exists():
            raise ValidationError({
                'name': f"Equipment description with name '{self.name}' already exists."
            })

    def save(self, *args, **kwargs):
        # Run validations + normalization before saving
        self.needs_sync = True
        self.full_clean()
        super().save(*args, **kwargs)
        # Cascade category update to all equipment using this description
        if self.category:
            from Inventory.models import Equipment  # avoid circular import
            Equipment.objects.filter(description=self).update(category=self.category)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Manufacturer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True)
    active_status = models.BooleanField(default=True)
        # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    pending_delete = models.BooleanField(default=False)

    syncable = True  # <- important, so sync task knows to sync this model


    def clean(self):
        """Prevent duplicates like Sony vs sony, and normalize name."""
        if not self.name:
            raise ValidationError({"name": "Manufacturer name cannot be empty."})

        # Trim spaces
        normalized_name = self.name.strip()

        # Check duplicates case-insensitively
        existing = Manufacturer.objects.filter(
            name__iexact=normalized_name
        ).exclude(pk=self.pk)

        if existing.exists():
            raise ValidationError({"name": f"Manufacturer '{normalized_name.title()}' already exists."})

        # Normalize format (e.g., "sony", "SONY" -> "Sony")
        self.name = normalized_name.title()

    def save(self, *args, **kwargs):
        # Run validations + normalization before saving
        self.needs_sync = True
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class Supplier(models.Model):
    """Vendor that sold or services equipment; holds its standard warranty terms."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    default_warranty_months = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Warranty period this supplier normally gives; pre-fills new warranties"
    )
    warranty_terms = models.TextField(blank=True, help_text="Standard warranty terms; pre-fill new warranties")
    active_status = models.BooleanField(default=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    pending_delete = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    syncable = True

    def clean(self):
        if not self.name or not self.name.strip():
            raise ValidationError({"name": "Supplier name cannot be empty."})
        self.name = self.name.strip()
        if Supplier.objects.filter(name__iexact=self.name).exclude(pk=self.pk).exists():
            raise ValidationError({"name": f"Supplier '{self.name}' already exists."})

    def save(self, *args, **kwargs):
        self.needs_sync = True
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Equipment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, blank=True, null=True)
    description = models.ForeignKey('EquipmentDescription', on_delete=models.CASCADE)
    manufacturer = models.ForeignKey('Manufacturer', on_delete=models.SET_NULL, null=True, blank=True)
    active_status = models.BooleanField(default=True)
    model = models.CharField(max_length=100)
    serial_number = models.CharField(max_length=100, unique=True)  # Keep unique=True
    department = models.ForeignKey('Department', on_delete=models.CASCADE)
    pending_delete = models.BooleanField(default=False)

    # Hospital asset number (barcode): the hospital's asset register identifies
    # devices by this, not by serial number.
    asset_tag = models.CharField(max_length=100, blank=True, db_index=True)

    status = models.CharField(max_length=50, choices=[
        ('Working', 'Working'),
        ('Not working', 'Not working'),
        ('Under repair', 'Under repair')
    ])

    # Auto-synced category from description
    category = models.ForeignKey(
        'machineReports.EquipmentCategory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    syncable = True  # <- important, so sync task knows to sync this model

    def clean(self):
        # Ensure department exists
        if not self.department:
            raise ValidationError("You must select a department.")

        # Assign workshop from department
        workshop = self.department.workshop
        if not workshop:
            raise ValidationError("Selected department does not have a workshop.")
        self.workshop = workshop

        # Normalize serial number to uppercase for storage
        if self.serial_number:
            self.serial_number = self.serial_number.strip().upper()

        # Check for case-insensitive duplicate serial numbers
        # This catches duplicates before they hit the database
        duplicates = Equipment.objects.filter(
            serial_number__iexact=self.serial_number
        ).exclude(pk=self.pk)

        if duplicates.exists():
            dup = duplicates.first()
            if dup.active_status:
                raise ValidationError({
                    'serial_number': f"Equipment with serial number '{self.serial_number}' already exists in {dup.department.name} ({dup.workshop.name})."
                })
            else:
                # Equipment exists but is deactivated - allow reactivation
                raise ValidationError({
                    'serial_number': f"Equipment with serial number '{self.serial_number}' was previously deactivated. Use the reactivation feature."
                })

    def save(self, *args, **kwargs):
        """Save equipment and auto-inherit category from its description."""
        self.needs_sync = True

        # Normalize serial number before validation
        if self.serial_number:
            self.serial_number = self.serial_number.strip().upper()

        # ✅ Handle optional skip_clean argument safely
        skip_clean = kwargs.pop('skip_clean', False)

        # Only run validation when not explicitly skipped
        if not skip_clean:
            self.full_clean()

        # Auto-sync category with description
        if self.description and self.description.category:
            self.category = self.description.category
        else:
            self.category = None

        # ✅ Call super() without unknown kwargs
        super(Equipment, self).save(*args, **kwargs)


    def current_warranty(self):
        """The active warranty that runs latest, or None. Uses prefetched rows when present."""
        rows = [w for w in self.warranties.all() if w.active_status and not w.pending_delete]
        return max(rows, key=lambda w: w.expiry_date, default=None)

    def __str__(self):
        return f"{self.serial_number} ({self.description.name})"


class Warranty(models.Model):
    """A warranty on one piece of inventory, given by one existing Supplier.

    Inventory Equipment -> Warranty -> Supplier. Nothing about the device or
    the supplier is copied here, so editing either shows up in the Warranties
    module straight away. The status (active / expiring soon / expired) is
    worked out from the dates, never stored.
    """
    COVERAGE_CHOICES = [
        ('parts', 'Parts replacement'),
        ('labour', 'Labour'),
        ('manufacturing_defects', 'Manufacturing defects'),
        ('electrical', 'Electrical components'),
        ('software', 'Software / firmware'),
        ('preventive_maintenance', 'Preventive maintenance'),
        ('calibration', 'Calibration'),
        ('onsite_service', 'On-site service'),
        ('replacement_unit', 'Replacement / loan unit'),
    ]
    COVERAGE_LABELS = dict(COVERAGE_CHOICES)

    STATUS_ACTIVE = 'active'
    STATUS_EXPIRING = 'expiring'
    STATUS_EXPIRED = 'expired'
    STATUS_NONE = 'none'
    STATUS_LABELS = {
        STATUS_ACTIVE: 'Active',
        STATUS_EXPIRING: 'Expiring Soon',
        STATUS_EXPIRED: 'Expired',
        STATUS_NONE: 'No Warranty',
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey('Equipment', on_delete=models.CASCADE, related_name='warranties')
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='warranties')
    reference = models.CharField(max_length=100, blank=True, help_text="Warranty certificate or contract number")
    start_date = models.DateField()
    period_months = models.PositiveIntegerField(null=True, blank=True)
    expiry_date = models.DateField(help_text="Left blank, it is start date + period")
    coverage = models.JSONField(default=list, blank=True, help_text="Codes from COVERAGE_CHOICES")
    coverage_other = models.CharField(max_length=255, blank=True, help_text="Anything else covered")
    terms = models.TextField(blank=True, help_text="Terms, exclusions, how to make a claim")
    created_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='warranties_created')
    active_status = models.BooleanField(default=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    pending_delete = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    syncable = True

    class Meta:
        ordering = ['expiry_date']
        indexes = [models.Index(fields=['equipment', 'expiry_date'])]

    @staticmethod
    def add_months(start, months):
        from dateutil.relativedelta import relativedelta
        return start + relativedelta(months=months)

    def clean(self):
        errors = {}
        if not self.start_date:
            errors['start_date'] = "Start date is required."
        if self.start_date and not self.expiry_date and not self.period_months:
            errors['expiry_date'] = "Enter the warranty period or the expiry date."
        if self.start_date and self.expiry_date and self.expiry_date < self.start_date:
            errors['expiry_date'] = "Expiry date cannot be before the start date."
        unknown = [c for c in (self.coverage or []) if c not in self.COVERAGE_LABELS]
        if unknown:
            errors['coverage'] = f"Unknown coverage: {', '.join(map(str, unknown))}."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.needs_sync = True
        if self.start_date and not self.expiry_date and self.period_months:
            self.expiry_date = self.add_months(self.start_date, self.period_months)
        if self.start_date and self.expiry_date and not self.period_months:
            from dateutil.relativedelta import relativedelta
            delta = relativedelta(self.expiry_date, self.start_date)
            months = delta.years * 12 + delta.months
            if months and self.add_months(self.start_date, months) == self.expiry_date:
                self.period_months = months
        # Stored in the order of COVERAGE_CHOICES; unknown codes are kept so full_clean rejects them.
        coverage = list(self.coverage or [])
        self.coverage = [c for c, _ in self.COVERAGE_CHOICES if c in coverage] + \
            [c for c in coverage if c not in self.COVERAGE_LABELS]
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def expiring_soon_days(cls):
        from django.conf import settings
        return getattr(settings, 'WARRANTY_EXPIRING_SOON_DAYS', 60)

    def status(self, today=None):
        from django.utils.timezone import localdate
        days_left = (self.expiry_date - (today or localdate())).days
        if days_left < 0:
            return self.STATUS_EXPIRED
        return self.STATUS_EXPIRING if days_left <= self.expiring_soon_days() else self.STATUS_ACTIVE

    @property
    def status_code(self):
        return self.status()

    @property
    def status_label(self):
        return self.STATUS_LABELS[self.status()]

    @property
    def days_left(self):
        from django.utils.timezone import localdate
        return (self.expiry_date - localdate()).days

    @property
    def days_since_expiry(self):
        return -self.days_left

    @property
    def period_display(self):
        months = self.period_months
        if not months:
            return f"Until {self.expiry_date:%d %b %Y}" if self.expiry_date else "-"
        years, rest = divmod(months, 12)
        if not rest:
            return f"{years} year{'s' if years != 1 else ''}"
        if not years:
            return f"{months} month{'s' if months != 1 else ''}"
        return f"{years} yr {rest} mo"

    @property
    def coverage_labels(self):
        labels = [self.COVERAGE_LABELS[c] for c in self.coverage or [] if c in self.COVERAGE_LABELS]
        if self.coverage_other:
            labels.append(self.coverage_other)
        return labels

    @property
    def coverage_summary(self):
        return ", ".join(self.coverage_labels) or "Not specified"

    def __str__(self):
        return f"{self.equipment} warranty to {self.expiry_date}"
