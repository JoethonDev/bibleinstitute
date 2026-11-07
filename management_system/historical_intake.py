from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.db.models.functions import Lower
from django.utils.timezone import now
from django.utils.translation import gettext as _

from .academic_enrollment import promote_historical_summary
from .models import (
    AcademicYear,
    AcademicYearLevel,
    AdminOperationJob,
    CourseOffering,
    Enrollment,
    HistoricalAcademicSummary,
    Level,
    Role,
    User,
)
from .utils.decorators import can_manage_academic_setup
from .utils.validators import normalize_username


INTAKE_COLUMNS = (
    "source_name",
    "source_level",
    "source_academic_year",
    "historical_outcome",
    "account_action",
    "lms_username",
    "lms_email",
    "lms_user_id",
    "first_name",
    "last_name",
    "destination_academic_year",
    "destination_level",
    "promote_now",
    "failed_course_offering_ids",
    "promotion_reason",
    "admin_note",
)

MAX_INTAKE_ROWS = 1000
MAX_INTAKE_BYTES = 10 * 1024 * 1024
# Best-effort declared XLSX expansion budget (zip entries are read lazily).
MAX_INTAKE_EXPANDED_BYTES = 40 * 1024 * 1024
# One validation context covers a bounded page; per-row atomicity still uses
# the locked authoritative rows inside each transaction.
INTAKE_CHUNK_SIZE = 100
MAX_FROZEN_BYTES = 1_000_000
MAX_FIELD_LENGTH = 500
MAX_EXISTING_BULK_STUDENTS = 50
PROMOTABLE_OUTCOMES = frozenset({
    HistoricalAcademicSummary.Outcome.COMPLETED,
    HistoricalAcademicSummary.Outcome.PASSED,
    HistoricalAcademicSummary.Outcome.PARTIAL,
})


def build_existing_intake_row(
    *,
    user: User,
    source_scope: AcademicYearLevel,
    outcome: str,
    destination: AcademicYearLevel | None,
    promote: bool,
    failed_ids: list[int],
    reason: str,
    notes: str,
) -> dict[str, str]:
    """Build one canonical find-mode intake row for an existing student.

    The returned dict matches the shape consumed by `_validate_row()` and
    `intake_historical_row()`, so preview and apply always agree.
    """
    failed_text = ", ".join(str(value) for value in dict.fromkeys(int(value) for value in failed_ids))
    # Only the numeric user ID identifies the account: the bounded lookup loads
    # one user set per row page, so extra username/email copies would not add
    # safety and would only widen the ambiguous-identity surface.
    return {
        "source_name": user.get_full_name() or user.username,
        "source_level": str(source_scope.level.ordering),
        "source_academic_year": source_scope.academic_year.name,
        "historical_outcome": outcome,
        "account_action": "find",
        "lms_user_id": str(user.pk),
        "lms_username": "",
        "lms_email": "",
        "destination_academic_year": destination.academic_year.name if destination else "",
        "destination_level": str(destination.level.ordering) if destination else "",
        "promote_now": "yes" if promote else "no",
        "failed_course_offering_ids": failed_text,
        "promotion_reason": reason,
        "admin_note": notes,
    }


def summaries_for_users_scopes(users: Iterable[User], scopes: Iterable[AcademicYearLevel]) -> dict[tuple[int, int], HistoricalAcademicSummary]:
    """Return existing summaries keyed by (user_id, scope_id) in one query."""
    user_ids = [user.pk for user in users]
    scope_ids = list(dict.fromkeys(scope.pk for scope in scopes))
    if not user_ids or not scope_ids:
        return {}
    found = HistoricalAcademicSummary.objects.filter(
        student_id__in=user_ids,
        academic_year_level_id__in=scope_ids,
    ).select_related("student")
    return {(summary.student_id, summary.academic_year_level_id): summary for summary in found}


def _text(value) -> str:
    return str(value or "").strip()


def _split_name(name: str) -> tuple[str, str]:
    parts = _text(name).split()
    return (parts[0], " ".join(parts[1:])) if parts else ("Historical", "Student")


def _parse_ids(value: str) -> list[int]:
    if not value:
        return []
    values = []
    for raw in value.replace(";", ",").split(","):
        raw = raw.strip()
        if not raw:
            continue
        if not raw.isdigit() or int(raw) <= 0:
            raise ValidationError(_("Failed course offering IDs must be positive numbers separated by commas."))
        values.append(int(raw))
    return list(dict.fromkeys(values))


def _read_bounded_upload(upload, *, max_bytes: int = MAX_INTAKE_BYTES) -> bytes:
    """Read at most ``max_bytes`` after rejecting an oversized declaration."""

    declared = getattr(upload, "size", None)
    if isinstance(declared, int) and declared > max_bytes:
        raise ValidationError(_("The intake file is too large."))
    content = upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValidationError(_("The intake file is too large."))
    return content


def _require_rows(rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValidationError(_("The intake file must contain between 1 and 1,000 rows."))


def _bounded_rows(values_iterator):
    """Consume at most ``MAX_INTAKE_ROWS`` non-empty rows from an iterator."""

    rows = []
    for values in values_iterator:
        if not any(_text(value) for value in values):
            continue
        if len(rows) >= MAX_INTAKE_ROWS:
            raise ValidationError(_("The intake file must contain between 1 and 1,000 rows."))
        rows.append(values)
    _require_rows(rows)
    return rows


def parse_intake_upload(upload) -> tuple[list[dict[str, str]], str]:
    """Parse one reviewed CSV/XLSX upload without touching the database.

    The byte budget is enforced before reading, headers are validated before
    any row is materialized, and at most ``MAX_INTAKE_ROWS`` rows are consumed.
    """

    name = (upload.name or "").lower()
    content = _read_bounded_upload(upload)
    digest = hashlib.sha256(content).hexdigest()
    expected_headers = INTAKE_COLUMNS
    if name.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
            reader = csv.reader(io.StringIO(text))
            headers = tuple(next(reader, ()))
            if headers != expected_headers:
                raise ValidationError(_("The intake file columns do not match the approved template."))
            values = _bounded_rows(reader)
        except csv.Error as exc:
            raise ValidationError(_("The intake file could not be read.")) from exc
        rows = [
            {expected_headers[index]: _text(value) for index, value in enumerate(item[:len(expected_headers)])}
            for item in values
        ]
    elif name.endswith(".xlsx"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                declared = sum(entry.file_size for entry in archive.infolist())
            if declared > MAX_INTAKE_EXPANDED_BYTES:
                raise ValidationError(_("The intake file is too large."))
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except (zipfile.BadZipFile, InvalidFileException, OSError, KeyError) as exc:
            raise ValidationError(_("The intake file could not be read.")) from exc
        try:
            if "Intake" not in workbook.sheetnames:
                raise ValidationError(_("The XLSX file must contain an Intake sheet."))
            rows_iter = workbook["Intake"].iter_rows(values_only=True)
            headers = tuple(_text(value) for value in next(rows_iter, ()))
            if headers != expected_headers:
                raise ValidationError(_("The intake file columns do not match the approved template."))
            values = _bounded_rows(rows_iter)
        finally:
            workbook.close()
        rows = [
            {expected_headers[index]: _text(value) for index, value in enumerate(item[:len(expected_headers)])}
            for item in values
        ]
    else:
        raise ValidationError(_("Upload a CSV or XLSX intake file."))
    return rows, digest


def normalize_intake_row(row: dict) -> dict[str, str]:
    """Return the bounded canonical cell set frozen on the durable job."""

    return {
        column: _text(row.get(column))[:MAX_FIELD_LENGTH]
        for column in INTAKE_COLUMNS
    }


@dataclass(frozen=True)
class IntakeLookups:
    """Bounded preloaded facts for validating one page of intake rows.

    One context per bounded row page replaces the previous per-row
    user/scope/destination queries. Apply still locks the authoritative rows
    inside each per-row transaction.
    """

    source_scopes_by_id: dict[int, AcademicYearLevel]
    source_scopes_by_year_level: dict[tuple[str, int], AcademicYearLevel]
    destination_scopes_by_year_level: dict[tuple[str, int], AcademicYearLevel]
    users_by_id: dict[int, User]
    users_by_username: dict[str, User]
    users_by_email: dict[str, list[User]]
    max_level_ordering: int
    student_role: Role | None

    @classmethod
    def from_rows(cls, rows: Iterable[dict]) -> "IntakeLookups":
        source_ids: set[int] = set()
        scope_pairs: set[tuple[str, int]] = set()
        destination_pairs: set[tuple[str, int]] = set()
        user_ids: set[int] = set()
        usernames: set[str] = set()
        emails: set[str] = set()
        for row in rows:
            scope_id = _text(row.get("source_academic_year_level"))
            if scope_id.isdigit():
                source_ids.add(int(scope_id))
            source_year = _text(row.get("source_academic_year"))
            source_level = _text(row.get("source_level"))
            if source_year and source_level.isdigit():
                scope_pairs.add((source_year, int(source_level)))
            destination_year = _text(row.get("destination_academic_year"))
            destination_level = _text(row.get("destination_level"))
            if destination_year and destination_level.isdigit():
                destination_pairs.add((destination_year, int(destination_level)))
            user_id = _text(row.get("lms_user_id"))
            if user_id.isdigit():
                user_ids.add(int(user_id))
            username = normalize_username(_text(row.get("lms_username")))
            if username:
                usernames.add(username)
            email = _text(row.get("lms_email"))
            if email:
                emails.add(email.lower())

        year_names = {year for year, _level in scope_pairs | destination_pairs}
        scopes = []
        if source_ids or year_names:
            scope_filter = Q(academic_year__name__in=year_names)
            if source_ids:
                scope_filter |= Q(pk__in=source_ids)
            scopes = list(
                AcademicYearLevel.objects.select_related("academic_year", "level")
                .filter(scope_filter)
                .order_by("pk")
            )
        users = []
        if user_ids or usernames or emails:
            user_filter = Q(username__in=usernames)
            if user_ids:
                user_filter |= Q(pk__in=user_ids)
            if emails:
                user_filter |= Q(email_lower__in=emails)
            users = list(
                User.objects.select_related("role")
                .annotate(email_lower=Lower("email"))
                .filter(user_filter)
                .order_by("pk")
            )
        users_by_email: dict[str, list[User]] = {}
        for user in users:
            if user.email:
                users_by_email.setdefault(user.email.lower(), []).append(user)
        highest_ordering = Level.objects.aggregate(highest=Max("ordering"))["highest"] or 0
        return cls(
            source_scopes_by_id={scope.pk: scope for scope in scopes},
            source_scopes_by_year_level={
                (scope.academic_year.name, scope.level.ordering): scope for scope in scopes
            },
            destination_scopes_by_year_level={
                (scope.academic_year.name, scope.level.ordering): scope
                for scope in scopes
                if scope.academic_year.is_active
            },
            users_by_id={user.pk: user for user in users},
            users_by_username={user.username: user for user in users},
            users_by_email=users_by_email,
            max_level_ordering=highest_ordering,
            student_role=Role.objects.filter(role="student").first(),
        )

    def source_scope(self, row: dict[str, str], index: int) -> AcademicYearLevel:
        scope_id = _text(row.get("source_academic_year_level"))
        if scope_id:
            if not scope_id.isdigit():
                raise ValidationError(_("Select a valid academic scope."))
            scope = self.source_scopes_by_id.get(int(scope_id))
            if scope is None:
                raise ValidationError(_("The selected academic scope is not available."))
            return scope
        source_year = _text(row.get("source_academic_year"))
        source_level = _text(row.get("source_level"))
        if not source_year or not source_level.isdigit():
            raise ValidationError(_("Row %(row)d must identify its source academic year and level.") % {"row": index})
        scope = self.source_scopes_by_year_level.get((source_year, int(source_level)))
        if scope is None:
            raise ValidationError(_("Row %(row)d has no matching source academic scope.") % {"row": index})
        return scope

    def destination_scope(self, row: dict[str, str], index: int) -> AcademicYearLevel:
        destination_year = _text(row.get("destination_academic_year"))
        destination_level = _text(row.get("destination_level"))
        if not destination_year or not destination_level.isdigit():
            raise ValidationError(_("Row %(row)d promotion requires destination academic year and level.") % {"row": index})
        scope = self.destination_scopes_by_year_level.get((destination_year, int(destination_level)))
        if scope is None:
            raise ValidationError(_("Row %(row)d has no matching active destination scope.") % {"row": index})
        return scope

    def is_last_level(self, scope: AcademicYearLevel) -> bool:
        return scope.level.ordering >= self.max_level_ordering

    def find_user(self, row: dict[str, str]) -> User:
        identifiers = []
        raw_id = _text(row.get("lms_user_id"))
        if raw_id:
            if not raw_id.isdigit():
                raise ValidationError(_("LMS user ID must be a positive number."))
            found = [self.users_by_id[int(raw_id)]] if int(raw_id) in self.users_by_id else []
            identifiers.append(found)
        raw_username = _text(row.get("lms_username"))
        if raw_username:
            username = normalize_username(raw_username)
            found = [self.users_by_username[username]] if username in self.users_by_username else []
            identifiers.append(found)
        raw_email = _text(row.get("lms_email"))
        if raw_email:
            identifiers.append(self.users_by_email.get(raw_email.lower(), []))
        if not identifiers:
            raise ValidationError(_("Find mode requires an LMS user ID, username, or email."))
        candidates = None
        for found in identifiers:
            if len(found) > 1:
                raise ValidationError(_("The LMS identity matches more than one account."))
            if found:
                if candidates is None:
                    candidates = found[0]
                elif candidates.pk != found[0].pk:
                    raise ValidationError(_("The LMS identity fields refer to different accounts."))
        if candidates is None:
            raise ValidationError(_("No LMS account matches the supplied identity."))
        if not candidates.role or candidates.role.role != "student":
            raise ValidationError(_("The selected LMS account is not a student account."))
        return candidates


def _validate_row(row: dict[str, str], index: int, lookups: IntakeLookups | None = None) -> dict:
    # Direct single-row callers (manual form, focused probes) build one bounded
    # context; the preview/apply paths always pass the page context.
    lookups = lookups or IntakeLookups.from_rows([row])
    source_name = _text(row.get("source_name"))
    if not source_name:
        raise ValidationError(_("Row %(row)d has no source student name.") % {"row": index})
    source_scope = lookups.source_scope(row, index)
    outcome = _text(row.get("historical_outcome"))
    if outcome not in HistoricalAcademicSummary.Outcome.values:
        raise ValidationError(_("Row %(row)d has an invalid historical outcome.") % {"row": index})
    action = _text(row.get("account_action")).lower()
    if action not in {"find", "create"}:
        raise ValidationError(_("Row %(row)d must set account_action to find or create.") % {"row": index})
    student = lookups.find_user(row) if action == "find" else None
    if action == "create":
        username = normalize_username(_text(row.get("lms_username")))
        if not username:
            raise ValidationError(_("Row %(row)d create mode requires lms_username.") % {"row": index})
        if username in lookups.users_by_username:
            raise ValidationError(_("Row %(row)d create username already exists.") % {"row": index})
    promote = _text(row.get("promote_now")).lower() == "yes"
    destination = None
    failed_ids = _parse_ids(_text(row.get("failed_course_offering_ids")))
    if promote:
        if outcome not in PROMOTABLE_OUTCOMES:
            raise ValidationError(_("Row %(row)d cannot be promoted with this historical outcome.") % {"row": index})
        no_destination_allowed = lookups.is_last_level(source_scope) and outcome in {
            HistoricalAcademicSummary.Outcome.COMPLETED,
            HistoricalAcademicSummary.Outcome.PASSED,
        }
        if not no_destination_allowed:
            destination = lookups.destination_scope(row, index)
        if outcome == HistoricalAcademicSummary.Outcome.PARTIAL and not failed_ids:
            raise ValidationError(_("Row %(row)d partial promotion requires failed course offering IDs.") % {"row": index})
        if not _text(row.get("promotion_reason")):
            raise ValidationError(_("Row %(row)d manual promotion requires a reason.") % {"row": index})
    return {
        "row": row,
        "action": action,
        "source_scope": source_scope,
        "destination": destination,
        "student": student,
        "promote": promote,
        "failed_ids": failed_ids,
    }


def preview_intake_rows(rows: list[dict[str, str]]) -> list[dict]:
    """Validate one upload in bounded pages without writing anything."""

    results = []
    for start in range(0, len(rows), INTAKE_CHUNK_SIZE):
        chunk = rows[start:start + INTAKE_CHUNK_SIZE]
        lookups = IntakeLookups.from_rows(chunk)
        for offset, row in enumerate(chunk, start=start):
            index = offset + 2
            try:
                plan = _validate_row(row, index, lookups)
                results.append({"row": index, "source_name": row.get("source_name", ""), "action": "valid", "account": plan["student"].username if plan["student"] else normalize_username(row.get("lms_username", "")) or "(new account)", "message": _("Ready for review")})
            except ValidationError as exc:
                results.append({"row": index, "source_name": row.get("source_name", ""), "action": "error", "account": "", "message": "; ".join(exc.messages)})
    return results


def enqueue_historical_intake(
    *,
    rows: list[dict],
    actor: User,
    source_file: str,
    digest: str,
    language: str = "ar",
) -> AdminOperationJob:
    """Freeze validated rows and queue one durable per-row historical intake.

    The normalized rows and the upload digest are frozen before enqueue, so a
    duplicate submit of the same file returns the same active job and a replay
    reuses the same per-row source keys.
    """

    from .admin_operations import create_admin_operation

    normalized = [normalize_intake_row(row) for row in rows]
    _require_rows(normalized)
    if len(normalized) > MAX_INTAKE_ROWS:
        raise ValidationError(_("The intake file must contain between 1 and 1,000 rows."))
    if not digest:
        raise ValidationError(_("The intake file could not be identified."))
    if len(json.dumps(normalized, ensure_ascii=False)) > MAX_FROZEN_BYTES:
        raise ValidationError(_("The intake file is too large."))
    return create_admin_operation(
        actor=actor,
        kind=AdminOperationJob.Kind.HISTORICAL_INTAKE,
        input={
            "rows": normalized,
            "digest": digest[:64],
            "source_file": (source_file or "")[:255],
            "row_count": len(normalized),
        },
        conflict_key=f"historical-intake:{digest[:64]}",
        language=language,
        total=len(normalized),
    )


def execute_historical_intake(job, token) -> dict:
    """J01 executor: apply frozen rows with per-row atomic, idempotent intake."""

    from .admin_operations import (
        MAX_ROW_ERRORS,
        AdminOperationError,
        assert_admin_operation_progress,
    )

    from .admin_operations import require_admin_operation_actor

    require_admin_operation_actor(job)
    if not can_manage_academic_setup(job.actor):
        raise AdminOperationError(
            "not_permitted",
            _("You are no longer allowed to run historical intake."),
        )
    payload = job.input or {}
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_INTAKE_ROWS:
        raise AdminOperationError("invalid_input", _("The frozen intake input is not valid."))
    digest = str(payload.get("digest") or "")
    source_file = str(payload.get("source_file") or "")[:255]
    if not digest:
        raise AdminOperationError("invalid_input", _("The frozen intake input is not valid."))

    cursor_index = max(0, int((job.cursor or {}).get("index", 0) or 0))
    succeeded = int(job.succeeded or 0)
    failed = int(job.failed or 0)
    skipped = int(job.skipped or 0)
    errors = list((job.result or {}).get("row_errors") or [])[:MAX_ROW_ERRORS]
    processed_rows = list((job.result or {}).get("rows") or [])[:MAX_ROW_ERRORS]
    total = len(rows)

    for chunk_start in range(cursor_index, total, INTAKE_CHUNK_SIZE):
        chunk_end = min(chunk_start + INTAKE_CHUNK_SIZE, total)
        chunk = rows[chunk_start:chunk_end]
        lookups = IntakeLookups.from_rows(chunk)
        for index in range(chunk_start, chunk_end):
            row_number = index + 2
            row = chunk[index - chunk_start]
            try:
                with transaction.atomic():
                    intake_historical_row(
                        row=row,
                        actor=job.actor,
                        source_key=f"upload:{digest}:{row_number}",
                        source_file=source_file,
                        source_row=row_number,
                        lookups=lookups,
                    )
                    succeeded += 1
                    if len(processed_rows) < MAX_ROW_ERRORS:
                        processed_rows.append({"row": row_number, "action": "saved"})
                    assert_admin_operation_progress(
                        job.pk,
                        token,
                        processed=index + 1,
                        succeeded=succeeded,
                        failed=failed,
                        skipped=skipped,
                        total=total,
                        cursor={"index": index + 1},
                        result={"row_errors": errors, "rows": processed_rows},
                    )
            except (ValidationError, User.DoesNotExist, PermissionDenied) as exc:
                failed += 1
                message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
                if len(errors) < MAX_ROW_ERRORS:
                    errors.append({"row": row_number, "message": message[:300]})
                with transaction.atomic():
                    assert_admin_operation_progress(
                        job.pk,
                        token,
                        processed=index + 1,
                        succeeded=succeeded,
                        failed=failed,
                        skipped=skipped,
                        total=total,
                        cursor={"index": index + 1},
                        result={"row_errors": errors, "rows": processed_rows},
                    )

    status = AdminOperationJob.Status.PARTIAL if failed else AdminOperationJob.Status.COMPLETED
    return {
        "status": status,
        "result": {
            "saved": succeeded,
            "failed": failed,
            "skipped": skipped,
            "row_errors": errors,
            "rows": processed_rows,
        },
    }


def _create_or_get_user(plan: dict, lookups: IntakeLookups | None = None) -> User:
    """Consume the validated plan: the resolved find account or a new account.

    ``intake_historical_row`` locks the find account itself before applying, so
    this helper returns the already-resolved plan object for find mode.
    """

    if plan["action"] == "find":
        if plan["student"] is None:
            raise ValidationError(_("No LMS account matches the supplied identity."))
        return plan["student"]
    lookups = lookups or IntakeLookups.from_rows([plan["row"]])
    row = plan["row"]
    username = normalize_username(_text(row.get("lms_username")))
    if User.objects.filter(username=username).exists():
        raise ValidationError(_("The create username already exists."))
    first_name = _text(row.get("first_name"))
    last_name = _text(row.get("last_name"))
    if not first_name:
        first_name, last_name = _split_name(_text(row.get("source_name")))
    if lookups.student_role is None:
        raise ValidationError(_("The student role is not configured."))
    user = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=_text(row.get("lms_email")),
        role=lookups.student_role,
        is_active=True,
        application_status="active",
    )
    # Shared fixed password for operator-created historical-intake accounts;
    # set_password hashes it before storage, so plaintext is never persisted.
    user.set_password("123456789")
    user.full_clean()
    user.save()
    return user


@transaction.atomic
def intake_historical_row(
    *,
    row: dict[str, str],
    actor: User,
    source_key: str,
    source_file: str = "",
    source_row: int | None = None,
    lookups: IntakeLookups | None = None,
):
    lookups = lookups or IntakeLookups.from_rows([row])
    plan = _validate_row(row, source_row or 1, lookups)
    if plan["action"] == "find":
        student = plan["student"]
        # Apply must lock the freshly resolved authoritative account and
        # recheck the current role instead of trusting the preloaded page.
        locked = User.objects.select_for_update().only("pk", "role_id").get(pk=student.pk)
        if lookups.student_role is None or locked.role_id != lookups.student_role.pk:
            raise ValidationError(_("The selected LMS account is not a student account."))
    else:
        student = _create_or_get_user(plan, lookups)
    source_scope = AcademicYearLevel.objects.select_for_update().get(pk=plan["source_scope"].pk)
    summary = HistoricalAcademicSummary.objects.select_for_update().filter(source_key=source_key).first()
    if summary is None:
        summary = HistoricalAcademicSummary.objects.select_for_update().filter(
            student=student,
            academic_year_level=source_scope,
        ).first()
    if summary is None:
        summary = HistoricalAcademicSummary.objects.create(
            source_key=source_key,
            student=student,
            academic_year_level=source_scope,
            source_name=_text(row.get("source_name")),
            source_file=source_file,
            source_row=source_row,
            outcome=row["historical_outcome"],
        )
    else:
        if summary.student_id != student.pk or summary.academic_year_level_id != source_scope.pk:
            raise ValidationError(_("This source row is already linked to a different student or academic scope."))
        if summary.promoted_at:
            raise ValidationError(_("This historical summary has already been promoted."))
        summary.outcome = row["historical_outcome"]
        summary.source_key = source_key
        summary.source_file = source_file
        summary.source_row = source_row
    summary.notes = _text(row.get("admin_note"))
    summary.reviewed_by = actor
    summary.reviewed_at = now()
    summary.certificate_eligible = False
    summary.save()
    enrollment, created = Enrollment.objects.select_for_update().get_or_create(
        student=student,
        academic_year_level=source_scope,
        course_offering=None,
        defaults={"enrollment_type": Enrollment.Type.NORMAL, "status": Enrollment.Status.COMPLETED, "enrolled_by": actor},
    )
    if enrollment.enrollment_type != Enrollment.Type.NORMAL:
        raise ValidationError(_("The historical source enrollment is not a normal enrollment."))
    if enrollment.status == Enrollment.Status.WITHDRAWN:
        raise ValidationError(_("The historical source enrollment is withdrawn."))
    if enrollment.status != Enrollment.Status.COMPLETED:
        enrollment.status = Enrollment.Status.COMPLETED
        enrollment.enrolled_by = enrollment.enrolled_by or actor
        enrollment.save(update_fields=["status", "enrolled_by"])
    if plan["promote"]:
        promote_historical_summary(
            summary_id=summary.pk,
            destination_scope_id=plan["destination"].pk if plan["destination"] else None,
            exceptional_offering_ids=plan["failed_ids"],
            reason=_text(row.get("promotion_reason")),
            actor=actor,
        )
    return summary


@transaction.atomic
def assign_exceptional_courses(*, student_id: int, offering_ids: Iterable[int], actor: User) -> list[Enrollment]:
    User.objects.select_for_update().get(pk=student_id)
    student = User.objects.select_related("role").get(pk=student_id)
    if not student.role or student.role.role != "student":
        raise ValidationError(_("Exceptional courses can only be assigned to students."))
    active_years = list(AcademicYear.objects.filter(is_active=True).values_list("pk", flat=True))
    if len(active_years) != 1:
        raise ValidationError(_("Exactly one active academic year is required."))
    ids = list(dict.fromkeys(int(value) for value in offering_ids))
    if not ids or len(ids) > 100:
        raise ValidationError(_("Select between one and 100 exceptional course offerings."))
    offerings = list(CourseOffering.objects.select_for_update().select_related("academic_year_level").filter(
        pk__in=ids,
        academic_year_level__academic_year_id=active_years[0],
        status="published",
    ))
    if len(offerings) != len(ids):
        raise ValidationError(_("Every selected course must be a published offering in the active academic year."))
    # Bounded preloads: one normal-enrollment scope query and one existing
    # exceptional-enrollment query replace the per-offering exists+get_or_create.
    normal_scope_ids = set(
        Enrollment.objects.filter(
            student=student,
            academic_year_level_id__in={offering.academic_year_level_id for offering in offerings},
            course_offering__isnull=True,
            enrollment_type=Enrollment.Type.NORMAL,
            status=Enrollment.Status.ACTIVE,
        ).values_list("academic_year_level_id", flat=True)
    )
    if normal_scope_ids:
        raise ValidationError(_("A full-year enrollment already grants this student's active scope."))
    existing = {
        enrollment.course_offering_id: enrollment
        for enrollment in Enrollment.objects.select_for_update().filter(
            student=student,
            course_offering_id__in=ids,
        )
    }
    resolved: dict[int, Enrollment] = {}
    pending: list[Enrollment] = []
    for offering in offerings:
        enrollment = existing.get(offering.pk)
        if enrollment is None:
            pending.append(Enrollment(
                student=student,
                course_offering=offering,
                academic_year_level=offering.academic_year_level,
                enrollment_type=Enrollment.Type.REPEAT,
                status=Enrollment.Status.ACTIVE,
                enrolled_by=actor,
            ))
            continue
        if enrollment.academic_year_level_id != offering.academic_year_level_id:
            raise ValidationError(_("Exceptional enrollment scope does not match its offering."))
        if enrollment.enrollment_type == Enrollment.Type.NORMAL:
            raise ValidationError(_("An existing normal enrollment cannot be converted to exceptional access."))
        resolved[offering.pk] = enrollment
    if pending:
        Enrollment.objects.bulk_create(pending)
        for enrollment in pending:
            resolved[enrollment.course_offering_id] = enrollment
    return [resolved[offering.pk] for offering in offerings]
