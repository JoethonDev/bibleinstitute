"""URL configuration for the locked student mobile API.

Included from the project root at the non-language-prefixed
``/api/mobile/v1/`` boundary. View behavior, authentication, and data
queries live in :mod:`management_system.mobile_api` and are implemented
outside this URLconf.
"""
from django.urls import path

from . import mobile_api

app_name = "mobile-v1"

urlpatterns = [
    path("auth/login/", mobile_api.login, name="login"),
    path("auth/otp/request/", mobile_api.otp_request, name="otp-request"),
    path("auth/otp/verify/", mobile_api.otp_verify, name="otp-verify"),
    path("auth/logout/", mobile_api.logout, name="logout"),
    path("me/", mobile_api.me, name="me"),
    path("profile/", mobile_api.profile, name="profile"),
    path("profile/qr/", mobile_api.profile_qr, name="profile-qr"),
    path("courses/", mobile_api.courses, name="courses"),
    path(
        "courses/<int:offering_id>/",
        mobile_api.course_detail,
        name="course-detail",
    ),
    path(
        "courses/<int:offering_id>/lessons/<int:lesson_id>/",
        mobile_api.lesson_detail,
        name="lesson-detail",
    ),
    path(
        "courses/<int:offering_id>/quizzes/<int:quiz_id>/",
        mobile_api.quiz_detail,
        name="quiz-detail",
    ),
    path(
        "courses/<int:offering_id>/quizzes/<int:quiz_id>/submit/",
        mobile_api.quiz_submit,
        name="quiz-submit",
    ),
    path(
        "courses/<int:offering_id>/quiz-status/",
        mobile_api.quiz_status,
        name="quiz-status",
    ),
    path("calendar/", mobile_api.calendar, name="calendar"),
    path("progress/", mobile_api.progress, name="progress"),
    path("grades/", mobile_api.grades, name="grades"),
    path("notifications/", mobile_api.notifications, name="notifications"),
    path(
        "notifications/unread-count/",
        mobile_api.notification_unread_count,
        name="notification-unread-count",
    ),
    path(
        "notifications/read-all/",
        mobile_api.notification_read_all,
        name="notification-read-all",
    ),
    path(
        "notifications/<int:notification_id>/read/",
        mobile_api.notification_read,
        name="notification-read",
    ),
    path("push-devices/", mobile_api.push_devices, name="push-devices"),
    path(
        "push-devices/current/",
        mobile_api.push_device_deactivate,
        name="push-device-current",
    ),
    path(
        "courses/<int:offering_id>/lessons/<int:lesson_id>/media/<int:file_index>/session/",
        mobile_api.media_session,
        name="media-session",
    ),
    path(
        "courses/<int:offering_id>/lessons/<int:lesson_id>/media/<int:file_index>/manifest/",
        mobile_api.media_manifest,
        name="media-manifest",
    ),
    path(
        "courses/<int:offering_id>/lessons/<int:lesson_id>/progress/",
        mobile_api.lesson_progress,
        name="lesson-progress",
    ),
    path(
        "courses/<int:offering_id>/lessons/<int:lesson_id>/audio/",
        mobile_api.lesson_audio,
        name="lesson-audio",
    ),
]
