# forms.py
from django import forms

from workshop.models import Workshop
from .models import Tools, Accessories, AccessoryRequest
from django.core.exceptions import ValidationError
from django.contrib import messages
from users.models import UserProfile 
from django.db import models 

class ToolsForm(forms.ModelForm):
    class Meta:
        model = Tools
        fields = ['name', 'description', 'manufacturer', 'serial_number']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'manufacturer': forms.TextInput(attrs={'class': 'form-control'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        if self.request and self.request.user.userprofile.role == 'HOD':
            raise ValidationError("HOD users are not allowed to add or edit tools.")
        if self.request and self.request.user.userprofile.role in ['Tech', 'Nurse']:
            self.fields['workshop'] = forms.ModelChoiceField(
                queryset=Workshop.objects.filter(id=self.request.user.userprofile.workshop.id),
                widget=forms.HiddenInput(),
                initial=self.request.user.userprofile.workshop
            )
            self.fields['workshop'].required = True

class AccessoriesForm(forms.ModelForm):
    class Meta:
        model = Accessories
        fields = ['name', 'description', 'manufacturer', 'note', 'stock_count']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'manufacturer': forms.TextInput(attrs={'class': 'form-control'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'stock_count': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
        }
        labels = {
            'stock_count': 'Current Stock',
            'name': 'Accessory Name',
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        if self.request and self.request.user.userprofile.role == 'HOD':
            raise ValidationError("HOD users are not allowed to add or edit accessories.")
        if self.request and self.request.user.userprofile.role in ['Tech', 'Nurse']:
            self.fields['workshop'] = forms.ModelChoiceField(
                queryset=Workshop.objects.filter(id=self.request.user.userprofile.workshop.id),
                widget=forms.HiddenInput(),
                initial=self.request.user.userprofile.workshop
            )
            self.fields['workshop'].required = True

    def clean_stock_count(self):
        stock_count = self.cleaned_data.get('stock_count')
        if stock_count is None or stock_count < 0:
            raise ValidationError("Stock count cannot be negative.")
        return stock_count

class AccessoryRequestForm(forms.ModelForm):
    new_accessory_name = forms.CharField(
        max_length=200,
        required=False,
        help_text="Enter a new accessory name if it's not in the list above.",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )

    class Meta:
        model = AccessoryRequest
        fields = ['accessory', 'requested_quantity', 'note', 'new_accessory_name']
        widgets = {
            'accessory': forms.Select(attrs={'class': 'form-control'}),
            'requested_quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        labels = {
            'accessory': 'Select Existing Accessory:',
            'new_accessory_name': 'Or propose a new accessory:',
        }

    def __init__(self, *args, **kwargs):
        self.workshop = kwargs.pop('workshop', None)
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        if self.request and self.request.user.userprofile.role == 'HOD':
            raise ValidationError("HOD users are not allowed to request accessories.")
        
        order_field = 'name'
        if self.workshop:
            self.fields['accessory'].queryset = Accessories.objects.filter(
                models.Q(workshop=self.workshop) | models.Q(workshop__isnull=True)
            ).order_by(order_field)
        else:
            self.fields['accessory'].queryset = Accessories.objects.all().order_by(order_field)

        self.fields['accessory'].required = False
        self.fields['new_accessory_name'].required = False

    def clean(self):
        cleaned_data = super().clean()
        accessory = cleaned_data.get('accessory')
        new_accessory_name = cleaned_data.get('new_accessory_name')
        requested_quantity = cleaned_data.get('requested_quantity')

        if not accessory and not new_accessory_name:
            raise ValidationError("Either select an existing accessory or provide a new accessory name.")
        if accessory and new_accessory_name:
            raise ValidationError("Please select an existing accessory OR provide a new accessory name, not both.")

        if new_accessory_name and not accessory:
            normalized_new_name = new_accessory_name.strip().lower()
            existing_accessory = Accessories.objects.filter(
                models.Q(name__iexact=normalized_new_name, workshop=self.workshop) |
                models.Q(name__iexact=normalized_new_name, workshop__isnull=True)
            ).first()
            if existing_accessory:
                cleaned_data['accessory'] = existing_accessory
                cleaned_data['new_accessory_name'] = ''
                if self.request:
                    messages.info(self.request, f"Accessory '{new_accessory_name}' already exists. Requesting existing accessory.")
            # Do not create new accessory here; HOD will handle it upon approval

        if requested_quantity is None or requested_quantity <= 0:
            raise ValidationError("Requested quantity must be at least 1.")

        return cleaned_data

class AccessoryRequestResponseForm(forms.ModelForm):
    class Meta:
        model = AccessoryRequest
        fields = ['status', 'response_reason']
        widgets = {
            'status': forms.Select(choices=[('Approved', 'Approved'), ('Declined', 'Declined')], attrs={'class': 'form-control'}),
            'response_reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def clean_status(self):
        status = self.cleaned_data.get('status')
        if not status:
            raise ValidationError("Please select a valid status.")
        return status