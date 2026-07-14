from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from .models import *

# Register your models here.
admin.site.register(User)
admin.site.register(Role)
admin.site.register(Course)
admin.site.register(Lesson)
admin.site.register(Quiz)
admin.site.register(Question)
admin.site.register(Submission)
admin.site.register(Grade)


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ["name", "level", "starts_on", "ends_on", "is_current"]
    list_filter = ["level", "is_current"]
    search_fields = ["name"]
    ordering = ["-starts_on"]


@admin.register(CourseOffering)
class CourseOfferingAdmin(admin.ModelAdmin):
    list_display = ["course", "academic_year", "instructor", "status"]
    list_filter = ["academic_year__level", "status", "academic_year"]
    search_fields = ["course__name", "instructor"]


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = [
        "student", "academic_year", "course_offering",
        "enrollment_type", "status", "enrolled_at",
    ]
    list_filter = ["enrollment_type", "status", "academic_year__level"]
    search_fields = ["student__username"]
    raw_id_fields = ["student", "enrolled_by"]


@admin.register(OfflineCity)
class OfflineCityAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active"]
    search_fields = ["name"]


@admin.register(AcademicHoliday)
class AcademicHolidayAdmin(admin.ModelAdmin):
    list_display = ["academic_year", "date", "name"]
    list_filter = ["academic_year"]
    search_fields = ["name"]


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ["student", "academic_year", "attendance_date", "action", "scanned_at", "scanned_by"]
    list_filter = ["action", "academic_year"]
    search_fields = ["student__username"]
    raw_id_fields = ["student", "scanned_by"]


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
