"""Mobile notification withdrawal cursor API."""

from __future__ import annotations

from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from .mobile_auth import require_mobile_session
from .mobile_http import json_api_response
from .models import StudentNotification


MAX_NOTIFICATION_WITHDRAWAL_IDS = 100


@require_mobile_session
@require_GET
def notification_withdrawals(request):
    """Return which requested notification IDs this account has withdrawn."""
    raw_ids = request.GET.getlist("id")
    if len(raw_ids) > MAX_NOTIFICATION_WITHDRAWAL_IDS:
        return json_api_response(
            request,
            {"error": {"code": "invalid_ids", "message": _("Too many notification IDs were requested.")}},
            status=400,
        )
    notification_ids = []
    for raw_id in raw_ids:
        try:
            if not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 20:
                raise ValueError
            notification_id = int(raw_id)
        except (TypeError, ValueError):
            notification_id = 0
        if notification_id <= 0:
            return json_api_response(
                request,
                {"error": {"code": "invalid_ids", "message": _("A notification ID is invalid.")}},
                status=400,
            )
        notification_ids.append(notification_id)
    if len(notification_ids) != len(set(notification_ids)):
        return json_api_response(
            request,
            {"error": {"code": "invalid_ids", "message": _("Duplicate notification IDs were requested.")}},
            status=400,
        )

    withdrawn_ids = list(StudentNotification.objects.filter(
        student_id=request.user.pk,
        withdrawn_at__isnull=False,
        pk__in=notification_ids,
    ).order_by("pk").values_list("pk", flat=True)) if notification_ids else []
    return json_api_response(
        request,
        {"withdrawn_ids": withdrawn_ids},
    )
