
from django.contrib import admin
from .models import ClientMachine, UpdatePackage, UpdateHistory, UpdateSettings


@admin.register(UpdatePackage)
class UpdatePackageAdmin(admin.ModelAdmin):
    list_display = ['version', 'size_mb', 'critical', 'created_at', 'is_active']
    list_filter = ['critical', 'is_active', 'created_at']
    search_fields = ['version', 'changes']
    readonly_fields = ['created_at', 'size_mb']

    fieldsets = (
        ('Version Info', {
            'fields': ('version', 'min_version', 'created_at')
        }),
        ('Package Details', {
            'fields': ('size_bytes', 'checksum', 'package_path')
        }),
        ('Release Notes', {
            'fields': ('changes', 'critical')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
    )


@admin.register(UpdateHistory)
class UpdateHistoryAdmin(admin.ModelAdmin):
    list_display = ['package', 'machine_id', 'status', 'started_at', 'completed_at']
    list_filter = ['status', 'auto_applied', 'started_at']
    search_fields = ['machine_id', 'client_id', 'error_message']
    readonly_fields = ['started_at', 'completed_at']

    fieldsets = (
        ('Update Info', {
            'fields': ('package', 'machine_id', 'client_id')
        }),
        ('Status', {
            'fields': ('status', 'started_at', 'completed_at')
        }),
        ('Details', {
            'fields': ('backup_path', 'error_message', 'applied_by', 'auto_applied')
        }),
    )


@admin.register(UpdateSettings)
class UpdateSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Auto-Check Settings', {
            'fields': ('auto_check_enabled', 'check_interval_hours', 'last_check')
        }),
        ('Auto-Apply Settings', {
            'fields': ('auto_apply_updates', 'auto_apply_critical')
        }),
        ('Component Settings', {
            'fields': (
                'update_templates',
                'update_static',
                'update_django_apps',
                'update_python_code',
                'update_migrations'
            )
        }),
    )

    def has_add_permission(self, request):
        # Singleton - only one instance
        return not UpdateSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Cannot delete settings
        return False



@admin.register(ClientMachine)
class ClientMachineAdmin(admin.ModelAdmin):
    list_display = ['machine_id', 'hostname', 'current_version', 'last_check', 'last_update']
    list_filter = ['current_version']
    search_fields = ['machine_id', 'hostname']
    readonly_fields = ['registered_at', 'last_check', 'last_update']

    def has_add_permission(self, request):
        return False  # Machines register themselves
