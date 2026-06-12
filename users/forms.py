import random

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.core.exceptions import ValidationError

from Inventory.models import Department
from workshop.models import Workshop

from .models import UserPasswordReset, UserProfile, UserSignature

# Use get_user_model() instead of importing User directly
User = get_user_model()


class CustomLoginForm(AuthenticationForm):
    error_messages = {
        'invalid_login': "Invalid username or password. Please check your credentials and try again.",
        'inactive': "This account is inactive. Please contact the administrator.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['username'].widget.attrs.update({
            'placeholder': ' Enter your username',
            'class': 'form-control',
            'autocomplete': 'off',
            'required': True
        })
        self.fields['password'].widget.attrs.update({
            'placeholder': ' Enter your password',
            'class': 'form-control',
            'autocomplete': 'current-password',
            'required': True,
            'minlength': 8
        })

    def confirm_login_allowed(self, user):
        """Enhanced validation before login."""
        if not user.active_status:
            raise forms.ValidationError(
                'This account has been deactivated. Please contact the administrator.',
                code='inactive_account'
            )
        if not hasattr(user, 'userprofile'):
            raise forms.ValidationError(
                'No user profile found. Please contact the administrator.',
                code='no_profile'
            )
        # Auto-extend session on any successful activity
        if hasattr(self, 'request'):
            self.request.session.set_expiry(3600)  # 1 hour from now


class UserCreationForm(forms.ModelForm):
    """Form for HOD to create new users with profiles"""

    first_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control'})
    )

    # Profile fields
    role = forms.ChoiceField(
        choices=UserProfile.ROLE_CHOICES,
        required=True,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    department = forms.ModelChoiceField(
        queryset=Department.objects.all(),
        required=False,
        help_text="Required only for In-Charge role",
        widget=forms.Select(attrs={'class': 'form-control'}),
        empty_label=" Select department "
    )
    workshop = forms.ModelChoiceField(
        queryset=Workshop.objects.all(),
        required=False,
        help_text="Required for all Tech roles",
        widget=forms.Select(attrs={'class': 'form-control'}),
        empty_label="Select Workshop "
    )
    level = forms.ChoiceField(
        choices=[('', 'Select Level ')] + UserProfile.LEVEL_CHOICES,
        required=False,
        help_text="Required for all Tech roles",
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure all base User fields have proper styling
        for field_name in self.Meta.fields:
            self.fields[field_name].widget.attrs['class'] = 'form-control'

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get('role')
        level = cleaned_data.get('level')
        workshop = cleaned_data.get('workshop')
        department = cleaned_data.get('department')

        # HOD: Cannot have department, workshop, or level
        if role == 'HOD':
            if department:
                raise ValidationError("HOD cannot be assigned to any department.")
            if workshop:
                raise ValidationError("HOD cannot be assigned to any workshop.")
            if level:
                raise ValidationError("HOD cannot have a level.")

        # NIC: Must have department, cannot have workshop or level
        elif role == 'NIC':
            if not department:
                raise ValidationError("In-charge must be assigned to a department.")
            if workshop:
                raise ValidationError("In-charge be assigned to a workshop.")
            if level:
                raise ValidationError("In-charge have a level.")

        # Tech: Must have level AND workshop, cannot have department
        elif role == 'Tech':
            if not level:
                raise ValidationError("All Tech roles must have a level assigned.")
            if not workshop:
                raise ValidationError("All Tech roles must be assigned to a workshop.")
            if department:
                raise ValidationError("Tech cannot be assigned to a department.")

        return cleaned_data

    def clean_email(self):
        """Ensure email is unique"""
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email=email).exists():
            raise ValidationError("A user with this email already exists.")
        return email

    def generate_username(self, first_name):
        """Generate unique username from first name + 4 random numbers"""
        base_username = first_name.lower().replace(' ', '').replace('-', '')

        # Ensure base username is not empty
        if not base_username:
            base_username = 'user'

        # Generate username with 4 random numbers
        for _ in range(100):  # Try up to 100 times
            random_numbers = ''.join([str(random.randint(0, 9)) for _ in range(4)])
            username = f"{base_username}{random_numbers}"

            if not User.objects.filter(username=username).exists():
                return username

        # Fallback if all attempts fail
        import uuid
        return f"{base_username}{str(uuid.uuid4())[:4]}"

    def save(self, commit=True):
        if not commit:
            return super().save(commit=False)

        # Create User instance
        user = User(
            username=self.generate_username(self.cleaned_data['first_name']),
            first_name=self.cleaned_data['first_name'],
            last_name=self.cleaned_data['last_name'],
            email=self.cleaned_data['email'],
            active_status=True
        )

        # Set a temporary password (should be changed on first login)
        temp_password = f"temp{random.randint(1000, 9999)}"
        user.set_password(temp_password)
        user.save()

        # Create UserProfile based on role
        profile_data = {
            'user': user,
            'role': self.cleaned_data['role']
        }

        # Add role-specific fields
        if self.cleaned_data['role'] == 'NIC':
            profile_data['department'] = self.cleaned_data['department']
        elif self.cleaned_data['role'] == 'Tech':
            profile_data['level'] = self.cleaned_data['level']
            profile_data['workshop'] = self.cleaned_data['workshop']  # Now required for all Tech

        profile = UserProfile.objects.create(**profile_data)

        # Create UserSignature (handled by signal, but ensure it exists)
        UserSignature.objects.get_or_create(user=user)

        # Store the temporary password for display
        user.temp_password = temp_password

        return user


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email address',
            'required': True
        }),
        label="Email Address"
    )

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if not User.objects.filter(email=email).exists():
            raise ValidationError("No account found with this email address.")
        return email


class VerifyResetCodeForm(forms.Form):
    reset_code = forms.CharField(
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={
            'class': 'form-control text-center',
            'placeholder': '######',
            'style': 'font-size: 18px; letter-spacing: 5px;',
            'maxlength': '6',
            'required': True
        }),
        label="Reset Code"
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean_reset_code(self):
        code = self.cleaned_data.get('reset_code')
        if not code or len(code) != 6 or not code.isdigit():
            raise ValidationError("Please enter a valid 6-digit code.")

        try:
            reset_request = UserPasswordReset.objects.get(
                reset_code=code,
                user=self.user if self.user else None
            )

            if not reset_request.is_valid():
                if reset_request.is_used:
                    raise ValidationError("This reset code has already been used.")
                elif reset_request.is_expired():
                    raise ValidationError("This reset code has expired. Please request a new one.")

            # Store the reset request for later use
            self.reset_request = reset_request

        except UserPasswordReset.DoesNotExist:
            raise ValidationError("Invalid reset code. Please check and try again.")

        return code


class CustomSetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter new password',
            'autocomplete': 'new-password'
        }),
        strip=False,
        help_text="Password must be at least 8 characters long."
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm new password',
            'autocomplete': 'new-password'
        }),
        strip=False,
    )
