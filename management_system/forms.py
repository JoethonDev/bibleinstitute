from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django import forms
import secrets

from django.core.exceptions import ValidationError
from .models import User, Role, Course, Lesson, AcademicYear, CourseOffering, assign_academic_date
from .utils.validators import normalize_phone, validate_identity_by_type
from django.utils.translation import gettext_lazy as _ # Import gettext_lazy


class UserLoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super(UserLoginForm, self).__init__(*args, **kwargs)
        self.fields['username'].label = _("Username") # Localized
        self.fields['password'].label = _("Password") # Localized

    username = UsernameField(widget=forms.TextInput(
        attrs={ 
               'placeholder': _('Username'), # Localized
               'id': 'username'
        }))
    password = forms.CharField(widget=forms.PasswordInput(
        attrs={
            'placeholder': _('Password'), # Localized
            'id': 'password',
        }
))
    
class UserCreationForm(forms.ModelForm):
    date = assign_academic_date()
    
    def __init__(self, *args, **kwargs):
        super(UserCreationForm, self).__init__(*args, **kwargs)
        self.fields['username'].label = _("Username") # Localized
        self.fields['first_name'].label = _("First Name") # Localized
        self.fields['last_name'].label = _("Last Name") # Localized
        self.fields['role'].label = _("Role") # Localized
        self.fields['joined_date'].label = _("Joined Date") # Localized
        self.fields['password'].label = _("Password") # Localized

    username = UsernameField(widget=forms.TextInput(
        attrs={ 
               'placeholder': _('Username'), # Localized
               'id': 'username'
        }))
    
    first_name = forms.CharField(widget=forms.TextInput(
            attrs={
                'placeholder': _('First Name'), # Localized
                'id': 'first_name',
            }))
    
    last_name = forms.CharField(widget=forms.TextInput(
            attrs={
                'placeholder': _('Last Name'), # Localized
                'id': 'last_name',
            }), required=False)
    
    role = forms.ModelChoiceField(
        queryset=Role.objects.all(),
        widget=forms.Select(),
        required=False
    )

    joined_date = forms.DateField(
        input_formats=["%d/%m/%Y"],
        widget= forms.DateInput(format="%d/%m/%Y"),
        initial=date, 
        required=False
    )

    password = forms.CharField(widget=forms.PasswordInput(
        attrs={
            'placeholder': _('Password'), # Localized
            'id': 'password',
        }))

    class Meta:
        model = User
        fields = ["first_name", "last_name", "username", "password", "joined_date", "role"]
        exclude = []

    def save(self, commit=True):
        user = super(UserCreationForm, self).save(False)
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class UserUpdateForm(UserCreationForm):
    password = forms.CharField(widget=forms.PasswordInput(
        attrs={
            'placeholder': _('Password'), # Localized
            'id': 'password',
        }), required=False)

    def save(self, commit=True):
        password = self.cleaned_data.get("password")
        if not password:
            print("No password provided, skipping password update.")

            # Temporarily add 'password' to _meta.exclude for this save so Django's
            # ModelForm doesn't write an empty raw string to the password field.
            # Restore the original list afterwards to avoid permanent class mutation.
            original_exclude = list(self._meta.exclude or [])
            self._meta.exclude = original_exclude + ['password']
            self.cleaned_data.pop('password', None)
            print(f"Cleaned data: {self.cleaned_data}")
            print(f"Meta exclude: {self._meta.exclude}")
            try:
                return super(UserUpdateForm, self).save(commit)
            finally:
                self._meta.exclude = original_exclude
        return super(UserUpdateForm, self).save(commit)
    

class ProfileUpdateForm(UserUpdateForm):
    readonly_fields = {"username", "joined_date"}

    def __init__(self, *args, **kwargs):
        super(ProfileUpdateForm, self).__init__(*args, **kwargs)
        self.fields.pop("role", None)
        for fname in self.readonly_fields:
            if fname in self.fields:
                self.fields[fname].disabled = True

    def clean_username(self):
        return self.instance.username

    def clean_joined_date(self):
        return self.instance.joined_date


class CourseForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].label = _("Course Name") # Localized
        self.fields['description'].label = _("Course Description") # Localized
        self.fields['description'].required = False
        self.fields['instructor'].label = _("Instructor") # Localized
        self.fields['instructor'].required = False
        self.fields['level'].label = _("Academic Year") # Localized

    level = forms.ChoiceField(
        choices=Course.LEVELS_NAME,
        widget=forms.Select()
    )

    class Meta:
        model = Course
        fields = "__all__"

class AcademicYearForm(forms.ModelForm):
    class Meta:
        model = AcademicYear
        fields = ["name", "level", "starts_on", "ends_on", "meeting_weekdays", "is_current"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "required": True}),
            "level": forms.Select(choices=Course.LEVELS_NAME, attrs={"class": "form-select", "required": True}),
            "starts_on": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "ends_on": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "meeting_weekdays": forms.CheckboxSelectMultiple(
                choices=[(0, _("Monday")), (1, _("Tuesday")), (2, _("Wednesday")), (3, _("Thursday")), (4, _("Friday")), (5, _("Saturday")), (6, _("Sunday"))]
            ),
            "is_current": forms.Select(choices=[(True, _("Yes")), (False, _("No"))], attrs={"class": "form-select"}),
        }

    def clean_meeting_weekdays(self):
        value = self.cleaned_data.get("meeting_weekdays")
        if value:
            return [int(v) for v in value]
        return value

    def clean(self):
        cleaned = super().clean()
        starts_on = cleaned.get("starts_on")
        ends_on = cleaned.get("ends_on")
        if starts_on and ends_on and starts_on >= ends_on:
            raise forms.ValidationError(_("End date must be after start date."))
        level = cleaned.get("level")
        name = cleaned.get("name")
        if name and level:
            qs = AcademicYear.objects.filter(level=level, name=name)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(_("Academic year with this name and level already exists."))
        return cleaned


class CourseOfferingForm(forms.ModelForm):
    class Meta:
        model = CourseOffering
        fields = ["course", "academic_year", "instructor", "status"]
        widgets = {
            "course": forms.Select(attrs={"class": "form-select"}),
            "academic_year": forms.Select(attrs={"class": "form-select"}),
            "instructor": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "academic_year" in self.data:
            try:
                year_id = int(self.data.get("academic_year"))
                year = AcademicYear.objects.get(pk=year_id)
                self.fields["course"].queryset = Course.objects.filter(level=year.level)
            except (ValueError, TypeError, AcademicYear.DoesNotExist):
                pass
        elif self.instance and self.instance.pk and self.instance.academic_year_id:
            self.fields["course"].queryset = Course.objects.filter(level=self.instance.academic_year.level)


class CSVUploadForm(forms.Form):
    csv_file = forms.FileField(label=_("CSV File"), help_text=_("Upload a .csv file with user data."))


class SignupForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': _('Password'), 'id': 'password'}))
    agree_terms = forms.BooleanField(required=True, label=_("I agree to the Terms and Conditions"))
    identity_front = forms.FileField(required=False, label=_("National ID / Passport (Front)"))
    identity_back = forms.FileField(required=False, label=_("National ID / Passport (Back)"))
    payment = forms.FileField(required=False, label=_("Payment Receipt"))
    profile = forms.FileField(required=False, label=_("Profile Photo"))

    class Meta:
        model = User
        fields = ["username", "password", "first_name", "last_name", "email", "phone", "priest_name", "priest_phone", "church", "city", "identity_type", "identity_number"]
        widgets = {
            'username': forms.TextInput(attrs={'placeholder': _('Username'), 'id': 'username'}),
            'first_name': forms.TextInput(attrs={'placeholder': _('First Name'), 'id': 'first_name'}),
            'last_name': forms.TextInput(attrs={'placeholder': _('Last Name'), 'id': 'last_name'}),
            'email': forms.EmailInput(attrs={'placeholder': _('Email'), 'id': 'email'}),
            'phone': forms.TextInput(attrs={'placeholder': _('Phone'), 'id': 'phone'}),
            'priest_name': forms.TextInput(attrs={'placeholder': _('Priest Name'), 'id': 'priest_name'}),
            'priest_phone': forms.TextInput(attrs={'placeholder': _('Priest Phone'), 'id': 'priest_phone'}),
            'church': forms.TextInput(attrs={'placeholder': _('Church'), 'id': 'church'}),
            'city': forms.TextInput(attrs={'placeholder': _('City'), 'id': 'city'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = False
        self.fields['last_name'].required = False
        self.fields['first_name'].label = _("Name")
        self.fields['last_name'].label = _("Last Name")
        self.fields['email'].label = _("Email")
        self.fields['phone'].label = _("Phone")
        self.fields['phone'].required = True
        self.fields['priest_name'].label = _("Priest Name")
        self.fields['priest_name'].required = True
        self.fields['priest_phone'].label = _("Priest Phone")
        self.fields['priest_phone'].required = True
        self.fields['church'].label = _("Church")
        self.fields['church'].required = True
        self.fields['city'].label = _("City")
        self.fields['city'].required = True
        self.fields['identity_type'].label = _("Identity Type")
        self.fields['identity_type'].required = True
        self.fields['identity_number'].label = _("Identity Number")
        self.fields['identity_number'].required = True

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        if not phone:
            return phone
        phone = normalize_phone(phone)
        qs = User.objects.filter(phone=phone)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(_("This phone number is already in use."))
        return phone

    def clean_identity_number(self):
        identity_number = self.cleaned_data.get("identity_number")
        identity_type = self.cleaned_data.get("identity_type")
        if identity_type and identity_number:
            try:
                validate_identity_by_type(identity_type, identity_number)
            except ValidationError as e:
                raise forms.ValidationError(e.message if hasattr(e, 'message') else str(e))
        return identity_number

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("agree_terms"):
            raise forms.ValidationError(_("You must agree to the Terms and Conditions."))
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        user.application_status = "pending"
        user.is_active = False
        user.qr_token = secrets.token_urlsafe(32)
        if commit:
            user.save()
        return user
