from django.contrib import admin
from .models import (
    Tools, ToolsManufacturer, Toolname,
    Accessories, AccessoriesManufacturer, Accessoriesname,
    AccessoryRequest, AccessoryRequestHistory
)


@admin.register(ToolsManufacturer)
class ToolsManufacturerAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'updated_at', 'needs_sync', 'pending_delete')
    search_fields = ('name',)
    list_filter = ('needs_sync', 'pending_delete', 'created_at')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(Toolname)
class ToolnameAdmin(admin.ModelAdmin):
    list_display = ('name', 'active_status', 'created_at', 'updated_at', 'needs_sync', 'pending_delete')
    search_fields = ('name',)
    list_filter = ('active_status', 'needs_sync', 'pending_delete', 'created_at')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(Tools)
class ToolsAdmin(admin.ModelAdmin):
    list_display = ('get_tool_name', 'get_manufacturer', 'model', 'serial_number', 'workshop', 'active_status', 'created_at')
    search_fields = ('name__name', 'manufacturer__name', 'model', 'serial_number')
    list_filter = ('workshop', 'active_status', 'pending_delete', 'created_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
    raw_id_fields = ('name', 'manufacturer', 'workshop')

    def get_tool_name(self, obj):
        return obj.name.name if obj.name else '-'
    get_tool_name.short_description = 'Tool Name'

    def get_manufacturer(self, obj):
        return obj.manufacturer.name if obj.manufacturer else '-'
    get_manufacturer.short_description = 'Manufacturer'


@admin.register(AccessoriesManufacturer)
class AccessoriesManufacturerAdmin(admin.ModelAdmin):
    list_display = ('name', 'active_status', 'created_at', 'updated_at', 'needs_sync', 'pending_delete')
    search_fields = ('name',)
    list_filter = ('active_status', 'needs_sync', 'pending_delete', 'created_at')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(Accessoriesname)
class AccessoriesnameAdmin(admin.ModelAdmin):
    list_display = ('name', 'active_status', 'created_at', 'updated_at', 'needs_sync', 'pending_delete')
    search_fields = ('name',)
    list_filter = ('active_status', 'needs_sync', 'pending_delete', 'created_at')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(Accessories)
class AccessoriesAdmin(admin.ModelAdmin):
    list_display = ('get_accessory_name', 'get_equipment', 'get_manufacturer', 'stock_count', 'unit_cost', 'workshop', 'active_status', 'created_at')
    search_fields = ('name__name', 'manufacturer__name', 'equipment_description__name', 'note')
    list_filter = ('workshop', 'active_status', 'pending_delete', 'created_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
    raw_id_fields = ('name', 'manufacturer', 'equipment_description', 'workshop')

    def get_accessory_name(self, obj):
        return obj.name.name if obj.name else '-'
    get_accessory_name.short_description = 'Accessory Name'

    def get_equipment(self, obj):
        return obj.equipment_description.name if obj.equipment_description else '-'
    get_equipment.short_description = 'Equipment'

    def get_manufacturer(self, obj):
        return obj.manufacturer.name if obj.manufacturer else '-'
    get_manufacturer.short_description = 'Manufacturer'


class AccessoryRequestHistoryInline(admin.TabularInline):
    model = AccessoryRequestHistory
    extra = 0
    readonly_fields = ('action', 'performed_by', 'timestamp', 'notes', 'previous_status', 'new_status')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AccessoryRequest)
class AccessoryRequestAdmin(admin.ModelAdmin):
    list_display = (
        'get_request_display',
        'request_type',
        'status',
        'requested_by',
        'workshop',
        'requested_quantity',
        'requested_at',
        'approved_by',
        'accepted_by'
    )
    search_fields = (
        'accessory_name',
        'manufacturer_name',
        'requested_by__user__username',
        'approved_by__user__username',
        'accepted_by__user__username',
        'note'
    )
    list_filter = (
        'status',
        'request_type',
        'workshop',
        'requested_at',
        'approved_at',
        'accepted_at'
    )
    readonly_fields = (
        'id',
        'created_at',
        'updated_at',
        'requested_at',
        'approved_at',
        'accepted_at',
        'total_cost'
    )
    raw_id_fields = (
        'existing_accessory',
        'equipment_description',
        'requested_by',
        'approved_by',
        'accepted_by',
        'created_accessory',
        'workshop'
    )
    inlines = [AccessoryRequestHistoryInline]

    fieldsets = (
        ('Request Information', {
            'fields': (
                'id',
                'request_type',
                'status',
                'requested_at',
            )
        }),
        ('New Accessory Details', {
            'fields': (
                'accessory_name',
                'manufacturer_name',
            ),
            'classes': ('collapse',),
        }),
        ('Restock Details', {
            'fields': (
                'existing_accessory',
            ),
            'classes': ('collapse',),
        }),
        ('Common Details', {
            'fields': (
                'equipment_description',
                'requested_quantity',
                'unit_cost',
                'total_cost',
                'note',
                'workshop',
            )
        }),
        ('Requester Information', {
            'fields': (
                'requested_by',
            )
        }),
        ('HOD Approval', {
            'fields': (
                'approved_by',
                'approved_at',
                'approval_reason',
            )
        }),
        ('Workshop Acceptance', {
            'fields': (
                'accepted_by',
                'accepted_at',
                'acceptance_note',
                'created_accessory',
            )
        }),
        ('Sync Information', {
            'fields': (
                'needs_sync',
                'updated_at',
                'created_at',
                'pending_delete',
                'active_status',
            ),
            'classes': ('collapse',),
        }),
    )

    def get_request_display(self, obj):
        if obj.request_type == 'new':
            return f"New: {obj.accessory_name or 'Unnamed'}"
        else:
            accessory_name = obj.existing_accessory.name.name if obj.existing_accessory and obj.existing_accessory.name else "Unknown"
            return f"Restock: {accessory_name}"
    get_request_display.short_description = 'Request'

    def total_cost(self, obj):
        return obj.total_cost
    total_cost.short_description = 'Total Cost (KSh)'


@admin.register(AccessoryRequestHistory)
class AccessoryRequestHistoryAdmin(admin.ModelAdmin):
    list_display = ('request', 'action', 'performed_by', 'timestamp', 'previous_status', 'new_status')
    search_fields = ('request__accessory_name', 'performed_by__user__username', 'notes')
    list_filter = ('action', 'timestamp')
    readonly_fields = ('id', 'timestamp', 'updated_at')
    raw_id_fields = ('request', 'performed_by')

    def has_add_permission(self, request):
        return False
