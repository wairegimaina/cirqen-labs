from django.contrib import admin
from .models import EquipmentCategory

@admin.register(EquipmentCategory)
class EquipmentCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_critical", "description")
    list_filter = ("is_critical",)
    search_fields = ("name", "description")
    ordering = ("-is_critical", "name")
