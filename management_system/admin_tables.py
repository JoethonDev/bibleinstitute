"""Canonical filter scope, queryset, and signed bulk-selection contract for admin tables.

The generic admin tables (users, courses, lessons, quizzes) must only ever
delete the records an operator actually filtered. Each dashboard builds its
filter scope through the builder functions here, the scope is signed into a
selection token rendered with the table, and the bulk-delete endpoints rebuild
the exact same queryset from the signed scope. Browser-supplied IDs can only
narrow that scope; they can never widen it.
"""

from __future__ import annotations

import json
from typing import Any

from django.core import signing
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.utils.translation import gettext as _

from .models import (
    Course,
    CourseOffering,
    Lesson,
    Level,
    PublicationStatus,
    Quiz,
    Role,
    User,
)
from .utils.search import normalized_contains_q, normalize_search_text
from .utils.validators import parse_strict_bool

BULK_SELECTION_SALT = "bulk-table-selection"
SELECTION_MAX_AGE_SECONDS = 3600
MAX_EXPLICIT_SELECTION = 1000
TABLE_VIEWS = ("user", "course", "lesson", "quiz")

#: Account-state filter values accepted by the Students table. The account
#: state is ``User.is_active``; it is deliberately separate from the
#: application decision so "inactive" can never be read as "declined".
USER_ACCOUNT_FILTERS = ("active", "inactive")


def _clean_search(value: str | None) -> str:
    return normalize_search_text(str(value or "").strip()[:100])


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _choice_code(value: Any, choices) -> str:
    """Return the stored code for a submitted display label or raw code."""
    raw = str(value or "").strip()[:64]
    if not raw:
        return ""
    for code, label in choices:
        if raw == code or raw == str(label):
            return code
    return ""


def user_scope(request) -> dict[str, Any]:
    """Normalize the user dashboard filters into a JSON-safe scope.

    The scope is the single normalized description of the Students table
    filters: search, role, application decision, study mode and account
    state. Counts, ordering, SQL pagination, the signed bulk selection and
    the CSV export consume this same dictionary.
    """
    search = _clean_search(request.GET.get("name"))
    raw_role = str(request.GET.get("filtering") or "").strip()[:64]
    role_code = ""
    role_value = ""
    if raw_role:
        for code, label in Role.ROLES:
            if raw_role in (code, str(label)):
                role_code = code
                role_value = str(label)
                break
    application_status = _choice_code(
        request.GET.get("application_status"), User.APPLICATION_STATUSES
    )
    study_mode = _choice_code(request.GET.get("study_mode"), User.STUDY_MODES)
    account = str(request.GET.get("account") or "").strip().lower()[:16]
    if account not in USER_ACCOUNT_FILTERS:
        account = ""
    return {
        "search": search,
        "role": role_code,
        "role_value": role_value,
        "application_status": application_status,
        "study_mode": study_mode,
        "account": account,
    }


def user_queryset(scope: dict[str, Any]) -> QuerySet:
    query = Q()
    search = str(scope.get("search") or "")
    if search:
        query &= normalized_contains_q(
            ("username", "email", "first_name", "last_name", "phone", "identity_number"),
            search,
        )
    role_code = str(scope.get("role") or "")
    if role_code:
        query &= Q(role__role=role_code)
    application_status = str(scope.get("application_status") or "")
    if application_status:
        query &= Q(application_status=application_status)
    study_mode = str(scope.get("study_mode") or "")
    if study_mode:
        query &= Q(study_mode=study_mode)
    account = str(scope.get("account") or "")
    if account == "active":
        query &= Q(is_active=True)
    elif account == "inactive":
        query &= Q(is_active=False)
    # select_related removes the per-row role lookup the table and CSV both do.
    return User.objects.select_related("role").filter(query)


def user_tab_counts(scope: dict[str, Any]) -> dict[str, Any]:
    """Return the accurate filter-tab counts for the Students table.

    Each dimension is counted against the same normalized scope with its own
    filter removed, in one aggregate statement per dimension. This keeps the
    tab counts equal to the destination list counts without loading rows.
    """
    application_base = user_queryset(
        {**scope, "application_status": ""}
    )
    application_counts = application_base.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(application_status="pending")),
        active=Count("id", filter=Q(application_status="active")),
        declined=Count("id", filter=Q(application_status="declined")),
    )
    account_base = user_queryset({**scope, "account": ""})
    account_counts = account_base.aggregate(
        active=Count("id", filter=Q(is_active=True)),
        inactive=Count("id", filter=Q(is_active=False)),
    )
    return {
        "application": application_counts,
        "account": account_counts,
    }


def course_scope(request) -> dict[str, Any]:
    """Normalize the course dashboard filters into a JSON-safe scope."""
    search = _clean_search(request.GET.get("name"))
    raw_level = str(request.GET.get("filtering") or "").strip()[:64]
    level_ordering = None
    level_value = ""
    if raw_level:
        for level in Level.objects.order_by("ordering"):
            if raw_level in (str(level.ordering), level.display_name):
                level_ordering = level.ordering
                level_value = level.display_name
                break
    return {"search": search, "level": level_ordering, "level_value": level_value}


def course_queryset(scope: dict[str, Any]) -> QuerySet:
    query = Q()
    search = str(scope.get("search") or "")
    if search:
        query &= normalized_contains_q(("name",), search)
    level_ordering = _int_or_none(scope.get("level"))
    if level_ordering is not None:
        query &= Q(level__ordering=level_ordering)
    # select_related avoids an N+1 when serialize_pagination reads each
    # course's level (the dashboard groups and orders by level).
    return Course.objects.select_related("level").filter(query)


def content_scope(request, *, year_id: int) -> dict[str, Any]:
    """Normalize the lesson/quiz dashboard filters for one selected academic year."""
    search = _clean_search(request.GET.get("name"))
    raw_offering = str(request.GET.get("course") or "").strip()
    offering_id = _int_or_none(raw_offering)
    if offering_id is not None and not CourseOffering.objects.filter(
        pk=offering_id,
        academic_year_level__academic_year_id=year_id,
    ).exists():
        offering_id = None
    status = str(request.GET.get("status") or "").strip()[:24]
    if status not in PublicationStatus.values:
        status = ""
    return {
        "year": int(year_id),
        "search": search,
        "offering": offering_id,
        "status": status,
    }


def content_status_counts(view: str, scope: dict[str, Any]) -> dict[str, int]:
    """Publication-status counts for the lesson/quiz tables.

    One aggregate statement over the same normalized scope with the status
    filter removed, so each tab count equals its filtered list count.
    """
    queryset = scope_queryset(view, {**scope, "status": ""})
    return queryset.aggregate(
        total=Count("id"),
        draft=Count("id", filter=Q(status=PublicationStatus.DRAFT)),
        published=Count("id", filter=Q(status=PublicationStatus.PUBLISHED)),
        archived=Count("id", filter=Q(status=PublicationStatus.ARCHIVED)),
    )


def lesson_queryset(scope: dict[str, Any]) -> QuerySet:
    query = Q(course_offering__academic_year_level__academic_year_id=_int_or_none(scope.get("year")))
    search = str(scope.get("search") or "")
    if search:
        query &= normalized_contains_q(("name",), search)
    offering_id = _int_or_none(scope.get("offering"))
    if offering_id is not None:
        query &= Q(course_offering_id=offering_id)
    status = str(scope.get("status") or "")
    if status:
        query &= Q(status=status)
    return Lesson.objects.filter(query)


def quiz_queryset(scope: dict[str, Any]) -> QuerySet:
    query = Q(
        course_offering__isnull=False,
        course_offering__academic_year_level__academic_year_id=_int_or_none(scope.get("year")),
    )
    search = str(scope.get("search") or "")
    if search:
        query &= normalized_contains_q(("name",), search)
    offering_id = _int_or_none(scope.get("offering"))
    if offering_id is not None:
        query &= Q(course_offering_id=offering_id)
    status = str(scope.get("status") or "")
    if status:
        query &= Q(status=status)
    return Quiz.objects.filter(query)


_SCOPE_QUERYSETS = {
    "user": user_queryset,
    "course": course_queryset,
    "lesson": lesson_queryset,
    "quiz": quiz_queryset,
}


def scope_queryset(view: str, scope: dict[str, Any]) -> QuerySet:
    """Rebuild the canonical filtered queryset for one table view."""
    builder = _SCOPE_QUERYSETS.get(view)
    if builder is None:
        raise ValueError("Unknown table view")
    return builder(scope)


def create_selection_token(*, view: str, scope: dict[str, Any], actor_id: int) -> str:
    """Sign the view + filter scope + actor so deletion can be re-derived safely."""
    return signing.dumps(
        {"view": view, "scope": scope, "actor": int(actor_id)},
        salt=BULK_SELECTION_SALT,
    )


def load_selection_token(token: str, *, view: str, actor_id: int) -> dict[str, Any]:
    """Return the normalized signed selection bound to this view and actor.

    The token is the only trusted source for the filter scope. A valid token
    signed for another view, another operator, or with a malformed scope is
    rejected so callers do not each re-implement the binding checks.

    Raises ``signing.BadSignature`` for missing/expired/tampered/foreign tokens
    and ``ValueError`` when the signed payload has the wrong shape.
    """

    selection = signing.loads(token, salt=BULK_SELECTION_SALT, max_age=SELECTION_MAX_AGE_SECONDS)
    if not isinstance(selection, dict):
        raise signing.BadSignature("Invalid selection")
    if selection.get("view") != view or selection.get("actor") != int(actor_id):
        raise signing.BadSignature("Selection is not valid for this view and operator")
    scope = selection.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("Selection scope is not an object")
    return {"view": view, "actor": int(actor_id), "scope": scope}


def bulk_delete_records(request, view: str) -> JsonResponse:
    """Delete exactly the signed-scope selection sent by the admin table."""
    if request.method != "DELETE":
        return JsonResponse({"error": _("Method not allowed")}, status=405)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Invalid JSON")}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": _("Invalid JSON")}, status=400)

    try:
        selection = load_selection_token(
            str(payload.get("selection_token") or ""),
            view=view,
            actor_id=request.user.pk,
        )
    except signing.BadSignature:
        return JsonResponse(
            {"error": _("The selection has expired. Reload the list and try again.")},
            status=409,
        )
    except ValueError:
        return JsonResponse({"error": _("The selection is not valid for this operator.")}, status=400)
    scope = selection["scope"]

    explicit_ids = payload.get("ids", [])
    excluded_ids = payload.get("excluded_ids", [])
    if not isinstance(explicit_ids, list) or not isinstance(excluded_ids, list):
        return JsonResponse({"error": _("The selection has invalid types.")}, status=400)
    def _strict_id(value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError
        if value <= 0 or value > 2_147_483_647:
            raise ValueError
        return value

    try:
        explicit_ids = [_strict_id(value) for value in explicit_ids]
        excluded_ids = {_strict_id(value) for value in excluded_ids}
    except (TypeError, ValueError):
        return JsonResponse({"error": _("The selection has invalid types.")}, status=400)
    if len(explicit_ids) > MAX_EXPLICIT_SELECTION or len(excluded_ids) > MAX_EXPLICIT_SELECTION:
        return JsonResponse(
            {"error": _("Select all matching records for selections larger than 1,000.")},
            status=400,
        )

    try:
        scoped = scope_queryset(view, scope)
    except (ValueError, TypeError):
        return JsonResponse({"error": _("The selection is not valid for this operator.")}, status=400)

    stale: list[int] = []
    try:
        select_all = parse_strict_bool(payload.get("select_all"))
    except ValueError:
        return JsonResponse({"error": _("The selection has invalid types.")}, status=400)
    if select_all:
        target = scoped.exclude(pk__in=excluded_ids)
        requested = scoped.count()
        allowed_count = target.count()
    else:
        explicit_ids = list(dict.fromkeys(explicit_ids))
        requested = len(explicit_ids)
        allowed_ids = set(scoped.filter(pk__in=explicit_ids).values_list("pk", flat=True))
        stale = [pk for pk in explicit_ids if pk not in allowed_ids]
        target = scoped.filter(pk__in=allowed_ids)
        allowed_count = len(allowed_ids)

    if allowed_count == 0:
        return JsonResponse({"deleted": 0, "related_deleted": 0, "requested": requested, "stale": stale})
    try:
        with transaction.atomic():
            deleted_total, _per_model = target.delete()
    except ProtectedError:
        return JsonResponse(
            {"error": _("Some selected records are referenced by other data and cannot be deleted.")},
            status=409,
        )
    return JsonResponse({
        "deleted": allowed_count,
        "related_deleted": max(int(deleted_total) - allowed_count, 0),
        "requested": requested,
        "stale": stale,
    })
