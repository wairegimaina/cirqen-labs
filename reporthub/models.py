import uuid
from django.db import models
from django.conf import settings          # ✅ use settings instead of get_user_model
from workshop.models import Workshop
from django.utils import timezone

class Report(models.Model):
    PERIOD_TYPES = [
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('annual', 'Annual'),
    ]
    active_status = models.BooleanField(default=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE)
    period_type = models.CharField(max_length=20, choices=PERIOD_TYPES)
    period_start = models.DateField()
    remarks = models.TextField(blank=True)
    pending_delete = models.BooleanField(default=False)


    # ✅ Point to the custom user model correctly
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="reports_submitted"   # give a unique related_name
    )

    submitted_at = models.DateTimeField(default=timezone.now)

    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    syncable = True  # for sync tasks

    class Meta:
        unique_together = ['workshop', 'period_type', 'period_start']
