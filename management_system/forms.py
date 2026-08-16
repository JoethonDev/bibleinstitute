from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django import forms
import secrets

from django.core.exceptions import ValidationError
from .models import User, Role, Course, Lesson, AcademicYear, AcademicYearLevel, CourseOffering, Enrollment, Level, QuizType, PromotionRule, HistoricalAcademicSummary, QUIZ_TYPE_CODES, assign_academic_date
from .utils.validators import normalize_phone, validate_identity_by_type
from .utils.application_uploads import validate_application_file
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
    admin_only_fields = frozenset({
        "role", "time_zone", "application_status", "decision_notes",
        "study_mode", "study_mode_override", "identity_type", "identity_number",
        "enrollment_scope",
    })

    password = forms.CharField(widget=forms.PasswordInput(
        attrs={
            'placeholder': _('Password'), # Localized
            'id': 'password',
        }), required=False)

    email = forms.EmailField(
        max_length=254,
        required=False,
        label=_("Email"),
        widget=forms.EmailInput(attrs={"placeholder": _("Email")}),
    )
    phone = forms.CharField(
        max_length=20,
        required=False,
        label=_("Phone"),
        widget=forms.TextInput(attrs={"placeholder": _("Phone")}),
    )
    country = forms.ChoiceField(
        label=_("Country"),
        choices=country_choices(),
        required=False,
    )
    city = forms.CharField(
        max_length=255,
        required=False,
        label=_("City"),
        widget=forms.TextInput(attrs={"placeholder": _("City")}),
    )
    education_or_job = forms.CharField(
        max_length=255,
        required=False,
        label=_("Educational qualification / occupation"),
        widget=forms.TextInput(attrs={"placeholder": _("Educational qualification / occupation")}),
    )
    priest_name = forms.CharField(
        max_length=255,
        required=False,
        label=_("Confessor Name"),
        widget=forms.TextInput(attrs={"placeholder": _("Confessor Name")}),
    )
    priest_phone = forms.CharField(
        max_length=20,
        required=False,
        label=_("Confessor Phone"),
        widget=forms.TextInput(attrs={"placeholder": _("Confessor Phone")}),
    )
    church = forms.CharField(
        max_length=255,
        required=False,
        label=_("Church"),
        widget=forms.TextInput(attrs={"placeholder": _("Church")}),
    )
    service = forms.CharField(
        max_length=255,
        required=False,
        label=_("Service (if any)"),
        widget=forms.TextInput(attrs={"placeholder": _("Service (if any)")}),
    )
    identity_type = forms.ChoiceField(
        label=_("Identity type"),
        choices=User.IDENTITY_TYPES,
        required=False,
    )
    identity_number = forms.CharField(
        max_length=50,
        required=False,
        label=_("Identity Number"),
        widget=forms.TextInput(attrs={"placeholder": _("Identity Number")}),
    )
    study_mode = forms.ChoiceField(
        label=_("Study mode"),
        choices=User.STUDY_MODES,
        required=False,
    )
    study_mode_override = forms.BooleanField(
        required=False,
        label=_("Study mode override"),
    )
    application_status = forms.ChoiceField(
        label=_("Application status"),
        choices=User.APPLICATION_STATUSES,
        required=False,
    )
    decision_notes = forms.CharField(
        required=False,
        label=_("Decision notes"),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    enrollment_scope = forms.ModelChoiceField(
        queryset=AcademicYearLevel.objects.none(),
        required=False,
        label=_("Academic year and level"),
    )

    class Meta:
        model = User
        fields = [
            "first_name", "last_name", "username", "password", "joined_date", "role", "time_zone",
            "email", "phone", "country", "city", "education_or_job", "priest_name", "priest_phone",
            "church", "service", "identity_type", "identity_number", "study_mode",
            "study_mode_override", "application_status", "decision_notes", "enrollment_scope",
        ]
        exclude = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_password = self.instance.password
        current_country = getattr(self.instance, "country", None)
        if current_country and current_country not in dict(country_choices()):
            self.fields["country"].choices = [(current_country, current_country)] + list(self.fields["country"].choices)
        self.fields["enrollment_scope"].queryset = AcademicYearLevel.objects.filter(
            academic_year__is_active=True,
        ).select_related("academic_year", "level").order_by(
            "level__ordering", "academic_year__ordering",
        )
        active_enrollment = Enrollment.objects.filter(
            student=self.instance,
            academic_year_level__academic_year__is_active=True,
            enrollment_type=Enrollment.Type.NORMAL,
            course_offering__isnull=True,
            status=Enrollment.Status.ACTIVE,
        ).select_related("academic_year_level").first()
        if active_enrollment:
            self.fields["enrollment_scope"].initial = active_enrollment.academic_year_level_id

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        if not phone:
            return None
        phone = normalize_phone(phone)
        if not phone:
            raise forms.ValidationError(_("Enter a valid international phone number."))
        if User.objects.filter(phone=phone).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("This phone number is already in use."))
        return phone

    def clean_identity_number(self):
        identity_number = self.cleaned_data.get("identity_number")
        identity_type = self.cleaned_data.get("identity_type")
        if not identity_number:
            return None
        if identity_type and identity_number:
            try:
                validate_identity_by_type(identity_type, identity_number)
            except ValidationError as exc:
                raise forms.ValidationError(exc.message if hasattr(exc, "message") else str(exc))
        return identity_number

    def save(self, commit=True):
        password = self.cleaned_data.get("password")
        if not password:
            self.instance.password = self._original_password
        return super(UserUpdateForm, self).save(commit)


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


class HistoricalIntakeForm(forms.Form):
    source_year_level = forms.ModelChoiceField(
        queryset=AcademicYearLevel.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        label=_("Historical academic scope"),
    )
    source_name = forms.CharField(widget=forms.TextInput(attrs={"class": "form-control"}), label=_("Source student name"))
    account_action = forms.ChoiceField(
        choices=(("find", _("Find existing account")), ("create", _("Create new account"))),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    lms_user_id = forms.IntegerField(required=False, min_value=1, widget=forms.NumberInput(attrs={"class": "form-control"}))
    lms_username = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    lms_email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={"class": "form-control"}))
    username = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control"}), label=_("New username"))
    first_name = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    last_name = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    historical_outcome = forms.ChoiceField(
        choices=HistoricalAcademicSummary.Outcome.choices,
        initial=HistoricalAcademicSummary.Outcome.PENDING_REVIEW,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    destination_scope = forms.ModelChoiceField(
        queryset=AcademicYearLevel.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label=_("Destination academic scope"),
    )
    promote_now = forms.BooleanField(required=False, label=_("Promote after review"))
    exceptional_offering_ids = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "12, 15"}),
        label=_("Failed course offering IDs"),
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}))
    promotion_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}), label=_("Promotion reason"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_year_level"].queryset = AcademicYearLevel.objects.filter(academic_year__is_active=False).select_related("academic_year", "level").order_by("academic_year__ordering", "level__ordering")
        self.fields["destination_scope"].queryset = AcademicYearLevel.objects.filter(academic_year__is_active=True).select_related("academic_year", "level").order_by("level__ordering")

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get("account_action")
        if action == "find" and not any(cleaned.get(field) for field in ("lms_user_id", "lms_username", "lms_email")):
            self.add_error("lms_username", _("Find mode requires an LMS user ID, username, or email."))
        if action == "create" and not cleaned.get("username"):
            self.add_error("username", _("Create mode requires a new username."))
        if cleaned.get("promote_now") and cleaned.get("historical_outcome") == HistoricalAcademicSummary.Outcome.PENDING_REVIEW:
            self.add_error("historical_outcome", _("Review the historical outcome before promotion."))
        if cleaned.get("promote_now") and not cleaned.get("promotion_reason", "").strip():
            self.add_error("promotion_reason", _("A manual historical promotion requires a reason."))
        return cleaned


class ExceptionalCourseAssignmentForm(forms.Form):
    student_identifier = forms.CharField(
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": _("Username or numeric student ID")}),
        label=_("Student"),
    )
    course_offerings = forms.ModelMultipleChoiceField(
        queryset=CourseOffering.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label=_("Failed course offerings"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course_offerings"].queryset = CourseOffering.objects.filter(
            academic_year_level__academic_year__is_active=True,
            status="published",
        ).select_related("course", "academic_year_level__level").order_by("academic_year_level__level__ordering", "course__name")

    def clean_student_identifier(self):
        value = self.cleaned_data["student_identifier"].strip()
        queryset = User.objects.select_related("role")
        if value.isdigit():
            student = queryset.filter(pk=int(value)).first()
        else:
            student = queryset.filter(username=value).first()
        if student is None or not student.role or student.role.role != "student":
            raise forms.ValidationError(_("Select an existing student account."))
        return student


class HistoricalBulkIntakeForm(forms.Form):
    source_year_level = forms.ModelChoiceField(
        queryset=AcademicYearLevel.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        label=_("Historical academic scope"),
    )
    student_identifiers = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": _("Usernames or numeric IDs, separated by commas or lines")}),
        label=_("Existing students"),
    )
    historical_outcome = forms.ChoiceField(
        choices=HistoricalAcademicSummary.Outcome.choices,
        initial=HistoricalAcademicSummary.Outcome.PENDING_REVIEW,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    destination_scope = forms.ModelChoiceField(
        queryset=AcademicYearLevel.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label=_("Destination academic scope"),
    )
    promote_now = forms.BooleanField(required=False, label=_("Promote after review"))
    exceptional_offering_ids = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "12, 15"}),
        label=_("Failed course offering IDs"),
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}))
    promotion_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}), label=_("Promotion reason"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_year_level"].queryset = AcademicYearLevel.objects.filter(academic_year__is_active=False).select_related("academic_year", "level").order_by("academic_year__ordering", "level__ordering")
        self.fields["destination_scope"].queryset = AcademicYearLevel.objects.filter(academic_year__is_active=True).select_related("academic_year", "level").order_by("level__ordering")

    def clean_student_identifiers(self):
        values = [value.strip() for value in self.cleaned_data["student_identifiers"].replace(",", "\n").splitlines() if value.strip()]
        values = list(dict.fromkeys(values))
        if not values or len(values) > 1000:
            raise forms.ValidationError(_("Select between one and 1,000 students."))
        return values

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("promote_now") and cleaned.get("historical_outcome") == HistoricalAcademicSummary.Outcome.PENDING_REVIEW:
            self.add_error("historical_outcome", _("Review the historical outcome before promotion."))
        if cleaned.get("promote_now") and not cleaned.get("promotion_reason", "").strip():
            self.add_error("promotion_reason", _("A manual historical promotion requires a reason."))
        return cleaned


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
    time_zone = forms.ChoiceField(label=_("Time zone"), choices=(), required=False)
    identity_front = forms.FileField(required=False, label=_("Replace identity front"))
    identity_back = forms.FileField(required=False, label=_("Replace identity back"))
    payment = forms.FileField(required=False, label=_("Replace payment receipt (optional)"))
    profile = forms.FileField(required=False, label=_("Replace profile photo"))
    clear_identity_front = forms.BooleanField(required=False, label=_("Remove identity front"))
    clear_identity_back = forms.BooleanField(required=False, label=_("Remove identity back"))
    clear_payment = forms.BooleanField(required=False, label=_("Remove payment receipt"))
    clear_profile = forms.BooleanField(required=False, label=_("Remove profile photo"))
    country = forms.ChoiceField(label=_("Country"), choices=country_choices(), required=False)

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
            return None
        phone = normalize_phone(phone)
        if not phone:
            raise forms.ValidationError(_("Enter a valid international phone number."))
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
        return identity_number or None

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
    payment = forms.FileField(required=False, label=_("Payment Receipt (optional)"))
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
        if not phone:
            raise forms.ValidationError(_("Enter a valid international phone number."))
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


class MissingApplicationDocumentsForm(forms.Form):
    """Self-service upload for signup documents that are still missing.

    The form only describes and validates the file inputs; the caller enforces
    self-only authorization, CSRF, transactions, and exact allowed-field checks.
    Existing documents are never replaceable here and payment receipt upload is
    intentionally never offered. File-content validation reuses the same
    boundary used by signup application uploads.
    """

    identity_front = forms.FileField(
        required=False,
        label=_("National ID / Passport (Front)"),
        validators=[validate_application_file],
    )
    identity_back = forms.FileField(
        required=False,
        label=_("National ID / Passport (Back)"),
        validators=[validate_application_file],
    )
    profile = forms.FileField(
        required=False,
        label=_("Profile Photo"),
        validators=[validate_application_file],
    )

    def __init__(self, *args, **kwargs):
        instance = kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)
        self.instance = instance
        # Maps each offered field name to its User document-key field. It
        # contains only the currently missing, eligible documents and mirrors
        # the remaining fields so the view never duplicates the rule.
        self.document_types = self._missing_document_types(instance)
        for field_name in list(self.fields):
            if field_name not in self.document_types:
                self.fields.pop(field_name)

    @staticmethod
    def _missing_document_types(instance):
        if instance is None:
            return {}
        mapping = {}
        if not getattr(instance, "identity_front_key", None):
            mapping["identity_front"] = "identity_front_key"
        if (
            getattr(instance, "identity_type", None) == "national_id"
            and not getattr(instance, "identity_back_key", None)
        ):
            mapping["identity_back"] = "identity_back_key"
        if not getattr(instance, "profile_image_key", None):
            mapping["profile"] = "profile_image_key"
        return mapping


class SignupDetailsForm(forms.ModelForm):
    """Self-service completion of missing signup details.

    The form never mutates the database itself: the caller owns self-only
    authorization, row locking, the transaction, session-hash refresh, and
    save invocation. Only currently missing signup fields plus email and
    password are editable; any nonblank signup value stays disabled so forged
    POST data cannot overwrite it. Username is an identity key and is never
    editable. ``has_editable_fields`` is always true for the own profile
    because email and password are always editable.
    """

    # Required at signup; any blank value becomes enabled and required.
    SIGNUP_REQUIRED_FIELDS = frozenset({
        "phone", "country", "city", "education_or_job", "priest_name",
        "priest_phone", "church", "identity_type", "identity_number", "time_zone",
    })

    password = forms.CharField(
        label=_("Password"),
        required=False,
        widget=forms.PasswordInput(attrs={
            "placeholder": _("Password"),
            "autocomplete": "new-password",
        }),
    )
    full_name = forms.CharField(label=_("Full Name"), required=False)
    country = forms.ChoiceField(label=_("Country"), choices=country_choices(), required=False)
    identity_type = forms.ChoiceField(
        label=_("Identity Type"),
        choices=User.IDENTITY_TYPES,
        required=False,
    )
    time_zone = forms.ChoiceField(label=_("Time zone"), choices=(), required=False)

    class Meta:
        model = User
        # ``password`` is intentionally excluded: it is not a model-bound
        # form field here, so ``model_to_dict`` never seeds the stored hash
        # into initial and ``construct_instance`` never copies plaintext or a
        # blank value onto the instance. The declared field is merged into
        # the form below.
        fields = [
            "full_name", "username", "email", "phone", "country", "city",
            "education_or_job", "priest_name", "priest_phone", "church", "service",
            "identity_type", "identity_number", "time_zone",
        ]
        widgets = {
            "full_name": forms.TextInput(),
            "username": forms.TextInput(),
            "email": forms.EmailInput(),
            "phone": forms.TextInput(),
            "city": forms.TextInput(),
            "education_or_job": forms.TextInput(),
            "priest_name": forms.TextInput(),
            "priest_phone": forms.TextInput(),
            "church": forms.TextInput(),
            "service": forms.TextInput(),
            "identity_number": forms.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = _("Username")
        self.fields["password"].label = _("Password")
        self.fields["email"].label = _("Email")
        self.fields["phone"].label = _("Phone")
        self.fields["city"].label = _("City")
        self.fields["education_or_job"].label = _("Educational qualification / occupation")
        self.fields["priest_name"].label = _("Confessor Name")
        self.fields["priest_phone"].label = _("Confessor Phone")
        self.fields["church"].label = _("Church")
        self.fields["service"].label = _("Service (if any)")
        self.fields["identity_number"].label = _("Identity Number")
        self.fields["full_name"].initial = self.instance.get_full_name()
        self.fields["time_zone"].choices = user_time_zone_choices()
        self.fields["time_zone"].initial = getattr(self.instance, "time_zone", None) or settings.TIME_ZONE
        current_country = getattr(self.instance, "country", None)
        if current_country and current_country not in dict(country_choices()):
            self.fields["country"].choices = [(current_country, current_country)] + list(self.fields["country"].choices)
        self.order_fields([
            "full_name", "username", "password", "email", "phone", "country", "city",
            "education_or_job", "priest_name", "priest_phone", "church", "service",
            "identity_type", "identity_number", "time_zone",
        ])
        full_name_editable = not (
            getattr(self.instance, "first_name", None)
            and getattr(self.instance, "last_name", None)
        )
        for field_name, field in self.fields.items():
            if field_name == "username":
                field.disabled = True
                field.required = False
            elif field_name in ("email", "password"):
                field.disabled = False
                field.required = False
            elif field_name == "full_name":
                field.disabled = not full_name_editable
                field.required = full_name_editable
            else:
                missing = not (getattr(self.instance, field_name, None) or "").strip()
                field.disabled = not missing
                field.required = (
                    field_name in self.SIGNUP_REQUIRED_FIELDS and missing
                )
        self.has_editable_fields = any(
            not field.disabled for field in self.fields.values()
        )

    def clean_full_name(self):
        full_name = " ".join((self.cleaned_data.get("full_name") or "").split())
        if self.fields["full_name"].disabled:
            # Disabled fields keep the stored full name; forged POST data is
            # never consulted for it.
            return full_name
        if len(full_name.split()) < 2:
            raise forms.ValidationError(_("Enter your full name."))
        return full_name

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        if not phone:
            return phone
        phone = normalize_phone(phone)
        if not phone:
            raise forms.ValidationError(_("Enter a valid international phone number."))
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
                raise forms.ValidationError(e.message if hasattr(e, "message") else str(e))
        return identity_number

    def save(self, commit=True):
        user = super().save(commit=False)
        if not self.fields["full_name"].disabled:
            name_parts = self.cleaned_data["full_name"].split()
            user.first_name = name_parts[0]
            user.last_name = " ".join(name_parts[1:])
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user
