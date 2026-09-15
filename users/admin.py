from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import UserProfile, UserSignature   # adjust the import path if different

User = get_user_model()


# ----------  UserSignature ----------
@admin.register(UserSignature)
class UserSignatureAdmin(admin.ModelAdmin):
    list_display  = ["user", "signature_id", "is_active", "created_at"]
    list_filter   = ["is_active", "created_at"]
    search_fields = ["user__username", "user__first_name", "user__last_name", "signature_id"]
    readonly_fields = ["signature_id", "signature_hash", "created_at", "updated_at"]

    def regenerate_signature(self, request, queryset):
        for signature in queryset:
            signature.regenerate_signature()
        self.message_user(request, "Signatures regenerated successfully")
    regenerate_signature.short_description = "Regenerate selected signatures"
    actions = ["regenerate_signature"]


# ----------  Inline for UserProfile ----------
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profile"
    fk_name = "user"
    fields = ("role", "department", "workshop", "level")


# ----------  Custom User Admin ----------
@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    """
    Admin for your UUID-primary-key CustomUser.
    Extends Django’s built-in UserAdmin so you still get password hashing,
    permissions, etc.
    """
    inlines = [UserProfileInline]

    # Which fields to show in the list
    list_display = ("username", "email", "first_name", "last_name", "is_staff", "is_active")
    list_filter  = ("is_staff", "is_active", "is_superuser", "groups")

    # Fieldsets for editing an existing user
    fieldsets = (
        (None,               {"fields": ("username", "password")}),
        ("Personal info",    {"fields": ("first_name", "last_name", "email")}),
        ("Permissions",      {"fields": ("is_active", "is_staff", "is_superuser",
                                         "groups", "user_permissions")}),
        ("Important dates",  {"fields": ("last_login", "date_joined")}),
    )

    # Fieldsets for adding a new user
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "email", "password1", "password2",
                       "is_staff", "is_active"),
        }),
    )

    search_fields  = ("username", "email", "first_name", "last_name")
    ordering       = ("username",)

    def get_inline_instances(self, request, obj=None):
        """
        Only show the profile inline if the user object already exists.
        """
        if not obj:
            return []
        return super().get_inline_instances(request, obj)


# ----------  UserProfile stand-alone admin ----------
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ["user", "role", "department", "workshop", "level"]
    list_filter   = ["role", "department", "workshop", "level"]
    search_fields = ["user__username", "user__first_name", "user__last_name"]
