from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django import forms
from .models import User, Role, Course, Lesson, assign_academic_date


class UserLoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super(UserLoginForm, self).__init__(*args, **kwargs)
        self.fields['username'].label = "اسم المستخدم"
        self.fields['password'].label = " كلمه المرور"

    username = UsernameField(widget=forms.TextInput(
        attrs={ 
               'placeholder': 'اسم المستخدم', 
               'id': 'username'
        }))
    password = forms.CharField(widget=forms.PasswordInput(
        attrs={
            'placeholder': 'كلمه المرور',
            'id': 'password',
        }
))
    
class UserCreationForm(forms.ModelForm):
    date = assign_academic_date()
    
    def __init__(self, *args, **kwargs):
        super(UserCreationForm, self).__init__(*args, **kwargs)
        self.fields['username'].label = "اسم المستخدم"
        self.fields['first_name'].label = "اسم الاول"
        self.fields['last_name'].label = "اسم الاخير"
        self.fields['role'].label = "الحاله"
        self.fields['joined_date'].label = "تاريخ الانضمام"
        self.fields['password'].label = " كلمه المرور"

    username = UsernameField(widget=forms.TextInput(
        attrs={ 
               'placeholder': 'اسم المستخدم', 
               'id': 'username'
        }))
    
    first_name = forms.CharField(widget=forms.TextInput(
            attrs={
                'placeholder': 'الاسم الاول',
                'id': 'first_name',
            }))
    
    last_name = forms.CharField(widget=forms.TextInput(
            attrs={
                'placeholder': 'الاسم الاخير',
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
            'placeholder': 'كلمه المرور',
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
            'placeholder': 'كلمه المرور',
            'id': 'password',
        }), required=False)

    def save(self, commit=True):
        password = self.cleaned_data.get("password")
        if not password:
            # print("Password will not be updated!")
            self._meta.exclude.append("password")
            del self.cleaned_data['password']
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
        self.fields['name'].label = "اسم الكورس"
        self.fields['description'].label = "وصف الكورس"
        self.fields['description'].required = False
        self.fields['instructor'].label = "المحاضر"
        self.fields['instructor'].required = False
        self.fields['level'].label = "السنه الدراسيه"

    level = forms.ChoiceField(
        choices=Course.LEVELS_NAME,
        widget=forms.Select()
    )

    class Meta:
        model = Course
        fields = "__all__"

