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
from django.db.models import Q, QuerySet
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.utils.translation import gettext as _

from .models import Course, CourseOffering, Lesson, Level, Quiz, Role, User
from .utils.search import normalized_contains_q, normalize_search_text

BULK_SELECTION_SALT = "bulk-table-selection"
SELECTION_MAX_AGE_SECONDS = 3600
MAX_EXPLICIT_SELECTION = 1000
TABLE_VIEWS = ("user", "course", "lesson", "quiz")


def _clean_search(value: str | None) -> str:
    return normalize_search_text(str(value or "").strip()[:100])


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def user_scope(request) -> dict[str, Any]:
    """Normalize the user dashboard filters into a JSON-safe scope."""
    search = _clean_search(request.GET.get("name"))
    raw_role = str(request.GET.get("filtering") or "").strip()[:64]
    role_code = ""
    role_value = ""
    if raw_role:
        for code, label in Role.ROLES:
            if raw_role == str(label):
                role_code = code
                role_value = raw_role
                break
    return {"search": search, "role": role_code, "role_value": role_value}


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
    return User.objects.filter(query)


def course_scope(request) -> dict[str, Any]:
    """Normalize the course dashboard filters into a JSON-safe scope."""
    search = _clean_search(request.GET.get("name"))
    raw_level = str(request.GET.get("filtering") or "").strip()[:64]
    level_ordering = None
    level_value = ""
    if raw_level:
        for level in Level.objects.order_by("ordering"):
            if level.display_name == raw_level:
                level_ordering = level.ordering
                level_value = raw_level
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
    return Course.objects.filter(query)


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
    return {"year": int(year_id), "search": search, "offering": offering_id}


def lesson_queryset(scope: dict[str, Any]) -> QuerySet:
    query = Q(course_offering__academic_year_level__academic_year_id=_int_or_none(scope.get("year")))
    search = str(scope.get("search") or "")
    if search:
        query &= normalized_contains_q(("name",), search)
    offering_id = _int_or_none(scope.get("offering"))
    if offering_id is not None:
        query &= Q(course_offering_id=offering_id)
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
    """Return the signed selection scope or raise signing.BadSignature."""
    selection = signing.loads(token, salt=BULK_SELECTION_SALT, max_age=SELECTION_MAX_AGE_SECONDS)
    if not isinstance(selection, dict):
        raise signing.BadSignature("Invalid selection")
    return selection


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
    if selection.get("view") != view:
        return JsonResponse({"error": _("The selection does not match this list.")}, status=400)
    if selection.get("actor") != request.user.pk:
        return JsonResponse({"error": _("The selection is not valid for this operator.")}, status=403)
    scope = selection.get("scope")
    if not isinstance(scope, dict):
        return JsonResponse({"error": _("The selection is not valid for this operator.")}, status=403)

    explicit_ids = payload.get("ids", [])
    excluded_ids = payload.get("excluded_ids", [])
    if not isinstance(explicit_ids, list) or not isinstance(excluded_ids, list):
        return JsonResponse({"error": _("The selection has invalid types.")}, status=400)
    try:
        explicit_ids = [int(value) for value in explicit_ids]
        excluded_ids = {int(value) for value in excluded_ids}
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
    select_all = bool(payload.get("select_all"))
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
