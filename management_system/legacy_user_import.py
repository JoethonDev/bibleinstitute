"""Durable legacy user CSV import.

One bounded private artifact, whole-file validation and an all-or-nothing
apply. This is deliberately separate from historical academic intake: any
invalid row creates zero accounts, and the file is hashed and applied on the
admin worker rather than inside the request.
"""

from __future__ import annotations

import csv
import hashlib
import io

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from .models import AdminOperationJob, Role, User
from .utils.decorators import can_manage_applications
from .utils.validators import normalize_username

LEGACY_IMPORT_MAX_BYTES = 2 * 1024 * 1024
LEGACY_IMPORT_MAX_ROWS = 1000
LEGACY_IMPORT_COLUMNS = 5
MAX_IMPORT_ERRORS = 100
# Placeholder used by the pre-enqueue check: the real field validators run
# again in the worker where the supplied password is hashed before apply.
_PENDING_PASSWORD = "!"


def read_legacy_import_upload(upload) -> bytes:
    """Read at most the endpoint byte budget after rejecting early."""

    declared = getattr(upload, "size", None)
    if isinstance(declared, int) and declared > LEGACY_IMPORT_MAX_BYTES:
        raise ValidationError(_("The user CSV file is too large."))
    content = upload.read(LEGACY_IMPORT_MAX_BYTES + 1)
    if len(content) > LEGACY_IMPORT_MAX_BYTES:
        raise ValidationError(_("The user CSV file is too large."))
    if not content.strip():
        raise ValidationError(_("The user CSV file is empty."))
    return content


def validate_legacy_import_rows(content: bytes, *, hash_passwords: bool) -> dict:
    """Parse and validate the legacy five-column CSV without writing accounts.

    ``hash_passwords`` is False for the bounded pre-enqueue check, which must
    not spend per-row password hashing in a web request, and True in the worker,
    where the authoritative pass hashes every password before the atomic apply.
    Row errors are bounded; the returned ``rows`` are only applied when
    ``errors`` is empty.
    """

    try:
        text = content.decode("utf-8-sig")
    except (UnicodeDecodeError, LookupError) as exc:
        raise ValidationError(_("The user CSV file must be UTF-8 encoded.")) from exc
    roles = {role.role: role for role in Role.objects.all()}
    parsed = []
    line_number = 1
    try:
        reader = csv.reader(io.StringIO(text))
        next(reader, None)  # Header row, exactly like the original endpoint.
        for values in reader:
            line_number += 1
            if not values or not any(str(value).strip() for value in values):
                continue
            if len(parsed) >= LEGACY_IMPORT_MAX_ROWS:
                raise ValidationError(
                    _("The user CSV file must contain at most 1,000 data rows.")
                )
            parsed.append((line_number, values))
    except csv.Error as exc:
        raise ValidationError(_("The user CSV file could not be read.")) from exc
    if not parsed:
        raise ValidationError(_("The user CSV file has no data rows."))

    errors: list[dict] = []
    error_count = 0

    def add_error(line: int, message: str) -> None:
        nonlocal error_count
        error_count += 1
        if len(errors) < MAX_IMPORT_ERRORS:
            errors.append({"line": line, "message": message[:300]})

    shaped = []
    usernames = []
    for line, values in parsed:
        if len(values) != LEGACY_IMPORT_COLUMNS:
            add_error(line, _("Line %(line)d: Incorrect number of columns. Expected 5, got %(count)d.") % {
                "line": line,
                "count": len(values),
            })
            continue
        username = normalize_username(values[0])
        if not username:
            add_error(line, _("Line %(line)d: Username is required.") % {"line": line})
            continue
        shaped.append((line, username, values))
        usernames.append(username)

    existing = set(
        User.objects.filter(username__in=usernames).values_list("username", flat=True)
    )
    seen: set[str] = set()
    rows = []
    for line, username, values in shaped:
        if username in existing:
            add_error(line, _("Line %(line)d: User '%(username)s' already exists.") % {
                "line": line,
                "username": username,
            })
            continue
        if username in seen:
            add_error(line, _("Line %(line)d: User '%(username)s' appears more than once in this file.") % {
                "line": line,
                "username": username,
            })
            continue
        role_name = values[4].lower().strip()
        role = roles.get(role_name)
        if role is None:
            add_error(line, _("Line %(line)d: Role '%(role)s' does not exist.") % {
                "line": line,
                "role": values[4],
            })
            continue
        first_name = values[1].strip()
        last_name = values[2].strip()
        password = make_password(values[3]) if hash_passwords else _PENDING_PASSWORD
        user = User(
            username=username,
            first_name=first_name,
            last_name=last_name,
            role=role,
            password=password,
        )
        # Field-level validation only: canonical identity, role existence and
        # DB constraints are enforced at apply, so validating a bounded file
        # stays at a constant query count instead of four queries per row.
        try:
            for field_name in ("username", "first_name", "last_name", "password"):
                field = user._meta.get_field(field_name)
                field.clean(getattr(user, field_name), user)
        except ValidationError as exc:
            add_error(line, _("Line %(line)d: Validation error for user '%(username)s': %(errors)s") % {
                "line": line,
                "username": username,
                "errors": ", ".join(exc.messages),
            })
            continue
        seen.add(username)
        rows.append({
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "password": user.password,
            "role": role.role,
            "line": line,
        })
    return {
        "rows": rows,
        "errors": errors,
        "error_count": error_count,
        "parsed": len(parsed),
    }


def enqueue_legacy_user_import(
    *,
    actor: User,
    content: bytes,
    filename: str,
    language: str = "ar",
):
    """Store the bounded file privately and queue the all-or-nothing import.

    Returns ``(job, error)`` where ``error`` is a translated message when the
    private artifact could not be stored (no job or account is created then).
    A repeated submit of identical content returns the same active job.
    """

    from .admin_operations import (
        AdminOperationError,
        active_admin_operation,
        create_admin_operation,
        delete_admin_operation_artifact,
        store_admin_operation_artifact,
    )

    digest = hashlib.sha256(content).hexdigest()
    conflict_key = f"legacy-user-import:{digest[:64]}"
    existing = active_admin_operation(actor, AdminOperationJob.Kind.LEGACY_USER_IMPORT, conflict_key)
    if existing is not None:
        return existing, None
    try:
        artifact_key = store_admin_operation_artifact(content=content, filename=filename)
    except AdminOperationError as exc:
        return None, exc.message
    job = create_admin_operation(
        actor=actor,
        kind=AdminOperationJob.Kind.LEGACY_USER_IMPORT,
        input={
            "artifact_key": artifact_key,
            "digest": digest,
            "filename": (filename or "")[:255],
        },
        conflict_key=conflict_key,
        language=language,
    )
    if (job.input or {}).get("artifact_key") != artifact_key:
        delete_admin_operation_artifact(job, key=artifact_key)
    return job, None


def apply_legacy_user_rows(rows: list[dict]) -> int:
    """Create every validated account in one all-or-nothing transaction."""

    role_names = {row["role"] for row in rows}
    roles = {role.role: role for role in Role.objects.filter(role__in=role_names)}
    if len(roles) != len(role_names):
        raise ValidationError(_("One or more roles no longer exist."))
    users = [
        User(
            username=row["username"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            password=row["password"],
            role=roles[row["role"]],
        )
        for row in rows
    ]
    with transaction.atomic():
        User.objects.bulk_create(users, batch_size=500)
    return len(users)


def execute_legacy_user_import(job, token) -> dict:
    """J01 executor: validate the private file, hash, then apply or create none."""

    from .admin_operations import (
        MAX_ROW_ERRORS,
        AdminOperationError,
        delete_admin_operation_artifact,
        read_admin_operation_artifact,
        assert_admin_operation_progress,
    )

    from .admin_operations import require_admin_operation_actor

    require_admin_operation_actor(job)
    if not can_manage_applications(job.actor):
        raise AdminOperationError(
            "not_permitted",
            _("You are no longer allowed to import user accounts."),
        )
    payload = job.input or {}
    digest = str(payload.get("digest") or "")
    if not digest:
        raise AdminOperationError("invalid_input", _("The frozen import input is not valid."))
    cursor = job.cursor or {}
    if cursor.get("phase") == "applied":
        # The accounts committed with this marker; a stale/duplicate worker
        # must finish without replaying the import.
        delete_admin_operation_artifact(job)
        return {
            "status": AdminOperationJob.Status.COMPLETED,
            "result": {"created": int(cursor.get("created", 0) or 0), "row_errors": []},
        }

    content = read_admin_operation_artifact(job, expected_digest=digest)
    parsed = validate_legacy_import_rows(content, hash_passwords=True)
    total = int(parsed["parsed"])
    if parsed["errors"]:
        errors = list(parsed["errors"])[:MAX_ROW_ERRORS]
        assert_admin_operation_progress(
            job.pk,
            token,
            processed=total,
            succeeded=0,
            failed=int(parsed["error_count"]),
            skipped=0,
            total=total,
            cursor={"phase": "invalid"},
            result={"row_errors": errors},
        )
        delete_admin_operation_artifact(job)
        raise AdminOperationError(
            "invalid_rows",
            _("No users were created because the file has %(count)d invalid row(s). Correct the file and try again.")
            % {"count": int(parsed["error_count"])},
        )

    assert_admin_operation_progress(
        job.pk,
        token,
        processed=0,
        succeeded=0,
        failed=0,
        skipped=0,
        total=total,
        cursor={"phase": "validated"},
    )
    try:
        with transaction.atomic():
            created = apply_legacy_user_rows(parsed["rows"])
            # The applied marker commits with the created accounts, so a worker
            # killed after commit cannot replay the import.
            assert_admin_operation_progress(
                job.pk,
                token,
                processed=total,
                succeeded=created,
                failed=0,
                skipped=0,
                total=total,
                cursor={"phase": "applied", "created": created},
                result={"created": created, "row_errors": []},
            )
    except IntegrityError as exc:
        delete_admin_operation_artifact(job)
        raise AdminOperationError(
            "conflict",
            _("One or more usernames were created after this file was validated. No users were created; review the file and try again."),
        ) from exc
    except ValidationError as exc:
        delete_admin_operation_artifact(job)
        raise AdminOperationError("invalid_roles", "; ".join(exc.messages)) from exc

    delete_admin_operation_artifact(job)
    return {
        "status": AdminOperationJob.Status.COMPLETED,
        "result": {"created": created, "row_errors": []},
    }
