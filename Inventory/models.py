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


    def __str__(self):
        return f"{self.serial_number} ({self.description.name})"
