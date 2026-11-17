#!/usr/bin/env python3
"""Disposable Phase 8 attendance and cleanup checks; never prints student data."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, time as day_time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _rollback_fixtures(checks: list[str]) -> None:
    from django.db import connection, transaction
    from django.test.utils import CaptureQueriesContext
    from django.utils import timezone as django_timezone
    from django.db.models import Q

    from management_system.models import (
        AcademicYear,
        AcademicYearLevel,
        AttendancePolicy,
        Course,
        CourseOffering,
        Enrollment,
        LectureProgress,
        LectureProgressEvent,
        Level,
        Lesson,
        PublicationStatus,
        Role,
        User,
        VerifiedSegmentRequest,
        ViewingSession,
    )
    from management_system.progress_activity import online_attendance_for_students
    from management_system.session_cleanup import cleanup_expired_viewing_sessions

    applied = set(__import__("django.db.migrations.executor", fromlist=["MigrationExecutor"]).MigrationExecutor(connection).loader.applied_migrations)
    required_migration = ("management_system", "0128_viewing_session_mode_and_online_attendance_policy")
    if required_migration not in applied:
        raise RuntimeError("The Phase 8 forward migration is not applied to this disposable database.")

    suffix = uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    today = django_timezone.localdate(now)
    with transaction.atomic():
        try:
            role, _ = Role.objects.get_or_create(role="student")
            level = Level.objects.create(
                ordering=(Level.objects.order_by("-ordering").values_list("ordering", flat=True).first() or 0) + 1,
                name_en=f"Phase 8 {suffix}",
            )
            year = AcademicYear.objects.create(
                name=f"P8-{suffix}",
                starts_on=today - timedelta(days=60),
                ends_on=today + timedelta(days=60),
                ordering=(AcademicYear.objects.order_by("-ordering").values_list("ordering", flat=True).first() or 0) + 1,
            )
            scope = AcademicYearLevel.objects.create(academic_year=year, level=level)
            course = Course.objects.create(name=f"Phase 8 {suffix}", description="", level=level)
            offering = CourseOffering.objects.create(
                course=course,
                academic_year_level=scope,
                status=PublicationStatus.PUBLISHED,
            )

            def student(label: str, study_mode: str) -> User:
                return User.objects.create_user(
                    username=f"p8{label}{suffix}",
                    password=uuid4().hex,
                    role=role,
                    study_mode=study_mode,
                )

            online_students = [student(f"on{i}", "online") for i in range(3)]
            offline_student = student("off", "offline")
            graduate = student("grad", "online")
            active_users = [*online_students, offline_student]
            for user in active_users:
                Enrollment.objects.create(
                    student=user,
                    academic_year_level=scope,
                    status=Enrollment.Status.ACTIVE,
                )
            Enrollment.objects.create(
                student=graduate,
                academic_year_level=scope,
                status=Enrollment.Status.COMPLETED,
            )

            policy = AttendancePolicy.objects.create(
                singleton=f"phase8-{suffix}",
                online_full_days=7,
                online_partial_days=14,
                online_partial_value=Decimal("0.50"),
            )

            def lesson(label: str, links: list, *, opening_date, created_date=None) -> Lesson:
                return Lesson.objects.create(
                    name=f"Phase 8 {label} {suffix}",
                    description="",
                    links=json.dumps(links),
                    course_offering=offering,
                    status=PublicationStatus.PUBLISHED,
                    opening_date=opening_date,
                    created_date=created_date or today - timedelta(days=20),
                )

            partial = lesson(
                "partial",
                [
                    {"file_type": "video", "part_id": "part-1"},
                    {"file_type": "audio", "part_id": "part-1"},
                    {"file_type": "video", "part_id": "part-2"},
                ],
                opening_date=now - timedelta(days=20),
            )
            incomplete = lesson(
                "incomplete",
                [{"file_type": "video", "part_id": "part-1"}, {"file_type": "audio", "part_id": "part-2"}],
                opening_date=now - timedelta(days=20),
            )
            no_media = lesson("no-media", [], opening_date=now - timedelta(days=20))
            fallback = lesson("fallback", [{"file_type": "video", "part_id": "part-1"}], opening_date=None)

            def completed(user, target_lesson, part_id, completed_at, percent=80):
                LectureProgress.objects.create(
                    student=user,
                    lesson=target_lesson,
                    part_id=part_id,
                    percent=percent,
                    completed_at=completed_at,
                )

            published = partial.opening_date
            completed(online_students[0], partial, "part-1", published + timedelta(days=2), 85)
            completed(online_students[0], partial, "part-2", published + timedelta(days=10), 90)
            completed(online_students[0], incomplete, "part-1", incomplete.opening_date + timedelta(days=2), 90)
            completed(online_students[0], incomplete, "part-2", incomplete.opening_date + timedelta(days=3), 79)
            fallback_start = django_timezone.make_aware(datetime.combine(fallback.created_date, day_time.min))
            completed(online_students[0], fallback, "part-1", fallback_start + timedelta(days=7), 80)

            with CaptureQueriesContext(connection) as query_log:
                attendance = online_attendance_for_students(
                    student_ids=[user.pk for user in [*online_students, offline_student, graduate]],
                    offering_ids=[offering.pk],
                    window_start=django_timezone.make_aware(datetime.combine(year.starts_on, day_time.min)),
                    window_end=now,
                    policy=policy,
                )
            facts = attendance[online_students[0].pk]
            if facts != {
                "expected": 3,
                "valid": 1,
                "partial": 1,
                "absent": 1,
                "attendance_score": Decimal("1.50"),
            }:
                raise AssertionError("Online attendance aggregation returned incorrect lesson facts.")
            if len(attendance) != len(online_students):
                raise AssertionError("Offline students or graduates were included in online attendance.")
            if len(query_log) > 3:
                raise AssertionError("Online attendance used more than three bounded grouped queries.")
            checks.append(f"grouped online attendance for {len(online_students)} students in {len(query_log)} queries")

            cutoff = now - timedelta(days=8)
            stale = ViewingSession.objects.create(
                student=online_students[0],
                lesson=partial,
                part_id="part-1",
                session_id=f"p8-stale-{suffix}",
                expires_at=now + timedelta(days=30),
                started_at=now - timedelta(days=20),
                ended_at=cutoff,
            )
            replayable = ViewingSession.objects.create(
                student=online_students[0],
                lesson=partial,
                part_id="part-1",
                session_id=f"p8-replay-{suffix}",
                expires_at=now + timedelta(days=30),
                started_at=now - timedelta(days=10),
                ended_at=now - timedelta(days=6),
            )
            active = ViewingSession.objects.create(
                student=online_students[0],
                lesson=partial,
                part_id="part-1",
                session_id=f"p8-active-{suffix}",
                expires_at=now + timedelta(days=1),
                started_at=now,
            )
            stale_receipt = VerifiedSegmentRequest.objects.create(
                session=stale,
                segment_number=1,
                segment_key=f"smoke/{suffix}/stale.ts",
            )
            replay_receipt = VerifiedSegmentRequest.objects.create(
                session=replayable,
                segment_number=1,
                segment_key=f"smoke/{suffix}/replay.ts",
            )
            stale_event = LectureProgressEvent.objects.create(
                student=online_students[0],
                lesson=partial,
                viewing_session=stale,
                part_id="part-1",
                event_type=LectureProgressEvent.EventType.ACTIVITY,
                occurred_at=cutoff,
                bucket_start=cutoff,
                percent=85,
                unique_seconds=20,
            )
            old_progress_id = LectureProgress.objects.get(
                student=online_students[0], lesson=partial, part_id="part-1"
            ).pk
            cutoff_filter = Q(expires_at__lte=now - timedelta(days=7)) | Q(
                superseded_at__lte=now - timedelta(days=7)
            ) | Q(ended_at__lte=now - timedelta(days=7))
            existing_expired = ViewingSession.objects.filter(cutoff_filter).exclude(pk=stale.pk).exists()
            if existing_expired:
                raise RuntimeError("Disposable database contains pre-existing replay-expired sessions; cleanup was not run.")

            preview = cleanup_expired_viewing_sessions(
                dry_run=True,
                child_batch_size=1,
                session_batch_size=1,
                deadline=time.monotonic() + 3,
            )
            if preview["remaining_segments"] != 1 or preview["remaining_events"] != 1:
                raise AssertionError("Cleanup dry-run did not report the isolated fixture backlog.")
            if not VerifiedSegmentRequest.objects.filter(pk=stale_receipt.pk).exists():
                raise AssertionError("Cleanup dry-run changed segment evidence.")
            if LectureProgressEvent.objects.get(pk=stale_event.pk).viewing_session_id != stale.pk:
                raise AssertionError("Cleanup dry-run changed progress-event history.")

            cleanup = cleanup_expired_viewing_sessions(
                dry_run=False,
                child_batch_size=1,
                session_batch_size=1,
                deadline=time.monotonic() + 5,
            )
            if (cleanup["segments_deleted"], cleanup["events_detached"], cleanup["sessions_deleted"]) != (1, 1, 1):
                raise AssertionError("Cleanup did not report its exact fixture changes.")
            if cleanup["has_backlog"] or cleanup["max_transaction_seconds"] > 2.0:
                raise AssertionError("Cleanup left fixture backlog or exceeded the 2-second transaction target.")
            if ViewingSession.objects.filter(pk__in=[stale.pk]).exists():
                raise AssertionError("Replay-expired viewing session was retained.")
            if not VerifiedSegmentRequest.objects.filter(pk=replay_receipt.pk).exists():
                raise AssertionError("Replayable evidence was deleted early.")
            if ViewingSession.objects.filter(pk__in=[replayable.pk, active.pk]).count() != 2:
                raise AssertionError("Active or replayable viewing session was deleted.")
            if LectureProgressEvent.objects.get(pk=stale_event.pk).viewing_session_id is not None:
                raise AssertionError("Progress event history was not preserved and detached.")
            if not LectureProgress.objects.filter(pk=old_progress_id).exists():
                raise AssertionError("Academic progress history was deleted.")
            checks.append(
                "bounded cleanup preserves active/replayable sessions and progress history "
                f"(max transaction {cleanup['max_transaction_seconds']:.3f}s)"
            )
        finally:
            transaction.set_rollback(True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-disposable", action="store_true", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--contract-only", action="store_true")
    args = parser.parse_args()
    checks: list[str] = []
    failures: list[str] = []

    try:
        if os.environ.get("PHASE08_DISPOSABLE") != "1":
            raise RuntimeError("PHASE08_DISPOSABLE=1 is required for this smoke check.")

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")
        import django

        django.setup()

        from django.db import connection
        from management_system.models import AttendancePolicy
        from management_system.progress_activity import online_attendance_score_for_lesson
        from management_system.session_cleanup import (
            MAX_CHILD_BATCH_SIZE,
            MAX_SESSION_BATCH_SIZE,
            cleanup_expired_viewing_sessions,
        )

        policy_fields = {field.name for field in AttendancePolicy._meta.get_fields()}
        required_fields = {"online_full_days", "online_partial_days", "online_partial_value"}
        if required_fields - policy_fields:
            failures.append(f"AttendancePolicy is missing fields: {', '.join(sorted(required_fields - policy_fields))}")
        else:
            checks.append("online attendance policy fields")

        policy = type("Policy", (), {
            "online_full_days": 7,
            "online_partial_days": 14,
            "online_partial_value": Decimal("0.50"),
        })()
        published = datetime(2026, 1, 1, tzinfo=timezone.utc)
        cases = (
            (published + timedelta(days=7), Decimal("1.00")),
            (published + timedelta(days=7, seconds=1), Decimal("0.50")),
            (published + timedelta(days=14), Decimal("0.50")),
            (published + timedelta(days=14, seconds=1), Decimal("0.00")),
            (None, Decimal("0.00")),
        )
        if any(online_attendance_score_for_lesson(published, completed, policy) != expected for completed, expected in cases):
            failures.append("online attendance did not score the exact 7/14-day boundaries")
        else:
            checks.append("online attendance 7/14-day score boundaries")

        if not callable(cleanup_expired_viewing_sessions):
            failures.append("bounded viewing-session cleanup helper is missing")
        elif (MAX_CHILD_BATCH_SIZE, MAX_SESSION_BATCH_SIZE) != (5000, 500):
            failures.append("viewing-session cleanup batch caps differ from the approved limits")
        else:
            checks.append("bounded viewing-session cleanup contract")

        if failures:
            raise AssertionError("; ".join(failures))
        if args.contract_only:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                "CONTRACT ONLY PASS - database integration was not checked.\n"
                f"Checks: {', '.join(checks)}\n",
                encoding="utf-8",
            )
            print(f"Phase 8 attendance/cleanup contract checks passed: {', '.join(checks)}")
            return 0

        if connection.vendor != "postgresql":
            raise RuntimeError("Phase 8 attendance/cleanup checks require PostgreSQL.")
        if "phase08" not in str(connection.settings_dict.get("NAME", "")).lower():
            raise RuntimeError("Database name must contain phase08 to prove disposable isolation.")
        connection.ensure_connection()
        checks.append("isolated PostgreSQL connection")
        _rollback_fixtures(checks)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text("PASS - disposable PostgreSQL fixture rolled back.\n" + f"Checks: {', '.join(checks)}\n", encoding="utf-8")
        print(f"Phase 8 attendance/cleanup checks passed: {', '.join(checks)}")
        return 0

    except Exception as exc:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(f"FAILED: {exc}\nChecks: {', '.join(checks)}\n", encoding="utf-8")
        print(f"Phase 8 attendance/cleanup smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
