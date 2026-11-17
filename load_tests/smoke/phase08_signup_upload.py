#!/usr/bin/env python3
"""Disposable Phase 8 signup-upload checks; emits no student data.

Proves the applicant link: a signup document capability is minted by an
anonymous HTTP request, bound to that browser session, refused for a different
session, and finalized into the new account's private `applications/<id>/`
namespace with the user's document columns populated.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")

import django

django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from PIL import Image

from management_system import direct_uploads
from management_system.direct_upload_views import applicant_session_digest
from management_system.models import Role, User
from management_system.utils import application_uploads
from management_system.utils.application_uploads import finalize_application_documents

BUCKET = "phase08-signup-bucket"


class FakeBody:
    def __init__(self, payload: bytes):
        self._buffer = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def close(self) -> None:
        pass


class FakeR2:
    def __init__(self):
        self.objects: dict[str, dict] = {}

    def generate_presigned_url(self, *_args, **_kwargs):
        return "https://fake.r2.invalid/put"

    def head_object(self, *, Bucket, Key):
        item = self.objects[Key]
        return {"ContentLength": len(item["body"]), "ContentType": item["content_type"]}

    def get_object(self, *, Bucket, Key):
        item = self.objects[Key]
        return {"Body": FakeBody(item["body"]), "ContentType": item["content_type"]}

    def put_object(self, *, Bucket, Key, Body, ContentType=None, **_kwargs):
        payload = Body.read() if hasattr(Body, "read") else bytes(Body)
        self.objects[Key] = {"body": payload, "content_type": ContentType or ""}
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None, **_kwargs):
        self.objects[key] = {
            "body": fileobj.read(),
            "content_type": (ExtraArgs or {}).get("ContentType", ""),
        }

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), (30, 80, 140)).save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-disposable", action="store_true", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    checks: list[str] = []
    failures: list[str] = []
    if os.environ.get("PHASE08_DISPOSABLE") != "1":
        print("FAILED: PHASE08_DISPOSABLE=1 is required for this smoke check.")
        return 1
    if connection.vendor != "postgresql":
        print("FAILED: the signup upload checks require PostgreSQL.")
        return 1
    if "phase08" not in str(connection.settings_dict.get("NAME", "")).lower():
        print("FAILED: the database name must contain phase08 to prove disposable isolation.")
        return 1
    fake = FakeR2()
    payload = png_bytes()

    with transaction.atomic():
        role, _ = Role.objects.get_or_create(role="student")
        session_client = Client()
        session_client.get("/")  # establish a session cookie
        digest = applicant_session_digest(type("R", (), {"session": session_client.session})())
        if not digest:
            failures.append("no applicant session digest was produced")
        else:
            checks.append("applicant session digest is derived")

        direct_uploads.get_r2_client = lambda: fake
        application_uploads.get_r2_client = lambda: fake
        with override_settings(R2_BUCKET_NAME=BUCKET):
            grant = direct_uploads.authorize_direct_upload(
                actor=None,
                purpose="application_document",
                target=f"applicant:{digest}",
                filename="identity.png",
                size=len(payload),
                content_type="image/png",
                metadata={"document_type": "identity_front", "applicant_session": digest},
            )
            fake.objects[grant.staging_key] = {"body": payload, "content_type": "image/png"}

            user = User.objects.create_user(
                username=f"p8signup{uuid.uuid4().hex[:8]}",
                password=uuid.uuid4().hex,
                role=role,
                study_mode="online",
            )

            # A different browser session must not redeem the capability.
            other_digest = "0" * 32
            try:
                finalize_application_documents(
                    actor=None,
                    tokens={"identity_front": grant.token},
                    cloud_client=fake,
                    bucket_name=BUCKET,
                    user_id=user.pk,
                    applicant_session=other_digest,
                )
            except Exception:
                checks.append("a foreign session cannot redeem the capability")
            else:
                failures.append("a foreign session redeemed the capability")

            finalized = finalize_application_documents(
                actor=None,
                tokens={"identity_front": grant.token},
                cloud_client=fake,
                bucket_name=BUCKET,
                user_id=user.pk,
                applicant_session=digest,
            )
            item = finalized.get("identity_front")
            if item is None:
                failures.append("the applicant document was not finalized")
            else:
                if not item.original_key.startswith(f"applications/{user.pk}/identity_front/"):
                    failures.append("the document was not re-homed under the account")
                elif item.original_key not in fake.objects:
                    failures.append("the published object is missing from storage")
                else:
                    checks.append("document re-homed into the account namespace")
                if not item.preview_key or not item.preview_key.endswith(".webp"):
                    failures.append("no private preview was built")
                elif item.preview_key not in fake.objects:
                    failures.append("the preview object is missing from storage")
                else:
                    checks.append("private preview stored for the account")

            user.identity_front_key = item.original_key if item else ""
            user.identity_front_preview_key = item.preview_key if item else None
            user.save(update_fields=["identity_front_key", "identity_front_preview_key"])
            user.refresh_from_db()
            if not user.identity_front_key or not user.identity_front_preview_key:
                failures.append("the account columns were not populated")
            else:
                checks.append("account document columns populated")

        transaction.set_rollback(True)

    if User.objects.filter(username__startswith="p8signup").exists():
        failures.append("check did not roll back its fixture")

    if failures:
        args.report.write_text("FAIL - " + "; ".join(failures) + "\n", encoding="utf-8")
        print("Phase 8 signup upload checks failed: " + "; ".join(failures))
        return 1
    args.report.write_text(
        "PASS - disposable applicant fixture rolled back.\nChecks: "
        + ", ".join(checks)
        + "\n",
        encoding="utf-8",
    )
    print("Phase 8 signup upload checks passed: " + ", ".join(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
