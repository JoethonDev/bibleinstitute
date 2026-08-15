from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from .models import *

# Register your models here.


def _is_admin_user(request):
    return getattr(getattr(request.user, "role", None), "role", None) == "admin"


class AdminOnlyModelAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return _is_admin_user(request)

    def has_view_permission(self, request, obj=None):
        return _is_admin_user(request)

    def has_add_permission(self, request):
        return _is_admin_user(request)

    def has_change_permission(self, request, obj=None):
        return _is_admin_user(request)

    def has_delete_permission(self, request, obj=None):
        return _is_admin_user(request)


admin.site.register(User)
admin.site.register(Role)
admin.site.register(Course)
admin.site.register(Lesson)
admin.site.register(Quiz)
admin.site.register(Question)
admin.site.register(Submission)
admin.site.register(Grade)


@admin.register(Level)
class LevelAdmin(AdminOnlyModelAdmin):
    list_display = ["ordering", "name_en", "name_ar", "created_at"]
    list_display_links = ["ordering"]
    search_fields = ["name_en", "name_ar"]
    ordering = ["ordering"]

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return True
        if obj.courses.exists() or obj.academic_years.exists():
            return False
        return True


@admin.register(AcademicYear)
class AcademicYearAdmin(AdminOnlyModelAdmin):
    list_display = ["name", "levels_display", "starts_on", "ends_on", "ordering", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name"]
    ordering = ["-starts_on"]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("levels")

    def levels_display(self, obj):
        return " / ".join(lvl.display_name for lvl in obj.levels.order_by("ordering"))
    levels_display.short_description = _("Levels")


@admin.register(CourseOffering)
class CourseOfferingAdmin(AdminOnlyModelAdmin):
    list_display = ["course", "academic_year_level", "instructor", "status"]
    list_filter = ["status", "academic_year_level"]
    search_fields = ["course__name", "instructor"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "course", "academic_year_level__academic_year", "academic_year_level__level"
        )


@admin.register(Enrollment)
class EnrollmentAdmin(AdminOnlyModelAdmin):
    list_display = [
        "student", "academic_year_level", "course_offering",
        "enrollment_type", "status", "enrolled_at",
    ]
    list_filter = ["enrollment_type", "status"]
    search_fields = ["student__username"]
    raw_id_fields = ["student", "enrolled_by"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "student", "academic_year_level__academic_year", "academic_year_level__level", "course_offering"
        )


@admin.register(OfflineCity)
class OfflineCityAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active"]
    search_fields = ["name"]


@admin.register(AcademicHoliday)
class AcademicHolidayAdmin(AdminOnlyModelAdmin):
    list_display = ["academic_year", "date", "name"]
    list_filter = ["academic_year"]
    search_fields = ["name"]


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(AdminOnlyModelAdmin):
    list_display = ["student", "course_offering", "attendance_date", "action", "scanned_at", "scanned_by"]
    list_filter = ["action", "course_offering"]
    search_fields = ["student__username"]
    raw_id_fields = ["student", "scanned_by"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "student", "course_offering__academic_year_level__academic_year",
            "course_offering__academic_year_level__level", "course_offering__course", "scanned_by"
        )


@admin.register(ViewingSession)
class ViewingSessionAdmin(admin.ModelAdmin):
    list_display = ["student", "lesson", "part_id", "session_id", "expires_at"]
    raw_id_fields = ["student"]


@admin.register(LectureProgress)
class LectureProgressAdmin(admin.ModelAdmin):
    list_display = ["student", "lesson", "part_id", "unique_seconds", "percent", "completed_at"]
    list_filter = ["lesson"]
    raw_id_fields = ["student"]


@admin.register(VerifiedSegmentRequest)
class VerifiedSegmentRequestAdmin(admin.ModelAdmin):
    list_display = ["session", "segment_number", "segment_key", "requested_at"]
    raw_id_fields = ["session"]


@admin.register(QuizType)
class QuizTypeAdmin(admin.ModelAdmin):
    list_display = ["code", "name_en", "name_ar"]
    search_fields = ["code", "name_en", "name_ar"]
    ordering = ["code"]
    list_per_page = 50

    def has_module_permission(self, request):
        return getattr(getattr(request.user, "role", None), "role", None) == "admin"

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


@admin.register(PromotionFormula)
class PromotionFormulaAdmin(admin.ModelAdmin):
    list_display = [
        "academic_year_level", "course_offering", "overall_pass_percent",
        "evaluation_starts_on", "evaluation_ends_on", "created_by", "updated_at",
    ]
    list_filter = ["academic_year_level"]
    raw_id_fields = ["created_by", "updated_by"]
    ordering = ["-created_at"]
    list_per_page = 50

    def has_module_permission(self, request):
        return getattr(getattr(request.user, "role", None), "role", None) == "admin"

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


@admin.register(PromotionRule)
class PromotionRuleAdmin(admin.ModelAdmin):
    list_display = ["formula", "metric", "quiz_type", "weight_percent", "minimum_percent", "ordering"]
    list_filter = ["metric", "formula"]
    ordering = ["formula", "ordering"]
    list_per_page = 50
    raw_id_fields = ["formula"]

    def has_module_permission(self, request):
        return getattr(getattr(request.user, "role", None), "role", None) == "admin"

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


@admin.register(EvaluationResult)
class EvaluationResultAdmin(admin.ModelAdmin):
    list_display = ["enrollment", "course_offering", "formula", "computed_score", "computed_status", "final_status", "saved_at"]
    list_filter = ["final_status", "formula"]
    search_fields = ["enrollment__student__username"]
    ordering = ["-saved_at"]
    list_per_page = 50
    raw_id_fields = ["enrollment", "course_offering", "overridden_by"]
    readonly_fields = [
        "formula", "enrollment", "course_offering", "computed_score", "computed_status",
        "final_status", "metric_snapshot", "override_note", "overridden_by",
        "overridden_at", "calculated_at", "saved_at",
    ]

    def has_module_permission(self, request):
        return getattr(getattr(request.user, "role", None), "role", None) in {"admin", "staff"}

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PromotionHistory)
class PromotionHistoryAdmin(admin.ModelAdmin):
    list_display = [
        "student", "source_year_level", "destination_year_level",
        "outcome", "promotion_method", "score", "final_status", "actor", "reason", "created_at",
    ]
    list_filter = ["outcome", "source_year_level"]
    search_fields = ["student__username"]
    ordering = ["-created_at"]
    list_per_page = 50
    raw_id_fields = ["student", "actor", "override_actor", "source_enrollment", "destination_enrollment"]
    readonly_fields = [
        "evaluation_result", "source_enrollment", "destination_enrollment",
        "student", "source_year_level", "destination_year_level",
        "outcome", "score", "computed_status", "final_status",
        "override_note", "override_actor", "exceptional_offering_ids",
        "formula_snapshot", "promotion_method", "reason", "actor", "created_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_module_permission(self, request):
        return getattr(getattr(request.user, "role", None), "role", None) in {"admin", "staff"}

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HistoricalAcademicSummary)
class HistoricalAcademicSummaryAdmin(admin.ModelAdmin):
    list_display = ["student", "academic_year_level", "outcome", "certificate_eligible", "promoted_at", "reviewed_by"]
    list_filter = ["outcome", "certificate_eligible", "academic_year_level"]
    search_fields = ["student__username", "student__first_name", "student__last_name", "source_name"]
    ordering = ["-created_at"]
    list_per_page = 50
    raw_id_fields = ["student", "reviewed_by"]
    readonly_fields = ["source_key", "created_at", "updated_at", "promoted_at"]

    def has_module_permission(self, request):
        return _is_admin_user(request)

    def has_view_permission(self, request, obj=None):
        return _is_admin_user(request)

    def has_change_permission(self, request, obj=None):
        return _is_admin_user(request)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MigrationReviewItem)
class MigrationReviewItemAdmin(admin.ModelAdmin):
    list_display = ["item_type", "severity", "object_id", "resolved", "created_at"]
    list_filter = ["severity", "item_type", "resolved"]
    readonly_fields = ["item_type", "severity", "object_id", "message", "resolved", "created_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MediaProcessingJob)
class MediaProcessingJobAdmin(admin.ModelAdmin):
    """Inspection-only admin for durable media-processing job state."""

    list_display = [
        "original_filename", "created_by", "source_kind", "status", "phase",
        "progress", "attempt_count", "attachment_status", "created_at", "finished_at",
    ]
    list_filter = ["status", "source_kind", "attachment_status"]
    search_fields = [
        "public_id", "original_filename", "source_key",
        "error_code", "created_by__username",
    ]
    ordering = ["-created_at", "-pk"]
    list_per_page = 50

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "created_by",
            "lesson__course_offering__course",
            "lesson__course_offering__academic_year_level",
        )

    def has_module_permission(self, request):
        return _is_admin_user(request)

    def has_view_permission(self, request, obj=None):
        return _is_admin_user(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
