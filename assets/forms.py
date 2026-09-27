from django import forms

from Inventory.models import Equipment

from .models import ServiceContract, Supplier


class DateInput(forms.DateInput):
    input_type = "date"


class AssetDetailsForm(forms.ModelForm):
    class Meta:
        model = Equipment
        fields = ["purchase_date", "warranty_end", "purchase_cost", "expected_life_years"]
        widgets = {"purchase_date": DateInput(), "warranty_end": DateInput()}
        labels = {"purchase_cost": "Purchase cost (KSh)", "expected_life_years": "Expected life (years)"}


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact_person", "phone", "email", "address", "notes"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}


class ContractForm(forms.ModelForm):
    serial_number = forms.CharField(label="Machine serial number", max_length=100)

    class Meta:
        model = ServiceContract
        fields = ["supplier", "contract_number", "cover", "start_date", "end_date", "annual_cost", "notes"]
        widgets = {"start_date": DateInput(), "end_date": DateInput(), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {"annual_cost": "Annual cost (KSh)"}

    def __init__(self, *args, equipment_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.equipment_qs = equipment_qs
        self.fields["supplier"].queryset = Supplier.objects.filter(active_status=True)
        if self.instance.equipment_id:
            self.fields["serial_number"].initial = self.instance.equipment.serial_number

    def clean_serial_number(self):
        serial = self.cleaned_data["serial_number"].strip().upper()
        machine = self.equipment_qs.filter(serial_number__iexact=serial).first()
        if not machine:
            raise forms.ValidationError("No machine with that serial number in your area.")
        self.cleaned_data["equipment"] = machine
        return serial

    def clean(self):
        data = super().clean()
        if data.get("start_date") and data.get("end_date") and data["end_date"] < data["start_date"]:
            self.add_error("end_date", "The end date is before the start date.")
        return data

    def save(self, commit=True):
        self.instance.equipment = self.cleaned_data["equipment"]
        return super().save(commit)
