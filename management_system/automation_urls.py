from django.urls import path

from .automation_api import (
    calendar_events,
    lecture_publish,
    lecture_status,
    lecture_upload,
    media_retry,
    media_start,
)


urlpatterns = [
    path("calendar/events/", calendar_events, name="automation-calendar-events"),
    path("lectures/upload/", lecture_upload, name="automation-lecture-upload"),
    path("media/<uuid:job_id>/start/", media_start, name="automation-media-start"),
    path("lectures/<int:lesson_id>/status/", lecture_status, name="automation-lecture-status"),
    path("media/<uuid:job_id>/retry/", media_retry, name="automation-media-retry"),
    path("lectures/<int:lesson_id>/publish/", lecture_publish, name="automation-lecture-publish"),
]
