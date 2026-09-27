from django.contrib import admin

from .models import ServiceContract


@admin.register(ServiceContract)
class ServiceContractAdmin(admin.ModelAdmin):
    list_display = ("equipment", "supplier", "cover", "start_date", "end_date")
    list_filter = ("cover",)
    search_fields = ("equipment__serial_number", "contract_number")
    raw_id_fields = ("equipment",)
