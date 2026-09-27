from django.contrib import admin

from .models import ServiceContract, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_person", "phone", "email")
    search_fields = ("name",)


@admin.register(ServiceContract)
class ServiceContractAdmin(admin.ModelAdmin):
    list_display = ("equipment", "supplier", "cover", "start_date", "end_date")
    list_filter = ("cover",)
    search_fields = ("equipment__serial_number", "contract_number")
    raw_id_fields = ("equipment",)
