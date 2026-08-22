"""JSON API for the native mobile client."""

from __future__ import annotations

import json
from io import BytesIO

from django.contrib.auth import authenticate
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import HttpResponse, QueryDict
from django.urls import reverse
from django.utils import timezone
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
import qrcode

from .forms import SignupDetailsForm
from .mobile_auth import (
    MobileBiometricError,
    enroll_mobile_biometric,
    get_mobile_session,
    issue_mobile_session,
    clear_mobile_login_failures,
    normalize_mobile_installation_id,
    mobile_installation_conflict,
    mobile_login_rate_limited,
    json_api_response,
    normalize_language,
    revoke_other_account_biometric,
    revoke_mobile_biometric,
    require_mobile_session,
    revoke_mobile_session,
    record_mobile_login_failure,
    unlock_mobile_biometric,
)
from .mobile_otp import (
    OTP_RESEND_INTERVAL,
    MobileOtpError,
    request_mobile_otp,
    verify_mobile_otp,
)
from .models import (
    Grade,
    LectureProgress,
    MobilePushDevice,
    StudentNotification,
    User,
    ViewingSession,
)
from .utils.student_data import (
    PAGE_SIZE,
    _authorized_lesson,
    notification_payload,
    student_calendar_data,
    student_course_data,
    student_courses_data,
    student_grades_queryset,
    student_lesson_data,
    student_notification_data,
    student_profile_data,
    student_progress_data,
    student_quiz_data,
    student_quiz_statuses,
)
from .views import (
    create_viewing_session,
    generate_audio_download,
    lesson_manifest,
    progress_heartbeat as existing_progress_heartbeat,
    sign_session,
    take_exam,
    _valid_session_token,
)


def _localized(request, message: str) -> str:
    with translation.override(normalize_language(request)):
        return _(message)


def _json_body(request) -> dict | None:
    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _error(request, code: str, message: str, status: int, details: dict | None = None):
    error = {"code": code, "message": message}
    if details:
        error["details"] = details
    return json_api_response(request, {"error": error}, status=status)


def _mobile_installation_id(request, payload: dict) -> str | None:
    value = request.headers.get("X-Installation-ID")
    if value is None:
        value = payload.get("installation_id")
    return normalize_mobile_installation_id(value)


def _form_error(request, form, message: str, status: int = 400):
    fields = {
        name: [str(error) for error in errors]
        for name, errors in form.errors.items()
    }
    return json_api_response(
        request,
        {"error": {"code": "validation_error", "message": message, "field_errors": fields}},
        status=status,
    )


def _page_value(request) -> int:
    try:
        return max(1, int(request.GET.get("page", "1")))
    except (TypeError, ValueError):
        return 1


def _paginated_payload(page_obj, items: list[dict]) -> dict:
    return {
        "items": items,
        "pagination": {
            "page": page_obj.number,
            "page_size": PAGE_SIZE,
            "pages": page_obj.paginator.num_pages,
            "total": page_obj.paginator.count,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
        },
    }


@csrf_exempt
@require_POST
def login(request):
    payload = _json_body(request)
    if payload is None:
        return _error(request, "invalid_request", _localized(request, "Invalid request format."), 400)
    username = payload.get("username")
    password = payload.get("password")
    remote_addr = request.META.get("REMOTE_ADDR", "")
    username_key = username.strip().lower() if isinstance(username, str) else ""
    if mobile_login_rate_limited(username_key, remote_addr):
        return _error(request, "authentication_rate_limited", _localized(request, "Too many login attempts. Try again later."), 429)
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        record_mobile_login_failure(username_key, remote_addr)
        return _error(request, "invalid_credentials", _localized(request, "Invalid username or password."), 401)
    user = authenticate(request=request, username=username, password=password)
    if (
        user is None
        or not user.is_active
        or user.application_status != "active"
    ):
        record_mobile_login_failure(username_key, remote_addr)
        return _error(request, "invalid_credentials", _localized(request, "Invalid username or password."), 401)
    clear_mobile_login_failures(username_key, remote_addr)
    installation_id = payload.get("installation_id")
    if isinstance(installation_id, str) and mobile_installation_conflict(user, installation_id):
        return _error(request, "device_conflict", _localized(request, "This push device belongs to another account."), 409)
    revoke_other_account_biometric(
        user,
        normalize_mobile_installation_id(payload.get("biometric_installation_id")),
    )
    token, session = issue_mobile_session(
        user,
    )
    return json_api_response(request, {
        "token": token,
        "token_type": "Bearer",
        "expires_at": session.expires_at.isoformat(),
        "user": student_profile_data(user),
    })


@csrf_exempt
@require_POST
def otp_request(request):
    payload = _json_body(request)
    if payload is None:
        return _error(request, "invalid_request", _localized(request, "Invalid request format."), 400)
    try:
        challenge = request_mobile_otp(
            payload.get("phone_number", ""),
            payload.get("installation_id", ""),
            request.META.get("REMOTE_ADDR", ""),
        )
    except MobileOtpError as exc:
        response = _error(
            request,
            exc.code,
            _localized(request, exc.message),
            exc.status,
            getattr(exc, "details", None),
        )
        if exc.retry_after is not None:
            response["Retry-After"] = str(exc.retry_after)
        return response
    return json_api_response(
        request,
        {
            "status": "otp_sent",
            "challenge_id": str(challenge.challenge_id),
            "expires_at": challenge.expires_at.isoformat(),
            "delivery": "telegram",
            "retry_after_seconds": int(OTP_RESEND_INTERVAL.total_seconds()),
        },
        status=202,
    )


@csrf_exempt
@require_POST
def otp_verify(request):
    payload = _json_body(request)
    if payload is None:
        return _error(request, "invalid_request", _localized(request, "Invalid request format."), 400)
    try:
        user = verify_mobile_otp(
            payload.get("challenge_id", ""),
            payload.get("otp", ""),
            payload.get("installation_id", ""),
            payload.get("phone_number", ""),
        )
    except MobileOtpError as exc:
        response = _error(
            request,
            exc.code,
            _localized(request, exc.message),
            exc.status,
            getattr(exc, "details", None),
        )
        if exc.retry_after is not None:
            response["Retry-After"] = str(exc.retry_after)
        return response
    revoke_other_account_biometric(
        user,
        normalize_mobile_installation_id(payload.get("biometric_installation_id")),
    )
    token, session = issue_mobile_session(user)
    return json_api_response(
        request,
        {
            "token": token,
            "token_type": "Bearer",
            "expires_at": session.expires_at.isoformat(),
            "user": student_profile_data(user),
        },
    )


@csrf_exempt
@require_mobile_session
@require_POST
def logout(request):
    revoked = revoke_mobile_session(request)
    if not revoked:
        return _error(request, "authentication_required", _("Authentication required."), 401)
    return json_api_response(request, {"status": "logged_out"})


@csrf_exempt
@require_mobile_session
@require_POST
def biometric_enroll(request):
    payload = _json_body(request)
    if payload is None:
        return _error(request, "invalid_request", _localized(request, "Invalid request format."), 400)
    installation_id = _mobile_installation_id(request, payload)
    if installation_id is None:
        return _error(request, "installation_required", _localized(request, "A device installation ID is required."), 400)
    try:
        credential = enroll_mobile_biometric(request.user, installation_id)
    except MobileBiometricError as exc:
        return _error(request, exc.code, _localized(request, exc.message), exc.status)
    return json_api_response(
        request,
        {"status": "enrolled", "credential": credential},
    )


@csrf_exempt
@require_POST
def biometric_unlock(request):
    payload = _json_body(request)
    if payload is None:
        return _error(request, "invalid_request", _localized(request, "Invalid request format."), 400)
    installation_id = _mobile_installation_id(request, payload)
    if installation_id is None:
        return _error(request, "installation_required", _localized(request, "A device installation ID is required."), 400)
    try:
        token, session, user = unlock_mobile_biometric(
            installation_id,
            payload.get("credential"),
            request.META.get("REMOTE_ADDR", ""),
        )
    except MobileBiometricError as exc:
        return _error(request, exc.code, _localized(request, exc.message), exc.status)
    return json_api_response(
        request,
        {
            "token": token,
            "token_type": "Bearer",
            "expires_at": session.expires_at.isoformat(),
            "user": student_profile_data(user),
        },
    )


@csrf_exempt
@require_mobile_session
@require_http_methods(["DELETE"])
def biometric_revoke(request):
    payload = _json_body(request)
    if payload is None:
        return _error(request, "invalid_request", _localized(request, "Invalid request format."), 400)
    installation_id = _mobile_installation_id(request, payload)
    if installation_id is None:
        return _error(request, "installation_required", _localized(request, "A device installation ID is required."), 400)
    try:
        updated = revoke_mobile_biometric(request.user, installation_id)
    except MobileBiometricError as exc:
        return _error(request, exc.code, _localized(request, exc.message), exc.status)
    return json_api_response(request, {"status": "revoked", "updated": updated})


@require_mobile_session
@require_http_methods(["GET"])
def me(request):
    return json_api_response(request, {"user": student_profile_data(request.user)})


@require_mobile_session
@require_http_methods(["GET", "PATCH"])
@csrf_exempt
def profile(request):
    if request.method == "GET":
        return json_api_response(request, {"user": student_profile_data(request.user)})
    payload = _json_body(request)
    if payload is None:
        return _error(request, "invalid_request", _("Invalid request format."), 400)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        form = SignupDetailsForm(payload, instance=user)
        if not form.is_valid():
            return _form_error(request, form, _("Please correct the highlighted fields."))
        user = form.save()
    request.user = user
    return json_api_response(request, {"user": student_profile_data(user)})


@require_mobile_session
@require_http_methods(["GET"])
def profile_qr(request):
    if not request.user.qr_token:
        return _error(request, "qr_unavailable", _("The QR code is unavailable."), 404)
    qr_data = request.build_absolute_uri(reverse("scan-preview", args=[request.user.qr_token]))
    image = qrcode.make(qr_data)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return HttpResponse(buffer.getvalue(), content_type="image/png")


@require_mobile_session
@require_http_methods(["GET"])
def courses(request):
    language = normalize_language(request)
    items = student_courses_data(request.user, language)
    return json_api_response(request, {
        "items": items,
        "pagination": {"page": 1, "page_size": PAGE_SIZE, "pages": 1, "total": len(items), "has_next": False, "has_previous": False},
    })


@require_mobile_session
@require_http_methods(["GET"])
def course_detail(request, offering_id):
    data = student_course_data(request.user, offering_id, normalize_language(request))
    if data is None:
        return _error(request, "forbidden", _("You do not have access to this offering."), 403)
    return json_api_response(request, {"course": data})


@require_mobile_session
@require_http_methods(["GET"])
def lesson_detail(request, offering_id, lesson_id):
    data = student_lesson_data(request.user, offering_id, lesson_id)
    if data is None:
        return _error(request, "forbidden", _("You do not have access to this lesson."), 403)
    return json_api_response(request, {"lesson": data})


@require_mobile_session
@require_http_methods(["GET"])
def quiz_detail(request, offering_id, quiz_id):
    data = student_quiz_data(request.user, offering_id, quiz_id, normalize_language(request))
    if data is None:
        return _error(request, "forbidden", _("You do not have access to this quiz."), 403)
    return json_api_response(request, {"quiz": data})


@require_mobile_session
@require_http_methods(["GET"])
def quiz_status(request, offering_id):
    data = student_quiz_statuses(request.user, offering_id)
    if data is None:
        return _error(request, "forbidden", _("You do not have access to this offering."), 403)
    return json_api_response(request, {"quizzes": data})


@csrf_exempt
@require_mobile_session
@require_POST
def quiz_submit(request, offering_id, quiz_id):
    payload = _json_body(request)
    if payload is None or not isinstance(payload.get("answers"), (list, dict)):
        return _error(request, "invalid_request", _("Invalid quiz submission."), 400)
    data = student_quiz_data(request.user, offering_id, quiz_id, normalize_language(request))
    if data is None:
        return _error(request, "forbidden", _("You do not have access to this quiz."), 403)
    if data["mode"] != "exam":
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            existing_grade = Grade.objects.filter(
                user=request.user,
                quiz_id=quiz_id,
            ).values("total_grade", "submitted_at").first()
        if existing_grade is not None:
            return json_api_response(request, {"status": "submitted", "grade": existing_grade})
        return _error(request, "submission_closed", _("The quiz is closed or already submitted."), 400)

    answers = payload["answers"]
    if isinstance(answers, dict):
        answer_rows = [{"id": key, "answer": value} for key, value in answers.items()]
    else:
        answer_rows = answers
    if len(answer_rows) > 500 or any(not isinstance(row, dict) for row in answer_rows):
        return _error(request, "invalid_request", _("Invalid quiz submission."), 400)

    query = QueryDict("", mutable=True)
    for index, row in enumerate(answer_rows):
        question_id = row.get("id")
        if not str(question_id).isdigit():
            return _error(request, "invalid_request", _("Invalid quiz submission."), 400)
        answer = row.get("answer", "")
        if isinstance(answer, (dict, list)):
            answer = json.dumps(answer, ensure_ascii=False)
        query[f"questions[{index}][id]"] = str(question_id)
        query[f"questions[{index}][answer]"] = str(answer)
    request.POST = query
    response = take_exam(request, offering_id, quiz_id, allow_management=True)
    if getattr(response, "status_code", 0) in {301, 302, 303, 307, 308}:
        grade = Grade.objects.filter(user=request.user, quiz_id=quiz_id).values("total_grade", "submitted_at").first()
        if grade is None:
            return _error(request, "submission_failed", _("The quiz could not be submitted."), 400)
        return json_api_response(request, {"status": "submitted", "grade": grade})
    return _error(request, "submission_failed", _("The quiz could not be submitted."), 400)


@require_mobile_session
@require_http_methods(["GET"])
def calendar(request):
    return json_api_response(request, {"calendar": student_calendar_data(request.user, normalize_language(request))})


@require_mobile_session
@require_http_methods(["GET"])
def progress(request):
    raw_ids = request.GET.get("offering_ids", "")
    offering_ids = None
    if raw_ids:
        values = raw_ids.split(",")
        if not values or any(not value.isdigit() for value in values):
            return _error(request, "invalid_request", _localized(request, "Invalid offering identifier."), 400)
        offering_ids = [int(value) for value in values[:PAGE_SIZE]]
    page_obj, items = student_progress_data(request.user, offering_ids, _page_value(request))
    return json_api_response(request, _paginated_payload(page_obj, items))


@require_mobile_session
@require_http_methods(["GET"])
def grades(request):
    page_obj = Paginator(student_grades_queryset(request.user), PAGE_SIZE).get_page(_page_value(request))
    items = [
        {
            "id": grade.pk,
            "quiz_id": grade.quiz_id,
            "quiz": grade.quiz.name,
            "offering_id": grade.quiz.course_offering_id,
            "course": grade.quiz.course_offering.course.name,
            "total_grade": grade.total_grade,
            "submitted_at": grade.submitted_at.isoformat(),
        }
        for grade in page_obj.object_list
    ]
    return json_api_response(request, _paginated_payload(page_obj, items))


@require_mobile_session
@require_http_methods(["GET"])
def notifications(request):
    page_obj, unread_count = student_notification_data(
        request.user, normalize_language(request), _page_value(request)
    )
    language = normalize_language(request)
    items = [notification_payload(row, language) for row in page_obj.object_list]
    result = _paginated_payload(page_obj, items)
    result["unread_count"] = unread_count
    return json_api_response(request, result)


@require_mobile_session
@require_http_methods(["GET"])
def notification_unread_count(request):
    unread_count = StudentNotification.objects.filter(
        student=request.user,
        cancelled_at__isnull=True,
        scheduled_for__lte=timezone.now(),
        read_at__isnull=True,
    ).count()
    return json_api_response(request, {"unread_count": unread_count})


@csrf_exempt
@require_mobile_session
@require_POST
def notification_read(request, notification_id):
    with transaction.atomic():
        notification = StudentNotification.objects.select_for_update().filter(
            pk=notification_id,
            student=request.user,
            cancelled_at__isnull=True,
            scheduled_for__lte=timezone.now(),
        ).first()
        if notification is None:
            return _error(request, "forbidden", _("You do not have access to this notification."), 403)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at", "updated_at"])
    return json_api_response(request, {"status": "read", "notification_id": notification_id})


@csrf_exempt
@require_mobile_session
@require_POST
def notification_read_all(request):
    updated = StudentNotification.objects.filter(
        student=request.user,
        cancelled_at__isnull=True,
        scheduled_for__lte=timezone.now(),
        read_at__isnull=True,
    ).update(read_at=timezone.now(), updated_at=timezone.now())
    return json_api_response(request, {"status": "read", "updated": updated})


@csrf_exempt
@require_mobile_session
@require_http_methods(["GET", "POST"])
def push_devices(request):
    if request.method == "GET":
        devices = MobilePushDevice.objects.filter(user=request.user).order_by("-last_seen_at", "-pk")
        return json_api_response(request, {"devices": [
            {"id": device.pk, "installation_id": device.installation_id, "platform": device.platform, "is_active": device.is_active}
            for device in devices
        ]})
    payload = _json_body(request)
    token = payload.get("expo_push_token") if payload else None
    installation_id = payload.get("installation_id") if payload else None
    platform = payload.get("platform") if payload else None
    if (
        not isinstance(token, str) or not token.startswith("ExponentPushToken[") or len(token) > 255
        or not isinstance(installation_id, str) or not installation_id or len(installation_id) > 128
        or platform not in {MobilePushDevice.Platform.ANDROID, MobilePushDevice.Platform.IOS}
    ):
        return _error(request, "invalid_request", _("Invalid push-device registration."), 400)
    if mobile_installation_conflict(request.user, installation_id):
        return _error(request, "device_conflict", _("This push device belongs to another account."), 409)
    try:
        with transaction.atomic():
            conflicting_installation = MobilePushDevice.objects.select_for_update().filter(
                installation_id=installation_id,
            ).exclude(user=request.user).exists()
            if conflicting_installation:
                return _error(request, "device_conflict", _("This push device belongs to another account."), 409)
            existing_token = MobilePushDevice.objects.select_for_update().filter(expo_push_token=token).first()
            if existing_token and existing_token.user_id != request.user.pk:
                return _error(request, "device_conflict", _("This push device belongs to another account."), 409)
            device = MobilePushDevice.objects.select_for_update().filter(
                user=request.user, installation_id=installation_id
            ).first()
            if device is None:
                device = MobilePushDevice.objects.create(
                    user=request.user,
                    expo_push_token=token,
                    installation_id=installation_id,
                    platform=platform,
                )
            else:
                device.expo_push_token = token
                device.platform = platform
                device.is_active = True
                device.disabled_at = None
                device.last_seen_at = timezone.now()
                device.save(update_fields=["expo_push_token", "platform", "is_active", "disabled_at", "last_seen_at", "updated_at"])
    except IntegrityError:
        return _error(request, "device_conflict", _("This push device belongs to another account."), 409)
    return json_api_response(request, {"device": {"id": device.pk, "installation_id": device.installation_id, "platform": device.platform, "is_active": True}})


@csrf_exempt
@require_mobile_session
@require_http_methods(["DELETE"])
def push_device_deactivate(request):
    installation_id = request.headers.get("X-Installation-ID")
    if not installation_id:
        payload = _json_body(request) or {}
        installation_id = payload.get("installation_id")
    if not isinstance(installation_id, str) or not installation_id:
        return _error(request, "invalid_request", _("Installation ID is required."), 400)
    updated = MobilePushDevice.objects.filter(
        user=request.user, installation_id=installation_id, is_active=True
    ).update(is_active=False, disabled_at=timezone.now(), updated_at=timezone.now())
    return json_api_response(request, {"status": "deactivated", "updated": updated})


def _media_link(request, offering_id, lesson_id, file_index):
    lesson = _authorized_lesson(request.user, offering_id, lesson_id)
    if lesson is None:
        return None, None
    try:
        links = json.loads(lesson.links or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return lesson, None
    if not isinstance(links, list):
        return lesson, None
    if file_index < 0 or file_index >= len(links) or not isinstance(links[file_index], dict):
        return lesson, None
    return lesson, links[file_index]


@csrf_exempt
@require_mobile_session
@require_POST
def media_session(request, offering_id, lesson_id, file_index):
    lesson, link = _media_link(request, offering_id, lesson_id, file_index)
    if lesson is None:
        return _error(request, "forbidden", _("You do not have access to this lesson."), 403)
    if not link or link.get("file_type") not in {"video", "audio"} or not link.get("id"):
        return _error(request, "invalid_media", _("Invalid media file."), 400)
    payload = _json_body(request)
    if payload is None:
        return _error(request, "invalid_request", _localized(request, "Invalid request format."), 400)
    renew = payload.get("renew") is True
    part_id = link.get("part_id", "")
    session = None
    if not renew:
        session = ViewingSession.objects.filter(
            student=request.user,
            lesson=lesson,
            part_id=part_id,
            access_channel=ViewingSession.AccessChannel.MOBILE,
            mobile_session=request.mobile_session,
            expires_at__gt=timezone.now(),
        ).order_by("-expires_at").first()
    if session is None:
        session = create_viewing_session(
            request.user,
            lesson,
            part_id,
            mobile_session=request.mobile_session,
        )
    progress_percent = LectureProgress.objects.filter(
        student=request.user, lesson=lesson, part_id=part_id
    ).values_list("percent", flat=True).first() or 0
    return json_api_response(request, {
        "session_id": session.session_id,
        "token": sign_session(
            session.session_id,
            session.expires_at,
            audience="mobile",
            mobile_session_id=request.mobile_session.pk,
        ),
        "media_audience": "mobile",
        "expires_at": session.expires_at.isoformat(),
        "manifest_url": request.build_absolute_uri(reverse("mobile-v1:media-manifest", kwargs={"offering_id": offering_id, "lesson_id": lesson_id, "file_index": file_index})),
        "progress_percent": max(0, min(int(progress_percent), 100)),
    })


@require_http_methods(["GET"])
def media_manifest(request, offering_id, lesson_id, file_index):
    session_id = request.GET.get("session_id", "")
    token = request.GET.get("token", "")
    if not session_id or not token:
        return _error(request, "viewing_session_required", _("Viewing session is required."), 401)
    session = ViewingSession.objects.filter(
        session_id=session_id,
    ).select_related("student", "mobile_session").first()
    if (
        session is None
        or session.access_channel != ViewingSession.AccessChannel.MOBILE
        or session.mobile_session_id is None
        or session.mobile_session.revoked_at is not None
        or session.mobile_session.expires_at <= timezone.now()
        or not session.student.is_active
        or session.student.application_status != "active"
    ):
        return _error(request, "viewing_session_required", _("Viewing session is required."), 401)
    if timezone.now() >= session.expires_at:
        return _error(request, "viewing_session_expired", _("Viewing session expired."), 401)
    if not _valid_session_token(
        token,
        session,
        audience="mobile",
        mobile_session_id=session.mobile_session_id,
    ):
        return _error(request, "invalid_viewing_session", _("Invalid viewing session token."), 403)
    request.user = session.student
    request.media_audience = "mobile"
    lesson, link = _media_link(request, offering_id, lesson_id, file_index)
    if lesson is None:
        return _error(request, "forbidden", _("You do not have access to this lesson."), 403)
    if (
        not link
        or link.get("file_type") not in {"video", "audio"}
        or session.lesson_id != lesson.pk
        or session.part_id != link.get("part_id", "")
    ):
        return _error(request, "invalid_viewing_session", _("The viewing session is invalid."), 403)
    # The signed viewing session is the media credential. Native HLS requests
    # must not carry the mobile bearer token to the media host.
    response = lesson_manifest(request, offering_id, lesson_id, file_index)
    if response.status_code >= 400:
        return _error(
            request,
            "media_unavailable" if response.status_code == 404 else "invalid_viewing_session",
            _("The media is unavailable.") if response.status_code == 404 else _("The viewing session is invalid."),
            response.status_code,
        )
    return response


@require_mobile_session
@require_http_methods(["GET"])
def lesson_audio(request, offering_id, lesson_id):
    try:
        file_index = int(request.GET.get("file_index", "-1"))
    except (TypeError, ValueError):
        return _error(request, "invalid_media", _("Invalid media file."), 400)
    lesson, link = _media_link(request, offering_id, lesson_id, file_index)
    if lesson is None:
        return _error(request, "forbidden", _("You do not have access to this lesson."), 403)
    if not link or link.get("file_type") != "audio":
        return _error(request, "invalid_media", _("Invalid audio file."), 400)
    response = generate_audio_download(request, offering_id, lesson_id)
    if response.status_code in {301, 302, 303, 307, 308}:
        return response
    return _error(request, "audio_unavailable", _("Audio download is temporarily unavailable."), 503)


def _call_existing_progress(request, lesson_id=None, offering_id=None):
    payload = _json_body(request)
    if payload is None:
        return _error(request, "invalid_request", _("Invalid request format."), 400)
    session_id = payload.get("session_id")
    viewing_token = payload.get("viewing_token")
    if not isinstance(viewing_token, str) or not viewing_token:
        return _error(request, "viewing_session_required", _("Viewing session is required."), 401)
    session = ViewingSession.objects.select_related("lesson__course_offering").filter(
        session_id=session_id,
        student=request.user,
        access_channel=ViewingSession.AccessChannel.MOBILE,
        mobile_session=request.mobile_session,
    ).first()
    if session is None or (lesson_id is not None and session.lesson_id != lesson_id) or (
        offering_id is not None and session.lesson.course_offering_id != offering_id
    ):
        return _error(request, "forbidden", _("You do not have access to this viewing session."), 403)
    if not _valid_session_token(
        viewing_token,
        session,
        audience="mobile",
        mobile_session_id=request.mobile_session.pk,
    ):
        return _error(request, "forbidden", _("You do not have access to this viewing session."), 403)
    response = existing_progress_heartbeat(request, allow_management=True)
    try:
        data = json.loads(response.content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _error(request, "progress_failed", _("Progress could not be saved."), response.status_code)
    if response.status_code >= 400:
        return _error(request, "progress_failed", _("Progress could not be saved."), response.status_code)
    return json_api_response(request, data, response.status_code)


@csrf_exempt
@require_mobile_session
@require_POST
def lesson_progress(request, offering_id, lesson_id):
    return _call_existing_progress(request, lesson_id, offering_id)
