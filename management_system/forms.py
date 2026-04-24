from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django import forms
from .models import User, Role, Course, Lesson, assign_academic_date
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
            # Temporarily add 'password' to _meta.exclude for this save so Django's
            # ModelForm doesn't write an empty raw string to the password field.
            # Restore the original list afterwards to avoid permanent class mutation.
            original_exclude = list(self._meta.exclude or [])
            self._meta.exclude = original_exclude + ['password']
            self.cleaned_data.pop('password', None)
            try:
                return super(UserUpdateForm, self).save(commit)
            finally:
                self._meta.exclude = original_exclude
        return super(UserUpdateForm, self).save(commit)
    

class ProfileUpdateForm(UserUpdateForm):
    def __init__(self, *args, **kwargs):
        super(ProfileUpdateForm, self).__init__(*args, **kwargs)
        self.fields.pop("role", None)
        self.fields['joined_date'].widget.attrs['readonly'] = True
        self.fields['username'].widget.attrs['readonly'] = True


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

class CSVUploadForm(forms.Form):
    csv_file = forms.FileField(label=_("CSV File"), help_text=_("Upload a .csv file with user data."))
