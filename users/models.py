# users/models.py - Enhanced version with sync support and BASE64 signatures

import uuid
import io
import hashlib
import secrets
import string
import base64
from PIL import Image, ImageDraw, ImageFont
import random
from django.db import models


from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
User = get_user_model()

from workshop.models import Workshop
from Inventory.models import Department


# Monkey patch User model to add sync fields and methods
def add_sync_fields_to_user():
    """Add sync fields to Django's User model"""
    # Only add if not already present
    if not hasattr(User, 'needs_sync'):
        User.add_to_class('needs_sync', models.BooleanField(default=True, db_column='needs_sync'))
    if not hasattr(User, 'updated_at'):
        User.add_to_class('updated_at', models.DateTimeField(auto_now=True, db_column='updated_at'))

    # Mark User as syncable
    User.syncable = True

    # Add sync method
    def mark_for_sync(self):
        """Mark this user for sync"""
        User.objects.filter(pk=self.pk).update(
            needs_sync=True,
            updated_at=timezone.now()
        )

    User.mark_for_sync = mark_for_sync

# Apply the monkey patch
add_sync_fields_to_user()


# -------------------------------------------------
# User Profile - Enhanced with first login logic
# -------------------------------------------------
class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('Tech', 'Technologist'),
        ('NIC', 'In-Charge'),
        ('HOD', 'Head of department'),
    ]

    LEVEL_CHOICES = [
        ('Engineer', 'Engineer'),
        ('Engineer Incharge', 'Engineer Incharge'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )
    role = models.CharField(max_length=50, choices=ROLE_CHOICES)

    # First login control flags
    must_change_password = models.BooleanField(default=True)
    has_uploaded_signature = models.BooleanField(default=False)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Only for In-Charge role"
    )
    workshop = models.ForeignKey(
        Workshop, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Required for all Tech roles"
    )
    level = models.CharField(
        max_length=50, choices=LEVEL_CHOICES, null=True, blank=True,
        help_text="Required for all Tech roles"
    )

    # Extra info
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_id = models.CharField(max_length=20, unique=True, null=True, blank=True)
    phone_number = models.CharField(max_length=15, null=True, blank=True)
    is_approved = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_profiles'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Theme preferences
    THEME_CHOICES = [
        ('light', 'Light'),
        ('dark', 'Dark'),
        ('auto', 'Auto'),
    ]
    theme_mode = models.CharField(
        max_length=10,
        choices=THEME_CHOICES,
        default='light',
        help_text='User preferred theme mode'
    )
    sidebar_collapsed = models.BooleanField(
        default=False,
        help_text='Whether sidebar is collapsed by default'
    )

    # Offline sync
    needs_sync = models.BooleanField(default=True)

    syncable = True

    def clean(self):
        # HOD: No department, no workshop, no level
        if self.role == 'HOD':
            if self.department:
                raise ValidationError("HOD cannot be assigned to any department.")
            if self.workshop:
                raise ValidationError("HOD cannot be assigned to any workshop.")
            if self.level:
                raise ValidationError("HOD cannot have a level.")

        # NIC: Must have department, no workshop, no level
        elif self.role == 'NIC':
            if not self.department:
                raise ValidationError("In-charge must be assigned to a department.")
            if self.workshop:
                raise ValidationError("In-charge cannot be assigned to a workshop.")
            if self.level:
                raise ValidationError("In-charge cannot have a level.")

        # Tech: Must have level AND workshop, no department
        elif self.role == 'Tech':
            if not self.level:
                raise ValidationError("Tech must have a level assigned.")
            if not self.workshop:
                raise ValidationError("Tech must be assigned to a workshop.")
            if self.department:
                raise ValidationError("Tech cannot be assigned to a department.")

    def to_payload(self):
        """Serialize UserProfile for remote sync"""
        return {
            "id": str(self.id),
            "user": str(self.user.id) if self.user else None,
            "role": self.role,
            "must_change_password": self.must_change_password,
            "has_uploaded_signature": self.has_uploaded_signature,
            "department": str(self.department.id) if self.department else None,
            "workshop": str(self.workshop.id) if self.workshop else None,
            "level": self.level,
            "employee_id": self.employee_id,
            "phone_number": self.phone_number,
            "is_approved": self.is_approved,
            "created_by": str(self.created_by.id) if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat(),
            "needs_sync": self.needs_sync,
        }

    def save(self, *args, **kwargs):
        if not self.employee_id:
            self.employee_id = self.generate_employee_id()
        self.full_clean()
        super().save(*args, **kwargs)

    def needs_first_login_setup(self):
        """Check if user needs to go through first login setup"""
        return self.must_change_password or not self.has_uploaded_signature

    def generate_employee_id(self):
        """Generate unique employee ID"""
        role_prefix = {
            'HOD': 'HD',
            'NIC': 'IC',
            'Tech': 'TC'
        }.get(self.role, 'EMP')

        # For NIC, add department prefix
        dept_prefix = ''
        if self.role == 'NIC' and self.department:
            dept_prefix = self.department.name[:2].upper()

        # For Tech, add workshop prefix
        workshop_prefix = ''
        if self.role == 'Tech' and self.workshop:
            workshop_prefix = self.workshop.name[:2].upper()

        for _ in range(100):
            emp_id = f"{role_prefix}{dept_prefix}{workshop_prefix}{random.randint(1000, 9999)}"
            if not UserProfile.objects.filter(employee_id=emp_id).exists():
                return emp_id

        return f"{role_prefix}{str(uuid.uuid4())[:6].upper()}"

    def get_full_name(self):
        """Get user's full name with appropriate title"""
        full_name = f"{self.user.first_name} {self.user.last_name}".strip() or self.user.username
        if self.role == 'HOD':
            return f"ENG. {full_name}"
        elif self.role == 'Tech':
            return f"Eng. {full_name}"
        return full_name

    def can_create_users(self):
        return self.role == 'HOD'

    def get_subordinates(self):
        """Get users under this user's authority"""
        if self.role == 'HOD':
            return UserProfile.objects.exclude(id=self.id)
        elif self.role == 'NIC':
            return UserProfile.objects.filter(
                role='Tech',
                workshop__department=self.department
            )
        return UserProfile.objects.none()

    def get_managed_workshops(self):
        """Get workshops this user can manage"""
        if self.role == 'HOD':
            return Workshop.objects.all()
        elif self.role == 'NIC':
            return Workshop.objects.filter(department=self.department)
        elif self.role == 'Tech' and self.workshop:
            return Workshop.objects.filter(id=self.workshop.id)
        return Workshop.objects.none()

    def __str__(self):
        label = f"{self.role}"
        if self.level:
            label += f" - {self.level}"
        if self.department:
            label += f" ({self.department.name})"
        elif self.workshop:
            label += f" ({self.workshop.name})"
        return f"{self.user.username} ({label}) - {self.employee_id}"

    class Meta:
        ordering = ['role', 'user__first_name']
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'


# =============================================================================
# ENHANCED UserSignature with Base64 Support for PyInstaller Apps
# =============================================================================

class UserSignature(models.Model):
    """
    User signature model with dual storage support:
    1. signature_data (TextField) - Base64 encoded PNG (PREFERRED for PyInstaller apps)
    2. signature_image (ImageField) - Traditional file storage (fallback)

    The base64 approach is better for frozen PyInstaller applications because:
    - No file system dependencies
    - Signatures stored directly in SQLite database
    - Database travels with the app
    - Easier deployment and sync
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='signature'
    )
    active_status = models.BooleanField(default=True)

    signature_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    # DUAL STORAGE: Both base64 and ImageField
    signature_data = models.TextField(
        null=True,
        blank=True,
        help_text="Base64 encoded signature image (preferred for PyInstaller apps)"
    )
    signature_image = models.ImageField(
        upload_to='signatures/',
        null=True,
        blank=True,
        help_text="Traditional file storage (fallback)"
    )

    signature_hash = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_user_drawn = models.BooleanField(default=True)
    pending_delete = models.BooleanField(default=False)

    # Offline sync
    needs_sync = models.BooleanField(default=True)

    syncable = True

    def save_user_drawn_signature(self, signature_file):
        """
        Save a user-drawn signature from a file.
        Stores BOTH as ImageField AND as base64 for maximum compatibility.
        """
        # Save to ImageField (traditional)
        if self.signature_image:
            self.signature_image.delete(save=False)

        self.signature_image = signature_file

        # ALSO save as base64 (for PyInstaller apps)
        try:
            signature_file.seek(0)  # Reset file pointer
            image_data = signature_file.read()
            base64_encoded = base64.b64encode(image_data).decode('utf-8')
            self.signature_data = f"data:image/png;base64,{base64_encoded}"
        except Exception as e:
            # If base64 conversion fails, log but continue
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to create base64 signature for {self.user.username}: {e}")

        self.is_user_drawn = True
        self.is_active = True
        self.signature_hash = self.generate_signature_hash()
        super().save()

        # Update user profile flag
        try:
            profile = self.user.userprofile
            profile.has_uploaded_signature = True
            profile.save()
        except Exception:
            pass

    def save_signature_from_base64(self, base64_data):
        """
        Save signature directly from base64 string.
        Perfect for web-based signature pads or direct database imports.
        """
        self.signature_data = base64_data
        self.is_user_drawn = True
        self.is_active = True
        self.signature_hash = self.generate_signature_hash()
        self.save()

        # Update user profile flag
        try:
            profile = self.user.userprofile
            profile.has_uploaded_signature = True
            profile.save()
        except Exception:
            pass

    def get_signature_as_base64(self):
        """
        Get signature as base64 string.
        Returns base64 data if available, otherwise converts ImageField to base64.
        """
        # First priority: return stored base64
        if self.signature_data:
            return self.signature_data

        # Second priority: convert ImageField to base64
        if self.signature_image:
            try:
                from django.core.files.storage import default_storage
                import os

                # Try to read the file
                if default_storage.exists(self.signature_image.name):
                    with default_storage.open(self.signature_image.name, 'rb') as f:
                        image_data = f.read()
                elif hasattr(self.signature_image, 'path') and os.path.exists(self.signature_image.path):
                    with open(self.signature_image.path, 'rb') as f:
                        image_data = f.read()
                else:
                    return None

                # Convert to base64
                base64_encoded = base64.b64encode(image_data).decode('utf-8')
                return f"data:image/png;base64,{base64_encoded}"

            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to convert ImageField to base64 for {self.user.username}: {e}")
                return None

        return None

    def get_signature_as_image_buffer(self):
        """
        Convert signature to image buffer for ReportLab/PIL.
        Works with both base64 and ImageField storage.
        """
        # Try base64 first
        if self.signature_data:
            try:
                # Remove data URI prefix if present
                if 'base64,' in self.signature_data:
                    base64_str = self.signature_data.split('base64,')[1]
                else:
                    base64_str = self.signature_data

                # Decode base64
                image_data = base64.b64decode(base64_str)

                # Create image buffer
                img_buffer = io.BytesIO(image_data)
                img_buffer.seek(0)

                return img_buffer

            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error decoding base64 signature for {self.user.username}: {e}")

        # Fall back to ImageField
        if self.signature_image:
            try:
                from django.core.files.storage import default_storage
                import os

                if default_storage.exists(self.signature_image.name):
                    with default_storage.open(self.signature_image.name, 'rb') as f:
                        img_buffer = io.BytesIO(f.read())
                        img_buffer.seek(0)
                        return img_buffer
                elif hasattr(self.signature_image, 'path') and os.path.exists(self.signature_image.path):
                    with open(self.signature_image.path, 'rb') as f:
                        img_buffer = io.BytesIO(f.read())
                        img_buffer.seek(0)
                        return img_buffer

            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error reading signature file for {self.user.username}: {e}")

        return None

    def has_signature(self):
        """Check if user has any signature (base64 or ImageField)"""
        return bool(self.signature_data or self.signature_image)

    def generate_signature_hash(self):
        """Generate a unique hash for integrity checking"""
        try:
            profile = self.user.userprofile
            role_info = f"{profile.role}_{profile.level or ''}_{profile.employee_id or ''}"
        except Exception:
            role_info = "default"

        base_string = (
            f"{self.user.username}{self.user.email}"
            f"{self.signature_id}{self.user.date_joined.timestamp()}"
            f"{role_info}"
        )
        return hashlib.sha256(base_string.encode()).hexdigest()

    def to_payload(self):
        """Serialize for sync - includes base64 signature data"""
        return {
            "id": str(self.id),
            "user": str(self.user.id) if self.user else None,
            "signature_id": str(self.signature_id),
            "signature_data": self.signature_data,  # Base64 syncs across systems
            "signature_hash": self.signature_hash,
            "is_active": self.is_active,
            "is_user_drawn": self.is_user_drawn,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat(),
            "needs_sync": self.needs_sync,
        }

    def __str__(self):
        storage_type = "Base64" if self.signature_data else "ImageField" if self.signature_image else "None"
        return f"Signature for {self.user.username} ({storage_type})"

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'User Signature'
        verbose_name_plural = 'User Signatures'


class UserPasswordReset(models.Model):
    """Secure password reset with enhanced security features"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )
    active_status = models.BooleanField(default=True)
    reset_code = models.CharField(max_length=6, unique=True, blank=True)
    token = models.UUIDField(default=uuid.uuid4, editable=False, null=True, blank=True)

    # Timing and expiry
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    used_at = models.DateTimeField(null=True, blank=True)

    # Security tracking
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    pending_delete = models.BooleanField(default=False)

    # Request metadata
    requested_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_resets'
    )

    # Offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    syncable = True

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Password Reset"
        verbose_name_plural = "Password Resets"
        indexes = [
            models.Index(fields=['reset_code', 'is_used']),
            models.Index(fields=['user', 'expires_at']),
            models.Index(fields=['token']),
        ]

    def save(self, *args, **kwargs):
        """Auto-generate secure reset code and set expiry"""
        if not self.reset_code:
            self.reset_code = self.generate_unique_code()
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(
                minutes=getattr(settings, 'PASSWORD_RESET_TIMEOUT_MINUTES', 30)
            )
        super().save(*args, **kwargs)

    def generate_unique_code(self, max_attempts=1000):
        """Generate cryptographically secure unique 6-digit code"""
        for attempt in range(max_attempts):
            code = ''.join(secrets.choice(string.digits) for _ in range(6))

            if not UserPasswordReset.objects.filter(
                reset_code=code,
                is_used=False,
                expires_at__gt=timezone.now()
            ).exists():
                return code

        raise ValueError("Unable to generate unique reset code after maximum attempts")

    def is_expired(self):
        """Check if the reset code has expired"""
        return self.expires_at and timezone.now() > self.expires_at

    def is_valid(self):
        """Check if reset code is valid"""
        return (not self.is_used and
                not self.is_expired() and
                self.attempts < self.max_attempts)

    def increment_attempt(self):
        """Increment attempt counter"""
        self.attempts += 1
        self.save(update_fields=['attempts'])

        if self.attempts >= self.max_attempts:
            self.is_used = True
            self.save(update_fields=['is_used'])

    def use_code(self):
        """Mark the reset code as used"""
        if not self.is_valid():
            raise ValueError("Cannot use invalid reset code")

        self.is_used = True
        self.used_at = timezone.now()
        self.save(update_fields=['is_used', 'used_at'])

    def get_time_remaining(self):
        """Get remaining time before expiry"""
        if self.is_expired():
            return timedelta(0)
        return self.expires_at - timezone.now()

    def __str__(self):
        status = "USED" if self.is_used else ("EXPIRED" if self.is_expired() else "ACTIVE")
        return f"Reset code for {self.user.username} - {self.reset_code} [{status}]"


class UserSecurityLog(models.Model):
    """Log security events for audit trail"""

    EVENT_TYPES = [
        ('LOGIN_SUCCESS', 'Successful Login'),
        ('LOGIN_FAILURE', 'Failed Login'),
        ('LOGOUT', 'Logout'),
        ('PASSWORD_RESET_REQUEST', 'Password Reset Requested'),
        ('PASSWORD_RESET_SUCCESS', 'Password Reset Successful'),
        ('PASSWORD_CHANGE', 'Password Changed'),
        ('ACCOUNT_LOCKED', 'Account Locked'),
        ('ACCOUNT_UNLOCKED', 'Account Unlocked'),
        ('SIGNATURE_REGENERATED', 'Signature Regenerated'),
        ('SIGNATURE_UPLOADED', 'Signature Uploaded'),
        ('PROFILE_UPDATED', 'Profile Updated'),
        ('FIRST_LOGIN_SETUP', 'First Login Setup Completed'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=30, choices=EVENT_TYPES)
    event_description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    additional_data = models.JSONField(default=dict, blank=True)

    # Offline sync
    needs_sync = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    pending_delete = models.BooleanField(default=False)
    active_status = models.BooleanField(default=True)

    syncable = True

    class Meta:
        ordering = ['-timestamp']
        verbose_name = "Security Log"
        verbose_name_plural = "Security Logs"
        indexes = [
            models.Index(fields=['user', 'event_type']),
            models.Index(fields=['timestamp']),
            models.Index(fields=['ip_address']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.get_event_type_display()} - {self.timestamp}"

    @classmethod
    def log_event(cls, user, event_type, description="", ip_address=None, user_agent="", **kwargs):
        """Convenience method to log security events"""
        return cls.objects.create(
            user=user,
            event_type=event_type,
            event_description=description,
            ip_address=ip_address,
            user_agent=user_agent,
            additional_data=kwargs
        )


# ===================================================================
# Fixed Signal Handlers (no more infinite loops)
# ===================================================================

@receiver(post_save, sender=User)
def create_user_signature(sender, instance, created, **kwargs):
    """Create signature when user is created"""
    if created:
        UserSignature.objects.get_or_create(user=instance)


@receiver(post_save, sender=UserProfile)
def update_user_signature_conditionally(sender, instance, **kwargs):
    """Update signature when profile changes, but respect user-drawn signatures"""
    try:
        signature = instance.user.signature
        # Only regenerate if it's system-generated (not user-drawn)
        if not signature.is_user_drawn:
            signature.regenerate_signature()
    except UserSignature.DoesNotExist:
        # Create new signature if none exists
        UserSignature.objects.create(user=instance.user)


# Fixed sync signals - no more infinite recursion
@receiver(post_save, sender=UserProfile)
def mark_user_for_sync_on_profile_change(sender, instance, **kwargs):
    """Mark User for sync when their profile changes"""
    # Avoid infinite recursion
    if 'update_fields' in kwargs and kwargs['update_fields'] == ['needs_sync']:
        return

    # Use update() to avoid triggering signals
    User.objects.filter(pk=instance.user_id).update(
        needs_sync=True,
        updated_at=timezone.now()
    )


@receiver(post_save, sender=UserSignature)
def mark_user_for_sync_on_signature_change(sender, instance, **kwargs):
    """Mark User for sync when their signature changes"""
    # Avoid infinite recursion
    if 'update_fields' in kwargs and kwargs['update_fields'] == ['needs_sync']:
        return

    # Use update() to avoid triggering signals
    User.objects.filter(pk=instance.user_id).update(
        needs_sync=True,
        updated_at=timezone.now()
    )


@receiver(post_save, sender=UserPasswordReset)
def mark_user_for_sync_on_password_reset(sender, instance, **kwargs):
    """Mark User for sync when password reset is created/updated"""
    # Avoid infinite recursion
    if 'update_fields' in kwargs and kwargs['update_fields'] == ['needs_sync']:
        return

    # Use update() to avoid triggering signals
    User.objects.filter(pk=instance.user_id).update(
        needs_sync=True,
        updated_at=timezone.now()
    )


@receiver(post_save, sender=UserSecurityLog)
def mark_user_for_sync_on_security_log(sender, instance, **kwargs):
    """Mark User for sync when security events are logged"""
    # Avoid infinite recursion
    if 'update_fields' in kwargs and kwargs['update_fields'] == ['needs_sync']:
        return

    # Use update() to avoid triggering signals
    User.objects.filter(pk=instance.user_id).update(
        needs_sync=True,
        updated_at=timezone.now()
    )
