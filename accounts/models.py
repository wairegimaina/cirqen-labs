import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models

class CustomUser(AbstractUser):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    # offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now=True)
    active_status = models.BooleanField(default=True)

    pending_delete = models.BooleanField(default=False)


    syncable = True  # <- important, so sync task knows to sync this model

    def to_payload(self):
        return {
            "id": str(self.id),
            "username": self.username,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "is_active": self.is_active,
            "is_staff": self.is_staff,
            "is_superuser": self.is_superuser,
            "needs_sync": self.needs_sync,
            "updated_at": self.updated_at.isoformat(),
        }

    def __str__(self):
        return self.username  # or f"{self.get_full_name()} ({self.username})"
