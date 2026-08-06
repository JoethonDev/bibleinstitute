from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django import forms
import secrets

from django.core.exceptions import ValidationError
from .models import User, Role, Course, Lesson, AcademicYear, AcademicYearLevel, CourseOffering, Level, QuizType, PromotionRule, QUIZ_TYPE_CODES, assign_academic_date
from .utils.validators import normalize_phone, validate_identity_by_type
from django.utils.translation import gettext_lazy as _ # Import gettext_lazy
from django.utils import timezone
from django.conf import settings
from .utils.timezones import user_time_zone_choices
from .utils.countries import country_choices
from .utils.timezones import parse_application_datetime
from .utils.quiz_access import eligible_quiz_students


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
        self.fields['time_zone'].label = _("Time zone")
        self.fields['time_zone'].choices = user_time_zone_choices()

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
        fields = ["first_name", "last_name", "username", "password", "joined_date", "role", "time_zone"]
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_password = self.instance.password

    def save(self, commit=True):
        password = self.cleaned_data.get("password")
        if not password:
            self.instance.password = self._original_password
        return super(UserUpdateForm, self).save(commit)
    

class ProfileUpdateForm(UserUpdateForm):
    readonly_fields = {"username", "joined_date"}

    def __init__(self, *args, **kwargs):
        super(ProfileUpdateForm, self).__init__(*args, **kwargs)
        self.fields.pop("role", None)
        self.fields.pop("time_zone", None)
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
        self.fields['name'].label = _("Course Name")
        self.fields['description'].label = _("Course Description")
        self.fields['description'].required = False
        self.fields['instructor'].label = _("Instructor")
        self.fields['instructor'].required = False
        self.fields['level'].label = _("Level")
        self.fields['level'].queryset = Level.objects.order_by("ordering")

    class Meta:
        model = Course
        fields = "__all__"

class AcademicYearForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["levels"].queryset = Level.objects.order_by("ordering")
        self.fields["levels"].required = True

    class Meta:
        model = AcademicYear
        fields = ["name", "levels", "starts_on", "ends_on", "ordering"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "required": True}),
            "levels": forms.SelectMultiple(attrs={"class": "form-select", "required": True, "size": 5}),
            "starts_on": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "ends_on": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "ordering": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }

    def clean(self):
        cleaned = super().clean()
        starts_on = cleaned.get("starts_on")
        ends_on = cleaned.get("ends_on")
        if starts_on and ends_on and starts_on >= ends_on:
            raise forms.ValidationError(_("End date must be after start date."))
        return cleaned


class CourseOfferingForm(forms.ModelForm):
    class Meta:
        model = CourseOffering
        fields = ["course", "academic_year_level", "instructor", "status"]
        widgets = {
            "course": forms.Select(attrs={"class": "form-select"}),
            "academic_year_level": forms.Select(attrs={"class": "form-select"}),
            "instructor": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "academic_year_level" in self.data:
            try:
                scope_id = int(self.data.get("academic_year_level"))
                scope = AcademicYearLevel.objects.select_related("level").get(pk=scope_id)
                self.fields["course"].queryset = Course.objects.filter(level=scope.level)
            except (ValueError, TypeError, AcademicYearLevel.DoesNotExist):
                pass
        elif self.instance and self.instance.pk and self.instance.academic_year_level_id:
                self.fields["course"].queryset = Course.objects.filter(level=self.instance.academic_year_level.level)


class AcademicYearLevelWeekdayForm(forms.ModelForm):
    meeting_weekdays = forms.MultipleChoiceField(
        choices=[
            (0, _("Monday")),
            (1, _("Tuesday")),
            (2, _("Wednesday")),
            (3, _("Thursday")),
            (4, _("Friday")),
            (5, _("Saturday")),
            (6, _("Sunday")),
        ],
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 7}),
        required=False,
    )

    class Meta:
        model = AcademicYearLevel
        fields = ["meeting_weekdays"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["meeting_weekdays"] = [str(day) for day in (self.instance.meeting_weekdays or [])]

    def clean_meeting_weekdays(self):
        return sorted({int(day) for day in self.cleaned_data["meeting_weekdays"]})


class OfferingCopyForm(forms.Form):
    source_year_level = forms.ModelChoiceField(queryset=AcademicYearLevel.objects.none())
    target_year_level = forms.ModelChoiceField(queryset=AcademicYearLevel.objects.none())
    offerings = forms.ModelMultipleChoiceField(
        queryset=CourseOffering.objects.none(),
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
        required=True,
    )
    copy_lessons = forms.BooleanField(required=False, initial=True)
    copy_quizzes = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_year_level"].label = _("Source Academic Scope")
        self.fields["target_year_level"].label = _("Target Academic Scope")
        self.fields["source_year_level"].widget.attrs.update({"class": "form-select"})
        self.fields["target_year_level"].widget.attrs.update({"class": "form-select"})
        self.fields["copy_lessons"].widget.attrs.update({"class": "form-check-input"})
        self.fields["copy_quizzes"].widget.attrs.update({"class": "form-check-input"})
        source_scopes = AcademicYearLevel.objects.filter(
            academic_year__is_active=False,
        ).select_related("academic_year", "level").order_by(
            "academic_year__ordering", "level__ordering"
        )
        target_scopes = AcademicYearLevel.objects.filter(
            academic_year__is_active=True,
        ).select_related("academic_year", "level").order_by(
            "academic_year__ordering", "level__ordering"
        )
        self.fields["source_year_level"].queryset = source_scopes
        self.fields["target_year_level"].queryset = target_scopes
        source_id = self.data.get("source_year_level") if self.is_bound else None
        if source_id:
            try:
                self.fields["offerings"].queryset = CourseOffering.objects.filter(
                    academic_year_level_id=int(source_id)
                ).select_related("course").order_by("course__name")
            except (TypeError, ValueError):
                pass

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source_year_level")
        target = cleaned.get("target_year_level")
        if source and target and source.pk == target.pk:
            raise forms.ValidationError(_("Source and target academic scopes must be different."))
        if source and source.academic_year.is_active:
            self.add_error("source_year_level", _("The source scope must belong to an inactive academic year."))
        if target and not target.academic_year.is_active:
            self.add_error("target_year_level", _("The target scope must belong to the active academic year."))
        if source and target and source.level_id != target.level_id:
            raise forms.ValidationError(_("Source and target levels must match."))
        if not cleaned.get("copy_lessons") and not cleaned.get("copy_quizzes"):
            raise forms.ValidationError(_("Select lessons, quizzes, or both to copy."))
        return cleaned


class QuizTypeForm(forms.ModelForm):
    class Meta:
        model = QuizType
        fields = ["code", "name_en", "name_ar"]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control"}),
            "name_en": forms.TextInput(attrs={"class": "form-control"}),
            "name_ar": forms.TextInput(attrs={"class": "form-control", "dir": "rtl"}),
        }


class PromotionFormulaForm(forms.Form):
    academic_year_level = forms.ModelChoiceField(
        queryset=AcademicYearLevel.objects.filter(academic_year__is_active=True).select_related("academic_year", "level").order_by(
            "academic_year__ordering", "level__ordering"
        ),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    course_offering = forms.ModelChoiceField(
        queryset=CourseOffering.objects.filter(academic_year_level__academic_year__is_active=True)
        .select_related("course", "academic_year_level__level")
        .order_by("course__name"),
        required=False,
        empty_label=_("All courses"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    overall_pass_percent = forms.DecimalField(
        min_value=0, max_value=100, max_digits=5, decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 100, "step": "0.01"}),
    )
    evaluation_starts_on = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    evaluation_ends_on = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    failed_courses_repeat_threshold = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_id = self.data.get("academic_year_level") or self.initial.get("academic_year_level")
        if scope_id:
            self.fields["course_offering"].queryset = self.fields["course_offering"].queryset.filter(
                academic_year_level_id=scope_id
            )
        self.fields["course_offering"].label_from_instance = lambda offering: offering.course.name

    def clean(self):
        cleaned = super().clean()
        scope = cleaned.get("academic_year_level")
        offering = cleaned.get("course_offering")
        starts_on = cleaned.get("evaluation_starts_on")
        ends_on = cleaned.get("evaluation_ends_on")
        today = timezone.localdate()
        if scope and offering and offering.academic_year_level_id != scope.pk:
            self.add_error("course_offering", _("Course offering must belong to the selected academic scope."))
        if scope and starts_on and ends_on:
            year = scope.academic_year
            if starts_on > ends_on:
                self.add_error("evaluation_ends_on", _("Evaluation end date must not be before the start date."))
            if not year.starts_on <= starts_on <= year.ends_on:
                self.add_error("evaluation_starts_on", _("Evaluation start date must be inside the academic year."))
            if not year.starts_on <= ends_on <= year.ends_on:
                self.add_error("evaluation_ends_on", _("Evaluation end date must be inside the academic year."))
            if ends_on > min(today, year.ends_on):
                self.add_error("evaluation_ends_on", _("Evaluation end date cannot be in the future."))
        return cleaned


class PromotionRuleForm(forms.Form):
    metric = forms.ChoiceField(
        choices=PromotionRule.Metric.choices,
        widget=forms.Select(attrs={"class": "form-select rule-metric"}),
    )
    quiz_type = forms.ModelChoiceField(
        queryset=QuizType.objects.filter(code__in=QUIZ_TYPE_CODES).order_by("code"),
        required=False,
        empty_label=_("All quiz types"),
        widget=forms.Select(attrs={"class": "form-select rule-quiz-type"}),
    )
    weight_percent = forms.DecimalField(
        required=False, min_value=0, max_value=100, max_digits=5, decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 100, "step": "0.01"}),
    )
    minimum_percent = forms.DecimalField(
        required=False, min_value=0, max_value=100, max_digits=5, decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 100, "step": "0.01"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quiz_type"].label_from_instance = lambda quiz_type: (
            f"{quiz_type.name_en} — {quiz_type.name_ar}" if quiz_type.name_ar else quiz_type.name_en
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("metric") == PromotionRule.Metric.ATTENDANCE and cleaned.get("quiz_type"):
            self.add_error("quiz_type", _("Attendance rules cannot select a quiz type."))
        return cleaned

class CSVUploadForm(forms.Form):
    csv_file = forms.FileField(label=_("CSV File"), help_text=_("Upload a .csv file with user data."))


class QuizExceptionalOpeningForm(forms.Form):
    students = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        label=_("Students"),
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
    )
    opening_date = forms.CharField(
        label=_("Exceptional opening date"),
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )
    closing_date = forms.CharField(
        label=_("Exceptional closing date"),
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )

    def __init__(self, *args, quiz, **kwargs):
        self.quiz = quiz
        super().__init__(*args, **kwargs)
        self.fields["students"].queryset = eligible_quiz_students(quiz)

    def _parse_datetime(self, value):
        try:
            return parse_application_datetime(value)
        except (TypeError, ValueError):
            raise forms.ValidationError(_("Enter a valid date and time."))

    def clean_opening_date(self):
        return self._parse_datetime(self.cleaned_data["opening_date"])

    def clean_closing_date(self):
        return self._parse_datetime(self.cleaned_data["closing_date"])

    def clean(self):
        cleaned = super().clean()
        opening_date = cleaned.get("opening_date")
        closing_date = cleaned.get("closing_date")
        if opening_date and closing_date and closing_date <= opening_date:
            self.add_error("closing_date", _("Closing date must be after opening date."))
        return cleaned


class ApplicationAdminForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        label=_("Password (leave blank to keep current)"),
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    time_zone = forms.ChoiceField(label=_("Time zone"), choices=(), required=True)
    identity_front = forms.FileField(required=False, label=_("Replace identity front"))
    identity_back = forms.FileField(required=False, label=_("Replace identity back"))
    payment = forms.FileField(required=False, label=_("Replace payment receipt"))
    profile = forms.FileField(required=False, label=_("Replace profile photo"))
    clear_identity_front = forms.BooleanField(required=False, label=_("Remove identity front"))
    clear_identity_back = forms.BooleanField(required=False, label=_("Remove identity back"))
    clear_payment = forms.BooleanField(required=False, label=_("Remove payment receipt"))
    clear_profile = forms.BooleanField(required=False, label=_("Remove profile photo"))
    country = forms.ChoiceField(label=_("Country"), choices=country_choices(), required=True)

    class Meta:
        model = User
        fields = [
            "first_name", "last_name", "username", "email", "password", "joined_date", "phone", "country", "city",
            "education_or_job", "priest_name", "priest_phone", "church", "service", "identity_type",
            "identity_number", "time_zone", "study_mode", "study_mode_override", "role", "is_active",
            "application_status", "decision_notes",
            "identity_front", "identity_back", "payment", "profile",
            "clear_identity_front", "clear_identity_back", "clear_payment", "clear_profile",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": _("First Name")}),
            "last_name": forms.TextInput(attrs={"placeholder": _("Last Name")}),
            "username": forms.TextInput(attrs={"placeholder": _("Username")}),
            "email": forms.EmailInput(attrs={"placeholder": _("Email")}),
            "phone": forms.TextInput(attrs={"placeholder": _("Phone")}),
            "city": forms.TextInput(attrs={"placeholder": _("City")}),
            "education_or_job": forms.TextInput(attrs={"placeholder": _("Educational qualification / occupation")}),
            "priest_name": forms.TextInput(attrs={"placeholder": _("Confessor Name")}),
            "priest_phone": forms.TextInput(attrs={"placeholder": _("Confessor Phone")}),
            "church": forms.TextInput(attrs={"placeholder": _("Church")}),
            "service": forms.TextInput(attrs={"placeholder": _("Service (if any)")}),
            "identity_number": forms.TextInput(attrs={"placeholder": _("Identity Number")}),
            "decision_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["time_zone"].choices = user_time_zone_choices()
        self.fields["time_zone"].initial = getattr(self.instance, "time_zone", None) or settings.TIME_ZONE
        current_country = getattr(self.instance, "country", None)
        if current_country and current_country not in dict(country_choices()):
            self.fields["country"].choices = [(current_country, current_country)] + list(self.fields["country"].choices)
        self.fields["country"].widget.attrs.update({
            "id": "id_country",
            "data-country-select": "true",
            "data-search-placeholder": _("Search countries"),
            "aria-label": _("Search countries"),
        })

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        if not phone:
            return phone
        phone = normalize_phone(phone)
        if User.objects.filter(phone=phone).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("This phone number is already in use."))
        return phone

    def clean_identity_number(self):
        identity_number = self.cleaned_data.get("identity_number")
        identity_type = self.cleaned_data.get("identity_type")
        if identity_type and identity_number:
            try:
                validate_identity_by_type(identity_type, identity_number)
            except ValidationError as exc:
                raise forms.ValidationError(exc.message if hasattr(exc, "message") else str(exc))
        return identity_number

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user


class SignupForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': _('Password'), 'id': 'password'}))
    full_name = forms.CharField(
        label=_("Full Name"),
        max_length=255,
        widget=forms.TextInput(attrs={'placeholder': _('Full Name'), 'id': 'full_name'}),
    )
    agree_terms = forms.BooleanField(required=True, label=_("I agree to the Terms and Conditions"))
    identity_front = forms.FileField(required=True, label=_("National ID / Passport (Front)"))
    identity_back = forms.FileField(required=False, label=_("National ID / Passport (Back)"))
    payment = forms.FileField(required=True, label=_("Payment Receipt"))
    profile = forms.FileField(required=True, label=_("Profile Photo"))
    time_zone = forms.ChoiceField(label=_("Time zone"), choices=(), required=True)
    country = forms.ChoiceField(
        label=_("Country"),
        choices=country_choices(),
        required=True,
        widget=forms.Select(attrs={"id": "id_country", "data-country-select": "true"}),
    )

    class Meta:
        model = User
        fields = ["username", "password", "email", "phone", "priest_name", "priest_phone", "church", "city", "country", "education_or_job", "service", "identity_type", "identity_number", "time_zone"]
        widgets = {
            'username': forms.TextInput(attrs={'placeholder': _('Username'), 'id': 'username'}),
            'email': forms.EmailInput(attrs={'placeholder': _('Email'), 'id': 'email'}),
            'phone': forms.TextInput(attrs={'placeholder': _('Phone'), 'id': 'phone'}),
            'priest_name': forms.TextInput(attrs={'placeholder': _('Confessor Name'), 'id': 'priest_name'}),
            'priest_phone': forms.TextInput(attrs={'placeholder': _('Confessor Phone'), 'id': 'priest_phone'}),
            'church': forms.TextInput(attrs={'placeholder': _('Church'), 'id': 'church'}),
            'city': forms.TextInput(attrs={'placeholder': _('City'), 'id': 'city'}),
            'education_or_job': forms.TextInput(attrs={'placeholder': _('Educational qualification / occupation'), 'id': 'education_or_job'}),
            'service': forms.TextInput(attrs={'placeholder': _('Service (if any)'), 'id': 'service'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['time_zone'].choices = user_time_zone_choices()
        self.fields['time_zone'].initial = settings.TIME_ZONE
        self.fields['email'].required = False
        self.fields['full_name'].required = True
        self.fields['email'].label = _("Email")
        self.fields['phone'].label = _("Phone")
        self.fields['phone'].required = True
        self.fields['priest_name'].label = _("Confessor Name")
        self.fields['priest_name'].required = True
        self.fields['priest_phone'].label = _("Confessor Phone")
        self.fields['priest_phone'].required = True
        self.fields['church'].label = _("Church")
        self.fields['church'].required = True
        self.fields['city'].label = _("City")
        self.fields['city'].required = True
        self.fields['country'].label = _("Country")
        self.fields['country'].required = True
        self.fields['country'].initial = getattr(self.instance, "country", None) or "EG"
        self.fields['country'].widget.attrs.update({
            "data-search-placeholder": _("Search countries"),
            "aria-label": _("Search countries"),
        })
        self.fields['education_or_job'].label = _("Educational qualification / occupation")
        self.fields['education_or_job'].required = True
        self.fields['service'].label = _("Service (if any)")
        self.fields['service'].required = False
        self.fields['identity_type'].label = _("Identity Type")
        self.fields['identity_type'].required = True
        self.fields['identity_number'].label = _("Identity Number")
        self.fields['identity_number'].required = True
        self.order_fields([
            "full_name", "username", "password", "email", "phone", "country", "city",
            "education_or_job", "priest_name", "priest_phone", "church", "service",
            "identity_type", "identity_number", "time_zone", "identity_front", "identity_back",
            "payment", "profile", "agree_terms",
        ])

    def clean_full_name(self):
        full_name = " ".join((self.cleaned_data.get("full_name") or "").split())
        if len(full_name.split()) < 2:
            raise forms.ValidationError(_("Enter your full name."))
        return full_name

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
        if cleaned_data.get("identity_type") == "national_id" and not cleaned_data.get("identity_back"):
            self.add_error("identity_back", _("The back of the National ID is required."))
        if cleaned_data.get("identity_type") == "passport" and cleaned_data.get("identity_back"):
            self.add_error("identity_back", _("A passport requires only the front document."))
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        name_parts = self.cleaned_data["full_name"].split()
        user.first_name = name_parts[0]
        user.last_name = " ".join(name_parts[1:])
        user.set_password(self.cleaned_data["password"])
        user.application_status = "pending"
        user.is_active = False
        user.qr_token = secrets.token_urlsafe(32)
        if commit:
            user.save()
        return user
