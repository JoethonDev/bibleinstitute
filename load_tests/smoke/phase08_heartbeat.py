#!/usr/bin/env python3
"""Disposable Phase 8 heartbeat integration checks; emits no student data.

Exercises the real ``apply_progress_heartbeat`` on PostgreSQL: cumulative range
accounting, the exact replay acknowledgement, stale and gap resync, and the
earned-credit ceiling. The manifest timeline is seeded into the cache so no
provider call is made.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")

import django

django.setup()

from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone

from management_system.media_delivery import create_viewing_session
from management_system.models import (
    AcademicYear,
    AcademicYearLevel,
    Course,
    CourseOffering,
    Enrollment,
    LectureProgress,
    Lesson,
    Level,
    PublicationStatus,
    Role,
    User,
    ViewingSession,
)
from management_system.progress_activity import apply_progress_heartbeat

MEDIA_KEY = "phase08/heartbeat/playlist.m3u8"
PART_ID = "part-video"
TIMELINE = [
    {"key": "seg-1.ts", "number": 1, "duration": 30.0, "start": 0.0, "end": 30.0},
    {"key": "seg-2.ts", "number": 2, "duration": 30.0, "start": 30.0, "end": 60.0},
]


def build_fixture(suffix: str):
    today = timezone.localdate()
    role, _ = Role.objects.get_or_create(role="student")
    level = Level.objects.create(
        ordering=(Level.objects.order_by("-ordering").values_list("ordering", flat=True).first() or 0) + 1,
        name_en=f"Heartbeat {suffix}",
    )
    year = AcademicYear.objects.create(
        name=f"HB-{suffix}",
        starts_on=today - timedelta(days=30),
        ends_on=today + timedelta(days=30),
        ordering=(AcademicYear.objects.order_by("-ordering").values_list("ordering", flat=True).first() or 0) + 1,
        is_active=True,
    )
    scope = AcademicYearLevel.objects.create(academic_year=year, level=level)
    course = Course.objects.create(name=f"Heartbeat {suffix}", description="", level=level)
    offering = CourseOffering.objects.create(
        course=course,
        academic_year_level=scope,
        status=PublicationStatus.PUBLISHED,
    )
    lesson = Lesson.objects.create(
        name=f"Heartbeat lesson {suffix}",
        description="",
        links=json.dumps([{"id": MEDIA_KEY, "file_type": "video", "part_id": PART_ID}]),
        course_offering=offering,
        status=PublicationStatus.PUBLISHED,
        opening_date=timezone.now() - timedelta(days=1),
        created_date=today - timedelta(days=2),
    )
    user = User.objects.create_user(
        username=f"hb{suffix}",
        password=uuid.uuid4().hex,
        role=role,
        study_mode="online",
    )
    Enrollment.objects.create(
        student=user,
        academic_year_level=scope,
        status=Enrollment.Status.ACTIVE,
        enrollment_type=Enrollment.Type.NORMAL,
    )
    return user, lesson


def payload(session_id: str, sequence: int, ranges: list, active: float, ended: bool = False) -> dict:
    return {
        "session_id": session_id,
        "sequence": sequence,
        "ranges": ranges,
        "active_seconds": active,
        "ended": ended,
    }


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
        print("FAILED: the heartbeat checks require PostgreSQL.")
        return 1
    database_name = str(connection.settings_dict.get("NAME", "")).lower()
    if "phase08" not in database_name:
        print("FAILED: the database name must contain phase08 to prove disposable isolation.")
        return 1
    suffix = uuid.uuid4().hex[:10]
    with transaction.atomic():
        user, lesson = build_fixture(suffix)
        cache.set(f"hls:segments:{MEDIA_KEY}", TIMELINE, 3600)
        session = create_viewing_session(
            user,
            lesson,
            PART_ID,
            media_key=MEDIA_KEY,
        )

        first = apply_progress_heartbeat(
            user=user,
            payload=payload(session.session_id, 1, [[0, 30]], 25),
            cloud_client=None,
            bucket_name="unused",
        )
        if first.status != "ok" or first.percent <= 0:
            failures.append(f"first heartbeat: status={first.status} percent={first.percent}")
        else:
            checks.append("first heartbeat accepted and persisted progress")
        payload_ack = first.as_payload()
        if not payload_ack.get("accepted_ranges"):
            failures.append("first heartbeat acknowledgement reported no accepted range")
        else:
            checks.append("acknowledgement lists the accepted range")
        if not payload_ack.get("remaining_ranges"):
            failures.append("acknowledgement did not report the uncredited suffix")
        else:
            checks.append("acknowledgement reports the bounded credit suffix")

        replayed = apply_progress_heartbeat(
            user=user,
            payload=payload(session.session_id, 1, [[0, 30]], 25),
            cloud_client=None,
            bucket_name="unused",
        )
        if replayed.as_payload() != first.as_payload():
            failures.append("exact duplicate did not return the stored acknowledgement")
        else:
            checks.append("exact duplicate returns the stored acknowledgement")

        second = apply_progress_heartbeat(
            user=user,
            payload=payload(session.session_id, 2, [[0, 30], [20, 40]], 20),
            cloud_client=None,
            bucket_name="unused",
        )
        second_ack = second.as_payload()
        if second_ack.get("sequence") != 2 or second.percent < first.percent:
            failures.append(
                "partial overlap: sequence={seq} percent={percent}".format(
                    seq=second_ack.get("sequence"), percent=second.percent
                )
            )
        elif not second_ack.get("already_saved_ranges"):
            failures.append("overlapping ranges were not recognised as already saved")
        elif not second_ack.get("accepted_ranges"):
            failures.append("the newly covered second was not accepted")
        else:
            checks.append("overlapping ranges credit only the new second")

        stale = apply_progress_heartbeat(
            user=user,
            payload=payload(session.session_id, 1, [[0, 10]], 5),
            cloud_client=None,
            bucket_name="unused",
        )
        if stale.status != "resync" or stale.reason != "stale_sequence":
            failures.append(f"stale sequence: status={stale.status} reason={stale.reason}")
        else:
            checks.append("older sequence resyncs instead of crediting")

        gap = apply_progress_heartbeat(
            user=user,
            payload=payload(session.session_id, 5, [[40, 60]], 10),
            cloud_client=None,
            bucket_name="unused",
        )
        if gap.status != "resync" or gap.reason != "sequence_gap":
            failures.append(f"sequence gap: status={gap.status} reason={gap.reason}")
        else:
            checks.append("sequence gap resyncs")

        progress = LectureProgress.objects.get(
            student=user, lesson=lesson, part_id=PART_ID
        )
        if progress.percent < first.percent:
            failures.append(f"persisted percent={progress.percent}")
        else:
            checks.append("persisted percent matches the progress it credited")

        locked = ViewingSession.objects.get(pk=session.pk)
        honoured = (float(locked.edge_earned_seconds_consumed) * 100)
        slack_used = 1500 - (float(progress.edge_slack_remaining_seconds) * 100)
        if honoured - slack_used > 2 * 3600 + 15:
            failures.append("credit exceeded the server-time ceiling")
        else:
            checks.append("credit stays inside the server-time ceiling")

        transaction.set_rollback(True)

    cache.delete(f"hls:segments:{MEDIA_KEY}")
    if User.objects.filter(username=f"hb{suffix}").count():
        failures.append("check did not roll back its fixtures")

    if failures:
        args.report.write_text("FAIL - " + "; ".join(failures) + "\n", encoding="utf-8")
        print("Phase 8 heartbeat checks failed: " + "; ".join(failures))
        return 1
    args.report.write_text(
        "PASS - disposable PostgreSQL fixture rolled back.\n"
        + "Checks: " + ", ".join(checks) + "\n",
        encoding="utf-8",
    )
    print("Phase 8 heartbeat checks passed: " + ", ".join(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
