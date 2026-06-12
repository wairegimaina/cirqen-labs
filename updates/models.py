from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class ClientMachine(models.Model):
    """Tracks every machine that has checked in with the HQ server."""

    machine_id = models.CharField(max_length=100, unique=True)
    hostname = models.CharField(max_length=255, blank=True)
    current_version = models.CharField(max_length=50, default='0.0.0')

    last_check = models.DateTimeField(null=True, blank=True)
    last_update = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-last_check']
        verbose_name = 'Client Machine'
        verbose_name_plural = 'Client Machines'

    def __str__(self):
        return f"{self.hostname or self.machine_id} (v{self.current_version})"

    @property
    def is_up_to_date(self):
        from django.conf import settings
        return self.current_version == getattr(settings, 'APP_VERSION', '1.0.0')


class UpdatePackage(models.Model):
    """Tracks available update packages — can come from HQ server OR a local upload."""

    SOURCE_CHOICES = [
        ('hq_server', 'HQ Server'),
        ('local_upload', 'Local Upload'),
    ]

    version = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    min_version = models.CharField(max_length=50, default='0.0.0')

    changes = models.TextField(blank=True)
    critical = models.BooleanField(default=False)

    size_bytes = models.BigIntegerField(default=0)
    checksum = models.CharField(max_length=255, blank=True)
    file_count = models.IntegerField(default=0)

    # Remote URL (HQ server downloads)
    package_path = models.CharField(max_length=500, blank=True)

    # Local upload (zip from dev laptop)
    uploaded_file = models.FileField(
        upload_to='updates/packages/',
        null=True,
        blank=True,
        help_text="Upload a .zip update package built on the dev laptop"
    )

    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='hq_server')
    manifest_data = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)

    fetched_at = models.DateTimeField(null=True, blank=True)

    # Who uploaded it (for local uploads)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='uploaded_packages'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Update Package'
        verbose_name_plural = 'Update Packages'

    def __str__(self):
        tag = " [CRITICAL]" if self.critical else ""
        src = " [Upload]" if self.source == 'local_upload' else ""
        return f"v{self.version}{tag}{src}"

    @property
    def size_mb(self):
        return round(self.size_bytes / (1024 * 1024), 2) if self.size_bytes else 0

    @property
    def effective_package_path(self):
        """Returns the best path/URL to use for applying this package."""
        if self.source == 'local_upload' and self.uploaded_file:
            return self.uploaded_file.path  # local filesystem path
        return self.package_path  # remote URL


class UpdateHistory(models.Model):
    """Tracks applied updates."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('applying', 'Applying'),
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('rolled_back', 'Rolled Back'),
    ]

    package = models.ForeignKey(
        UpdatePackage, on_delete=models.CASCADE, related_name='installations'
    )

    machine_id = models.CharField(max_length=100)
    client_id = models.CharField(max_length=100)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    backup_path = models.CharField(max_length=500, blank=True)
    error_message = models.TextField(blank=True)

    applied_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    auto_applied = models.BooleanField(default=False)

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'Update History'
        verbose_name_plural = 'Update History'

    def __str__(self):
        return f"{self.package.version} - {self.machine_id} ({self.status})"

    def mark_success(self):
        self.status = 'success'
        self.completed_at = timezone.now()
        self.save()

    def mark_failed(self, error):
        self.status = 'failed'
        self.error_message = str(error)
        self.completed_at = timezone.now()
        self.save()


class UpdateSettings(models.Model):
    """Global update settings (singleton pattern)."""

    auto_check_enabled = models.BooleanField(default=True)
    check_interval_hours = models.IntegerField(default=24)

    auto_apply_updates = models.BooleanField(default=False)
    auto_apply_critical = models.BooleanField(default=True)

    last_check = models.DateTimeField(null=True, blank=True)
    last_successful_update = models.DateTimeField(null=True, blank=True)

    update_templates = models.BooleanField(default=True)
    update_static = models.BooleanField(default=True)
    update_django_apps = models.BooleanField(default=True)
    update_python_code = models.BooleanField(default=True)
    update_migrations = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Update Settings'
        verbose_name_plural = 'Update Settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
