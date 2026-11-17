#!/usr/bin/env python3
"""Disposable Phase 8 direct-upload checks; never prints a token or object key."""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")

DJANGO_READY = os.environ.get("PHASE08_DISPOSABLE") == "1"
DJANGO_SETUP_ERROR: Exception | None = None

if DJANGO_READY:
    try:
        import django

        django.setup()

        from django.core.exceptions import ValidationError
        from django.db import connection
        from django.test.utils import override_settings
        from django.utils import timezone

        import management_system.direct_uploads as direct_uploads
        from management_system.models import (
            DirectUploadAuthorization,
            Role,
            User,
        )
    except Exception as exc:  # noqa: BLE001 - main reports the setup failure
        DJANGO_SETUP_ERROR = exc

if DJANGO_READY and DJANGO_SETUP_ERROR is None:
    # delete_from_bucket logs object keys; never let a smoke run echo them.
    logging.getLogger("management_system.utils.storage_operations").setLevel(
        logging.CRITICAL
    )
    logging.getLogger("utils.storage_operations").setLevel(logging.CRITICAL)


class _FakeBody:
    def __init__(self, data: bytes):
        self._buffer = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def close(self) -> None:
        self._buffer.close()


class _FakeR2:
    """Small in-process stand-in for the S3 client; no provider call is made."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.head_sizes: dict[str, int] = {}
        self.head_types: dict[str, str] = {}
        self.upload_calls = 0

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (bytes(data), content_type)

    def generate_presigned_url(self, operation, Params=None, ExpiresIn=None) -> str:
        return f"https://fake.r2/{Params['Bucket']}/{Params['Key']}?signature=fake"

    def head_object(self, Bucket, Key):
        data, content_type = self.objects.get(
            Key, (b"", self.head_types.get(Key, ""))
        )
        return {
            "ContentLength": self.head_sizes.get(Key, len(data)),
            "ContentType": content_type,
        }

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("fake staging object is missing")
        data, content_type = self.objects[Key]
        return {"Body": _FakeBody(data), "ContentType": content_type}

    def upload_fileobj(self, handle, Bucket, Key, ExtraArgs=None) -> None:
        self.objects[Key] = (handle.read(), (ExtraArgs or {}).get("ContentType", ""))
        self.upload_calls += 1

    def delete_object(self, Bucket, Key) -> None:
        self.objects.pop(Key, None)
        self.head_sizes.pop(Key, None)
        self.head_types.pop(Key, None)


def _expect_error(code: str, call) -> None:
    try:
        call()
    except direct_uploads.DirectUploadError as exc:
        if exc.code != code:
            raise AssertionError(
                f"expected DirectUploadError {code}, got {exc.code}"
            ) from exc
        return
    raise AssertionError(f"expected DirectUploadError {code}, no error was raised")


def _create_user(role, label: str, suffix: str) -> User:
    return User.objects.create_user(
        username=f"p8du-{label}-{suffix}",
        password=uuid.uuid4().hex,
        role=role,
    )


def _grant(
    actor,
    tokens: list[str],
    *,
    purpose: str,
    target: str,
    filename: str,
    size: int,
    content_type: str,
):
    grant = direct_uploads.authorize_direct_upload(
        actor=actor,
        purpose=purpose,
        target=target,
        filename=filename,
        size=size,
        content_type=content_type,
    )
    tokens.append(grant.token)
    return grant


def run_contract_checks(checks: list[str]) -> None:
    apk = direct_uploads.upload_limits(
        purpose=DirectUploadAuthorization.Purpose.APK,
        content_type=direct_uploads.APK_CONTENT_TYPE,
        filename="phase8.apk",
    )
    if apk.max_bytes != 500 * direct_uploads.MEGABYTE:
        raise AssertionError("the APK hard limit is not 500 MiB")
    checks.append("APK hard limit 500 MiB")

    document = direct_uploads.upload_limits(
        purpose=DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT,
        content_type="image/png",
        filename="phase8.png",
    )
    if document.max_bytes != 10 * direct_uploads.MEGABYTE:
        raise AssertionError("the application-document hard limit is not 10 MiB")
    checks.append("application-document hard limit 10 MiB")

    if not all(
        callable(function)
        for function in (
            direct_uploads.authorize_direct_upload,
            direct_uploads.finalize_direct_upload,
            direct_uploads.expire_abandoned_direct_uploads,
        )
    ):
        raise AssertionError("the direct-upload contract functions are missing")
    checks.append("direct-upload contract functions")


def run_full_checks(checks: list[str]) -> None:
    fake = _FakeR2()
    suffix = uuid.uuid4().hex[:10]
    role = None
    role_created = False
    actor = None
    other = None
    tokens: list[str] = []
    try:
        role, role_created = Role.objects.get_or_create(role="admin")
        actor = _create_user(role, "a", suffix)
        other = _create_user(role, "b", suffix)
        with override_settings(R2_BUCKET_NAME="phase08-bucket"):
            with patch.object(direct_uploads, "get_r2_client", return_value=fake):
                _run_cases(checks, fake, actor, other, tokens)
    finally:
        owners = [user for user in (actor, other) if user is not None]
        if owners:
            DirectUploadAuthorization.objects.filter(actor__in=owners).delete()
        for user in owners:
            User.objects.filter(pk=user.pk).delete()
        if (
            role_created
            and role is not None
            and not User.objects.filter(role=role).exists()
        ):
            role.delete()
        fake.objects.clear()
        fake.head_sizes.clear()
        fake.head_types.clear()
        remaining = DirectUploadAuthorization.objects.filter(token__in=tokens).count()
        if remaining:
            raise AssertionError("cleanup left direct-upload rows behind")
        if owners and DirectUploadAuthorization.objects.filter(
            actor__in=owners
        ).exists():
            raise AssertionError("cleanup left an actor's direct-upload rows behind")
        if fake.objects or fake.head_sizes:
            raise AssertionError("cleanup left fake objects behind")


def _run_cases(checks, fake, actor, other, tokens: list[str]) -> None:
    apk_limit = 500 * direct_uploads.MEGABYTE
    document_limit = 10 * direct_uploads.MEGABYTE
    apk_type = direct_uploads.APK_CONTENT_TYPE

    # Case 1: APK boundaries and type rejection.
    apk_grant = _grant(
        actor,
        tokens,
        purpose=DirectUploadAuthorization.Purpose.APK,
        target=f"manager:{actor.pk}",
        filename="phase8.apk",
        size=apk_limit,
        content_type=apk_type,
    )
    if apk_grant.max_bytes != apk_limit:
        raise AssertionError("APK authorization did not return the 500 MiB limit")
    _expect_error(
        "file_too_large",
        lambda: direct_uploads.authorize_direct_upload(
            actor=actor,
            purpose=DirectUploadAuthorization.Purpose.APK,
            target=f"manager:{actor.pk}",
            filename="phase8.apk",
            size=apk_limit + 1,
            content_type=apk_type,
        ),
    )
    _expect_error(
        "unsupported_type",
        lambda: direct_uploads.authorize_direct_upload(
            actor=actor,
            purpose=DirectUploadAuthorization.Purpose.APK,
            target=f"manager:{actor.pk}",
            filename="phase8.zip",
            size=1024,
            content_type=apk_type,
        ),
    )
    _expect_error(
        "unsupported_type",
        lambda: direct_uploads.authorize_direct_upload(
            actor=actor,
            purpose=DirectUploadAuthorization.Purpose.APK,
            target=f"manager:{actor.pk}",
            filename="phase8.apk",
            size=1024,
            content_type="text/plain",
        ),
    )
    checks.append("case 1: APK 500 MiB boundary and type rejection")

    # Case 2: application-document boundaries.
    document_grant = _grant(
        actor,
        tokens,
        purpose=DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT,
        target=f"user:{actor.pk}",
        filename="phase8.png",
        size=9 * direct_uploads.MEGABYTE,
        content_type="image/png",
    )
    if document_grant.max_bytes != document_limit:
        raise AssertionError("application-document authorization did not return its limit")
    _expect_error(
        "file_too_large",
        lambda: direct_uploads.authorize_direct_upload(
            actor=actor,
            purpose=DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT,
            target=f"user:{actor.pk}",
            filename="phase8.png",
            size=11 * direct_uploads.MEGABYTE,
            content_type="image/png",
        ),
    )
    checks.append("case 2: application-document 9 MiB accepted, 11 MiB rejected")

    # Case 3: another actor cannot finalize a pending capability.
    pending_grant = _grant(
        actor,
        tokens,
        purpose=DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT,
        target=f"user:{actor.pk}",
        filename="phase8.png",
        size=1024,
        content_type="image/png",
    )
    _expect_error(
        "forbidden",
        lambda: direct_uploads.finalize_direct_upload(
            actor=other, token=pending_grant.token
        ),
    )
    pending_row = DirectUploadAuthorization.objects.get(token=pending_grant.token)
    if pending_row.status != DirectUploadAuthorization.Status.PENDING:
        raise AssertionError("a forbidden finalize changed the row status")
    if pending_row.failure_reason:
        raise AssertionError("a forbidden finalize set a failure reason")
    checks.append("case 3: non-owner finalize is forbidden and leaves the row pending")

    # Case 4: validator failure persists and propagates as ValidationError.
    validator_grant = _grant(
        actor,
        tokens,
        purpose=DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT,
        target=f"user:{actor.pk}",
        filename="phase8.png",
        size=8,
        content_type="image/png",
    )
    fake.put(validator_grant.staging_key, b"invalid8", "image/png")

    def reject_payload(upload) -> None:
        raise ValidationError("fixture rejection")

    try:
        direct_uploads.finalize_direct_upload(
            actor=actor,
            token=validator_grant.token,
            validator=reject_payload,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("a validator rejection did not propagate as ValidationError")
    validator_row = DirectUploadAuthorization.objects.get(
        token=validator_grant.token
    )
    if validator_row.status != DirectUploadAuthorization.Status.FAILED:
        raise AssertionError("a validator rejection did not persist a FAILED row")
    if validator_row.failure_reason != "validation_failed":
        raise AssertionError("a validator rejection did not record validation_failed")
    if validator_grant.staging_key in fake.objects:
        raise AssertionError("a validator rejection left the staging object behind")
    _expect_error(
        "expired",
        lambda: direct_uploads.finalize_direct_upload(
            actor=actor, token=validator_grant.token
        ),
    )
    checks.append(
        "case 4: validator rejection persists FAILED, deletes staging, and re-raises"
    )

    # Case 5: happy path stores exactly the declared length.
    apk_payload = b"PK\x03\x04" + b"phase8-apk-payload"
    happy_grant = _grant(
        actor,
        tokens,
        purpose=DirectUploadAuthorization.Purpose.APK,
        target=f"manager:{actor.pk}",
        filename="phase8.apk",
        size=len(apk_payload),
        content_type=apk_type,
    )
    fake.put(happy_grant.staging_key, apk_payload, apk_type)

    def accept_apk(upload) -> None:
        if upload.read(4) != b"PK\x03\x04":
            raise ValidationError("not an APK header")
        upload.seek(0)

    finalized = direct_uploads.finalize_direct_upload(
        actor=actor,
        token=happy_grant.token,
        validator=accept_apk,
        final_folder="app-releases",
    )
    if not finalized.key.startswith("app-releases/"):
        raise AssertionError("the finalized key is outside the requested final folder")
    if fake.objects.get(finalized.key, (None, ""))[0] != apk_payload:
        raise AssertionError("the final object does not contain the validated bytes")
    if happy_grant.staging_key in fake.objects:
        raise AssertionError("the happy path did not remove the staging object")
    happy_row = DirectUploadAuthorization.objects.get(token=happy_grant.token)
    if happy_row.status != DirectUploadAuthorization.Status.FINALIZED:
        raise AssertionError("the happy path did not finalize the row")
    if finalized.size != len(apk_payload):
        raise AssertionError("the finalized size does not equal the stored length")
    checks.append("case 5: APK happy path publishes the exact declared bytes")

    # Case 6: idempotent finalize does not publish a second object.
    upload_calls_before = fake.upload_calls
    repeated = direct_uploads.finalize_direct_upload(
        actor=actor, token=happy_grant.token
    )
    if repeated.key != finalized.key:
        raise AssertionError("idempotent finalize returned a different key")
    if fake.upload_calls != upload_calls_before:
        raise AssertionError("idempotent finalize published another object")
    if len([key for key in fake.objects if key.startswith("app-releases/")]) != 1:
        raise AssertionError("idempotent finalize left more than one final object")
    checks.append("case 6: repeated finalize returns the same key without republishing")

    # Case 7a: stored size differs from the declared size.
    mismatch_grant = _grant(
        actor,
        tokens,
        purpose=DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT,
        target=f"user:{actor.pk}",
        filename="phase8.png",
        size=1024,
        content_type="image/png",
    )
    fake.put(mismatch_grant.staging_key, b"x" * 2048, "image/png")
    _expect_error(
        "size_mismatch",
        lambda: direct_uploads.finalize_direct_upload(
            actor=actor, token=mismatch_grant.token
        ),
    )
    mismatch_row = DirectUploadAuthorization.objects.get(token=mismatch_grant.token)
    if mismatch_row.status != DirectUploadAuthorization.Status.FAILED:
        raise AssertionError("a declared-size mismatch did not persist a FAILED row")
    if mismatch_row.failure_reason != "size_mismatch":
        raise AssertionError("a declared-size mismatch did not record size_mismatch")
    if mismatch_grant.staging_key in fake.objects:
        raise AssertionError("a declared-size mismatch left the staging object behind")
    checks.append("case 7a: declared-size mismatch persists FAILED and deletes staging")

    # Case 7b: the purpose limit wins over the declared-size mismatch.
    oversize_grant = _grant(
        actor,
        tokens,
        purpose=DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT,
        target=f"user:{actor.pk}",
        filename="phase8.png",
        size=1024,
        content_type="image/png",
    )
    fake.put(oversize_grant.staging_key, b"x", "image/png")
    fake.head_sizes[oversize_grant.staging_key] = document_limit + 1
    _expect_error(
        "file_too_large",
        lambda: direct_uploads.finalize_direct_upload(
            actor=actor, token=oversize_grant.token
        ),
    )
    oversize_row = DirectUploadAuthorization.objects.get(token=oversize_grant.token)
    if oversize_row.status != DirectUploadAuthorization.Status.FAILED:
        raise AssertionError("an oversized stored object did not persist a FAILED row")
    if oversize_row.failure_reason != "file_too_large":
        raise AssertionError("an oversized stored object did not record file_too_large")
    if oversize_grant.staging_key in fake.objects:
        raise AssertionError("an oversized stored object left staging behind")
    checks.append("case 7b: stored size over the purpose cap raises file_too_large")

    # Case 8: expire only abandoned pending rows and their staging objects.
    stale_grant = _grant(
        actor,
        tokens,
        purpose=DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT,
        target=f"user:{actor.pk}",
        filename="phase8-stale.png",
        size=1024,
        content_type="image/png",
    )
    fake.put(stale_grant.staging_key, b"s" * 1024, "image/png")
    DirectUploadAuthorization.objects.filter(token=stale_grant.token).update(
        expires_at=timezone.now()
        - direct_uploads.FINALIZE_GRACE
        - timedelta(minutes=1)
    )
    fresh_grant = _grant(
        actor,
        tokens,
        purpose=DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT,
        target=f"user:{actor.pk}",
        filename="phase8-fresh.png",
        size=1024,
        content_type="image/png",
    )
    fake.put(fresh_grant.staging_key, b"f" * 1024, "image/png")
    sweep = direct_uploads.expire_abandoned_direct_uploads()
    stale_row = DirectUploadAuthorization.objects.get(token=stale_grant.token)
    fresh_row = DirectUploadAuthorization.objects.get(token=fresh_grant.token)
    if stale_row.status != DirectUploadAuthorization.Status.EXPIRED:
        raise AssertionError("an abandoned pending row was not expired")
    if fresh_row.status != DirectUploadAuthorization.Status.PENDING:
        raise AssertionError("a fresh pending row was expired")
    if stale_grant.staging_key in fake.objects:
        raise AssertionError("an expired row kept its staging object")
    if fresh_grant.staging_key not in fake.objects:
        raise AssertionError("a fresh pending row lost its staging object")
    if sweep["expired"] < 1:
        raise AssertionError("the expiry sweep did not report the expired row")
    checks.append("case 8: abandoned expiry removes only stale staging")

    # Case 9: the token alone is never authorization after finalize.
    upload_calls_before = fake.upload_calls
    _expect_error(
        "forbidden",
        lambda: direct_uploads.finalize_direct_upload(
            actor=other, token=happy_grant.token
        ),
    )
    happy_row.refresh_from_db()
    if happy_row.status != DirectUploadAuthorization.Status.FINALIZED:
        raise AssertionError("a non-owner finalize changed the finalized row")
    if fake.upload_calls != upload_calls_before:
        raise AssertionError("a non-owner finalize published another object")
    if len([key for key in fake.objects if key.startswith("app-releases/")]) != 1:
        raise AssertionError("a non-owner finalize duplicated the final object")
    checks.append("case 9: the finalized token still requires its original actor")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-disposable", action="store_true", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--contract-only", action="store_true")
    args = parser.parse_args()
    checks: list[str] = []

    try:
        if os.environ.get("PHASE08_DISPOSABLE") != "1":
            raise RuntimeError("PHASE08_DISPOSABLE=1 is required for this smoke check.")
        if DJANGO_SETUP_ERROR is not None:
            raise RuntimeError(
                f"Django setup failed: {type(DJANGO_SETUP_ERROR).__name__}"
            )
        if not DJANGO_READY:
            raise RuntimeError("Django modules were not loaded.")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        if args.contract_only:
            run_contract_checks(checks)
            args.report.write_text(
                "CONTRACT ONLY PASS - database integration was not checked.\n"
                f"Checks: {', '.join(checks)}\n",
                encoding="utf-8",
            )
            print(
                "Phase 8 direct-upload contract-only checks passed: "
                + ", ".join(checks)
            )
            return 0
        if connection.vendor != "postgresql":
            raise RuntimeError("Phase 8 direct-upload checks require PostgreSQL.")
        if "phase08" not in str(
            connection.settings_dict.get("NAME", "")
        ).lower():
            raise RuntimeError(
                "Database name must contain phase08 to prove disposable isolation."
            )
        connection.ensure_connection()
        checks.append("isolated PostgreSQL connection")
        run_full_checks(checks)
    except Exception as exc:  # noqa: BLE001 - the report carries the failure
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            f"FAILED: {type(exc).__name__}: {exc}\n"
            f"Checks: {', '.join(checks)}\n",
            encoding="utf-8",
        )
        print(
            f"Phase 8 direct-upload smoke failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        f"PASS\nChecks: {', '.join(checks)}\n",
        encoding="utf-8",
    )
    print(f"Phase 8 direct-upload smoke passed: {', '.join(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
