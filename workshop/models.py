import uuid
from django.db import models

class Workshop(models.Model):
    CATEGORY_CHOICES = [
        ('calibration_center', 'Calibration Center'),
        ('maintenance', 'Maintenance'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    category = models.CharField(
        max_length=50,
        choices=CATEGORY_CHOICES,
        default='maintenance'
    )
    active_status = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    pending_delete = models.BooleanField(default=False)

    # Optional: Track if this was synced to HQ (for debugging)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    # Optional: Store transfer target for HQ coordination
    transfer_workshop_id = models.UUIDField(null=True, blank=True)

    syncable = True

    def to_payload(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "category": self.category,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "needs_sync": self.needs_sync,
            "updated_at": self.updated_at.isoformat(),
            "pending_delete": self.pending_delete,
            "transfer_workshop_id": str(self.transfer_workshop_id) if self.transfer_workshop_id else None,
        }

    def __str__(self):
        return f"{self.name}"

    class Meta:
        indexes = [
            models.Index(fields=['pending_delete', 'updated_at']),
            models.Index(fields=['needs_sync']),
            models.Index(fields=['updated_at']),  # For sync ordering
        ]
