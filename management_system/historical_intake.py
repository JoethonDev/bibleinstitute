from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Iterable

from openpyxl import load_workbook

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.timezone import now
from django.utils.translation import gettext as _

from .academic_enrollment import promote_historical_summary
from .models import (
    AcademicYear,
    AcademicYearLevel,
    CourseOffering,
    Enrollment,
    HistoricalAcademicSummary,
    Role,
    User,
)


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
    "wpay_user_id",
    "wpay_login",
    "wpay_email",
    "wpay_display_name",
    "match_score",
    "match_review",
    "destination_academic_year",
    "destination_level",
    "promote_now",
    "failed_course_offering_ids",
    "promotion_reason",
    "admin_note",
)

MAX_INTAKE_ROWS = 1000
PROMOTABLE_OUTCOMES = frozenset({
    HistoricalAcademicSummary.Outcome.COMPLETED,
    HistoricalAcademicSummary.Outcome.PASSED,
    HistoricalAcademicSummary.Outcome.PARTIAL,
})


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


def parse_intake_upload(upload) -> tuple[list[dict[str, str]], str]:
    """Parse one reviewed CSV/XLSX upload without touching the database."""
    name = (upload.name or "").lower()
    content = upload.read()
    if len(content) > 10 * 1024 * 1024:
        raise ValidationError(_("The intake file is too large."))
    digest = hashlib.sha256(content).hexdigest()
    if name.endswith(".csv"):
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        headers = tuple(reader.fieldnames or ())
        rows = [{key: _text(value) for key, value in row.items()} for row in reader]
    elif name.endswith(".xlsx"):
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        if "Intake" not in workbook.sheetnames:
            raise ValidationError(_("The XLSX file must contain an Intake sheet."))
        rows_iter = workbook["Intake"].iter_rows(values_only=True)
        headers = tuple(_text(value) for value in next(rows_iter, ()))
        rows = [
            {_text(headers[index]): _text(value) for index, value in enumerate(values[:len(headers)])}
            for values in rows_iter
            if any(_text(value) for value in values)
        ]
    else:
        raise ValidationError(_("Upload a CSV or XLSX intake file."))
    expected_headers = INTAKE_COLUMNS
    # The approved operator templates carry explicit account fields. A future
    # admin-generated CSV may use the shorter manual form, but it must still
    # identify source scope and never rely on fuzzy WordPress suggestions.
    if headers != expected_headers:
        raise ValidationError(_("The intake file columns do not match the approved template."))
    if not rows or len(rows) > MAX_INTAKE_ROWS:
        raise ValidationError(_("The intake file must contain between 1 and 1,000 rows."))
    return rows, digest


def _find_user(row: dict[str, str]) -> User:
    identifiers = []
    if row.get("lms_user_id"):
        if not row["lms_user_id"].isdigit():
            raise ValidationError(_("LMS user ID must be a positive number."))
        identifiers.append(User.objects.filter(pk=int(row["lms_user_id"])))
    if row.get("lms_username"):
        identifiers.append(User.objects.filter(username=row["lms_username"]))
    if row.get("lms_email"):
        identifiers.append(User.objects.filter(email__iexact=row["lms_email"]))
    if not identifiers:
        raise ValidationError(_("Find mode requires an LMS user ID, username, or email."))
    candidates = None
    for queryset in identifiers:
        found = list(queryset[:2])
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


def _resolve_scope(scope_id: str, *, active: bool | None = None) -> AcademicYearLevel:
    if not scope_id or not scope_id.isdigit():
        raise ValidationError(_("Select a valid academic scope."))
    queryset = AcademicYearLevel.objects.select_related("academic_year", "level").filter(pk=int(scope_id))
    if active is not None:
        queryset = queryset.filter(academic_year__is_active=active)
    try:
        return queryset.get()
    except AcademicYearLevel.DoesNotExist as exc:
        raise ValidationError(_("The selected academic scope is not available.")) from exc


def _validate_row(row: dict[str, str], index: int) -> dict:
    source_name = _text(row.get("source_name"))
    if not source_name:
        raise ValidationError(_("Row %(row)d has no source student name.") % {"row": index})
    source_scope = _resolve_scope(row.get("source_academic_year_level", "")) if row.get("source_academic_year_level") else None
    if source_scope is None:
        source_year = _text(row.get("source_academic_year"))
        source_level = _text(row.get("source_level"))
        if not source_year or not source_level.isdigit():
            raise ValidationError(_("Row %(row)d must identify its source academic year and level.") % {"row": index})
        source_scope = AcademicYearLevel.objects.select_related("academic_year", "level").filter(
            academic_year__name=source_year,
            level__ordering=int(source_level),
        ).first()
        if source_scope is None:
            raise ValidationError(_("Row %(row)d has no matching source academic scope.") % {"row": index})
    outcome = _text(row.get("historical_outcome"))
    if outcome not in HistoricalAcademicSummary.Outcome.values:
        raise ValidationError(_("Row %(row)d has an invalid historical outcome.") % {"row": index})
    action = _text(row.get("account_action")).lower()
    if action not in {"find", "create"}:
        raise ValidationError(_("Row %(row)d must set account_action to find or create.") % {"row": index})
    student = _find_user(row) if action == "find" else None
    if action == "create":
        username = _text(row.get("lms_username"))
        if not username:
            raise ValidationError(_("Row %(row)d create mode requires lms_username.") % {"row": index})
        if User.objects.filter(username=username).exists():
            raise ValidationError(_("Row %(row)d create username already exists.") % {"row": index})
    promote = _text(row.get("promote_now")).lower() == "yes"
    destination = None
    failed_ids = _parse_ids(_text(row.get("failed_course_offering_ids")))
    if promote:
        if outcome not in PROMOTABLE_OUTCOMES:
            raise ValidationError(_("Row %(row)d cannot be promoted with this historical outcome.") % {"row": index})
        destination_year = _text(row.get("destination_academic_year"))
        destination_level = _text(row.get("destination_level"))
        is_last_level = not AcademicYearLevel.objects.filter(level__ordering__gt=source_scope.level.ordering).exists()
        no_destination_allowed = is_last_level and outcome in {
            HistoricalAcademicSummary.Outcome.COMPLETED,
            HistoricalAcademicSummary.Outcome.PASSED,
        }
        if not no_destination_allowed:
            if not destination_year or not destination_level.isdigit():
                raise ValidationError(_("Row %(row)d promotion requires destination academic year and level.") % {"row": index})
            destination = AcademicYearLevel.objects.select_related("academic_year", "level").filter(
                academic_year__name=destination_year,
                academic_year__is_active=True,
                level__ordering=int(destination_level),
            ).first()
            if destination is None:
                raise ValidationError(_("Row %(row)d has no matching active destination scope.") % {"row": index})
        if outcome == HistoricalAcademicSummary.Outcome.PARTIAL and not failed_ids:
            raise ValidationError(_("Row %(row)d partial promotion requires failed course offering IDs.") % {"row": index})
        if not _text(row.get("promotion_reason")):
            raise ValidationError(_("Row %(row)d manual promotion requires a reason.") % {"row": index})
    return {"row": row, "source_scope": source_scope, "destination": destination, "student": student, "promote": promote, "failed_ids": failed_ids}


def preview_intake_rows(rows: list[dict[str, str]]) -> list[dict]:
    results = []
    for index, row in enumerate(rows, start=2):
        try:
            plan = _validate_row(row, index)
            results.append({"row": index, "source_name": row.get("source_name", ""), "action": "valid", "account": plan["student"].username if plan["student"] else row.get("lms_username", "(new account)"), "message": _("Ready for review")})
        except ValidationError as exc:
            results.append({"row": index, "source_name": row.get("source_name", ""), "action": "error", "account": "", "message": "; ".join(exc.messages)})
    return results


def _create_or_get_user(row: dict[str, str], actor: User) -> User:
    if row.get("account_action") == "find":
        return _find_user(row)
    username = _text(row.get("lms_username"))
    if User.objects.filter(username=username).exists():
        raise ValidationError(_("The create username already exists."))
    first_name = _text(row.get("first_name"))
    last_name = _text(row.get("last_name"))
    if not first_name:
        first_name, last_name = _split_name(_text(row.get("source_name")))
    student_role = Role.objects.get(role="student")
    user = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=_text(row.get("lms_email")),
        role=student_role,
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
def intake_historical_row(*, row: dict[str, str], actor: User, source_key: str, source_file: str = "", source_row: int | None = None):
    plan = _validate_row(row, source_row or 1)
    student = _create_or_get_user(row, actor)
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
    created = []
    for offering in offerings:
        normal_exists = Enrollment.objects.filter(
            student=student,
            academic_year_level=offering.academic_year_level,
            course_offering__isnull=True,
            enrollment_type=Enrollment.Type.NORMAL,
            status=Enrollment.Status.ACTIVE,
        ).exists()
        if normal_exists:
            raise ValidationError(_("A full-year enrollment already grants this student's active scope."))
        enrollment, created_flag = Enrollment.objects.get_or_create(
            student=student,
            course_offering=offering,
            defaults={
                "academic_year_level": offering.academic_year_level,
                "enrollment_type": Enrollment.Type.REPEAT,
                "status": Enrollment.Status.ACTIVE,
                "enrolled_by": actor,
            },
        )
        if enrollment.academic_year_level_id != offering.academic_year_level_id:
            raise ValidationError(_("Exceptional enrollment scope does not match its offering."))
        if enrollment.enrollment_type == Enrollment.Type.NORMAL:
            raise ValidationError(_("An existing normal enrollment cannot be converted to exceptional access."))
        created.append(enrollment)
    return created
