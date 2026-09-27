"""Service contracts for medical equipment.

Suppliers and warranties are the Inventory module's (Inventory.Supplier,
Inventory.Warranty); a machine can also be under one or more service
contracts with a supplier. Synced to HQ.
"""
import uuid

from django.db import models
from django.utils import timezone


class ServiceContract(models.Model):
    COVER_CHOICES = [
        ("full", "Full cover (parts and labour)"),
        ("labour", "Labour only"),
        ("parts", "Parts only"),
        ("ppm", "Preventive maintenance"),
        ("calibration", "Calibration"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey("Inventory.Equipment", on_delete=models.CASCADE, related_name="service_contracts")
    supplier = models.ForeignKey("Inventory.Supplier", on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="service_contracts")
    contract_number = models.CharField(max_length=80, blank=True)
    cover = models.CharField(max_length=20, choices=COVER_CHOICES, default="full")
    start_date = models.DateField()
    end_date = models.DateField()
    annual_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    active_status = models.BooleanField(default=True)
    pending_delete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    syncable = True

    class Meta:
        ordering = ["end_date"]
        indexes = [models.Index(fields=["end_date"])]

    def __str__(self):
        return f"{self.get_cover_display()} for {self.equipment} until {self.end_date:%d %b %Y}"

    @property
    def is_current(self):
        today = timezone.localdate()
        return self.start_date <= today <= self.end_date
