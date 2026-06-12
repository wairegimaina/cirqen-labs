import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    """
    Audit trail for all database operations including mirror sync broadcasts.

    Schema includes both granular change tracking (old_values/new_values)
    and full row data for Flask sync server compatibility.
    """

    # Operation type choices
    # well
    OPERATION_CREATE = "c"
    OPERATION_UPDATE = "u"
    OPERATION_DELETE = "d"
    OPERATION_RESTORE = "r"
    OPERATION_TRANSFER = "t"
    OPERATION_SOFT_DELETE = "s"
    OPERATION_BROADCAST = "b"

    OPERATION_CHOICES = [
        (OPERATION_CREATE, "Create"),
        (OPERATION_UPDATE, "Update"),
        (OPERATION_DELETE, "Delete (Hard)"),
        (OPERATION_RESTORE, "Restore/Reactivate"),
        (OPERATION_TRANSFER, "Transfer"),
        (OPERATION_SOFT_DELETE, "Soft Delete/Deactivate"),
        (OPERATION_BROADCAST, "Broadcast (Mirror Sync)"),
    ]

    event_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Unique identifier for each audit event",
    )

    table_name = models.CharField(
        max_length=255, db_index=True, help_text="Name of the table where the operation occurred"
    )

    row_id = models.UUIDField(db_index=True, help_text="UUID of the affected row")

    operation = models.CharField(
        max_length=1,
        choices=OPERATION_CHOICES,
        db_index=True,
        help_text="Operation type: c=Create, u=Update, d=Delete, r=Restore, t=Transfer, s=Soft Delete, b=Broadcast",
    )

    source = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Source system/application that triggered the operation",
    )

    user_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="UUID of user who performed the operation (if applicable)",
    )

    # Granular change tracking
    changed_fields = models.JSONField(
        null=True, blank=True, help_text="Array of field names that were modified"
    )

    old_values = models.JSONField(
        null=True, blank=True, help_text="Previous values before the operation"
    )

    new_values = models.JSONField(null=True, blank=True, help_text="New values after the operation")

    # Full row data (for Flask sync server compatibility)
    data = models.JSONField(
        null=True,
        blank=True,
        help_text="Complete row data - auto-populated from new_values or old_values",
    )

    metadata = models.JSONField(
        null=True, blank=True, help_text="Additional context data (IP address, device info, etc.)"
    )

    received_at = models.DateTimeField(
        default=timezone.now, db_index=True, help_text="Timestamp when the audit event was received"
    )

    created_at = models.DateTimeField(
        default=timezone.now, db_index=True, help_text="Timestamp when the audit record was created"
    )

    class Meta:
        db_table = "audit_log"
        ordering = ["-received_at"]
        verbose_name = "Audit Log Entry"
        verbose_name_plural = "Audit Log Entries"
        indexes = [
            models.Index(fields=["table_name", "row_id", "-received_at"], name="idx_table_row"),
            models.Index(
                fields=["table_name", "operation"],
                name="idx_broadcasts",
                condition=models.Q(operation="b"),
            ),
        ]

    def save(self, *args, **kwargs):
        """
        Override save to ensure timestamps and data field are always set.

        Automatically populates the 'data' field from new_values or old_values
        for compatibility with Flask sync server.
        """
        if not self.received_at:
            self.received_at = timezone.now()
        if not self.created_at:
            self.created_at = timezone.now()

        # Auto-populate data field if not explicitly set
        if self.data is None:
            if self.new_values:
                # For creates/updates, use new values
                self.data = self.new_values
            elif self.old_values:
                # For deletes, use old values
                self.data = self.old_values
            else:
                # Fallback to empty dict
                self.data = {}

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_operation_display()} on {self.table_name} ({self.row_id}) at {self.received_at}"

    def get_operation_description(self):
        """Returns a detailed description of the operation type."""
        descriptions = {
            self.OPERATION_CREATE: "Record creation",
            self.OPERATION_UPDATE: "Record update/upsert",
            self.OPERATION_DELETE: "Permanent record deletion",
            self.OPERATION_RESTORE: "Restore soft-deleted record",
            self.OPERATION_TRANSFER: "Equipment/resource transfer",
            self.OPERATION_SOFT_DELETE: "Mark record as inactive",
            self.OPERATION_BROADCAST: "Mirror sync notification",
        }
        return descriptions.get(self.operation, "Unknown operation")

    @classmethod
    def log_operation(
        cls,
        table_name,
        row_id,
        operation,
        source,
        user_id=None,
        changed_fields=None,
        old_values=None,
        new_values=None,
        data=None,
        metadata=None,
    ):
        """
        Convenience method to create audit log entries.

        Args:
            table_name: Name of the table being modified
            row_id: UUID of the affected row
            operation: Operation type (use class constants)
            source: Source system identifier
            user_id: Optional user UUID who performed the action
            changed_fields: Optional list of changed field names
            old_values: Optional dict of previous values
            new_values: Optional dict of new values
            data: Optional full row data (auto-populated if not provided)
            metadata: Optional dict of additional context

        Usage:
            AuditLog.log_operation(
                table_name='users',
                row_id=user.id,
                operation=AuditLog.OPERATION_UPDATE,
                source='web_app',
                user_id=request.user.id,
                changed_fields=['email', 'name'],
                old_values={'email': 'old@example.com'},
                new_values={'email': 'new@example.com'},
                metadata={'ip': '192.168.1.1'}
            )
        """
        now = timezone.now()

        # Auto-populate data if not provided
        if data is None:
            if new_values:
                data = new_values
            elif old_values:
                data = old_values
            else:
                data = {}

        return cls.objects.create(
            table_name=table_name,
            row_id=row_id,
            operation=operation,
            source=source,
            user_id=user_id,
            changed_fields=changed_fields,
            old_values=old_values,
            new_values=new_values,
            data=data,
            metadata=metadata,
            received_at=now,
            created_at=now,
        )

    @classmethod
    def get_row_history(cls, table_name, row_id):
        """Get all audit entries for a specific row."""
        return cls.objects.filter(table_name=table_name, row_id=row_id)

    @classmethod
    def get_broadcasts(cls, table_name=None):
        """Get all broadcast operations, optionally filtered by table."""
        queryset = cls.objects.filter(operation=cls.OPERATION_BROADCAST)
        if table_name:
            queryset = queryset.filter(table_name=table_name)
        return queryset


from django.utils import timezone


class SyncClient(models.Model):
    """
    Tracks desktop client machines connected to the HQ sync server.
    Created/updated by og_server.py on every heartbeat/upload/download.
    Read by the dashboard to show online clients and their sync status.
    """

    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_OFFLINE = "offline"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
        (STATUS_OFFLINE, "Offline"),
    ]

    client_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique client identifier (derived from MAC address on first run)",
    )

    client_name = models.CharField(
        max_length=255, null=True, blank=True, help_text="Hospital/workshop name from client config"
    )

    last_seen = models.DateTimeField(
        null=True, blank=True, db_index=True, help_text="Last heartbeat timestamp"
    )

    last_upload = models.DateTimeField(
        null=True, blank=True, help_text="Last time client pushed data to HQ"
    )

    last_download = models.DateTimeField(
        null=True, blank=True, help_text="Last time client pulled data from HQ"
    )

    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )

    ip_address = models.CharField(
        max_length=50, null=True, blank=True, help_text="Last known IP address of the client"
    )

    version = models.CharField(
        max_length=50, null=True, blank=True, help_text="Desktop app version string"
    )

    workshop_data = models.JSONField(
        null=True, blank=True, help_text="Workshop metadata pushed by the client"
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        editable=False
    )

    class Meta:
        db_table = "sync_clients"
        ordering = ["-last_seen"]
        verbose_name = "Sync Client"
        verbose_name_plural = "Sync Clients"

    def __str__(self):
        return f"{self.client_name or self.client_id} ({self.status})"

    @property
    def is_online(self):
        """Client is online if last seen within 5 minutes."""
        if not self.last_seen:
            return False
        return (timezone.now() - self.last_seen).seconds < 300

    @classmethod
    def register_or_update(cls, client_id, **kwargs):
        """
        Upsert a client record — called by og_server.py on every heartbeat.

        Usage:
            SyncClient.register_or_update(
                client_id='abc-123',
                client_name='Kiharu Hospital',
                ip_address='41.90.x.x',
                version='1.04',
                status='active',
                last_seen=timezone.now(),
            )
        """
        kwargs.setdefault("last_seen", timezone.now())
        client, created = cls.objects.update_or_create(
            client_id=client_id,
            defaults=kwargs,
        )
        return client, created
