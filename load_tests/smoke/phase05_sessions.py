#!/usr/bin/env python3
"""Disposable Phase 5 session-backend checks.

Proves the configured backend is ``cached_db`` and that it keeps the
authoritative database row: a session written through the backend reaches both
stores, survives losing the cache entry, and repopulates the cache on read.
That is the property that makes a Redis outage a performance problem rather
than a logout for every user.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")

import django

django.setup()

from django.conf import settings
from django.contrib.sessions.backends.cached_db import SessionStore
from django.core.cache import cache
from django.db import connection


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
        print("FAILED: the session checks require PostgreSQL.")
        return 1
    database_name = str(connection.settings_dict.get("NAME", "")).lower()
    if "phase08" not in database_name:
        print("FAILED: the database name must contain phase08 to prove disposable isolation.")
        return 1

    if settings.SESSION_ENGINE != "django.contrib.sessions.backends.cached_db":
        failures.append(f"session engine is {settings.SESSION_ENGINE}")
    else:
        checks.append("session engine is cached_db")
    if settings.SESSION_CACHE_ALIAS != "default":
        failures.append(f"session cache alias is {settings.SESSION_CACHE_ALIAS}")
    else:
        checks.append("session cache alias is default")

    store = SessionStore()
    store["phase08_marker"] = uuid.uuid4().hex
    store.create()
    key = store.session_key
    store.save()
    cache_key = store.cache_key

    session_table = connection.ops.quote_name("django_session")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT count(*) FROM {session_table} WHERE session_key = %s", [key]
        )
        database_row = cursor.fetchone()[0]

    try:
        if database_row != 1:
            failures.append("the session was not written to the database")
        else:
            checks.append("session persisted in the database")
        if cache.get(cache_key) is None:
            failures.append("the session was not cached after save")
        else:
            checks.append("session cached after save")

        cache.delete(cache_key)
        if cache.get(cache_key) is not None:
            failures.append("cache entry survived deletion")
        reloaded = SessionStore(session_key=key)
        marker = reloaded.get("phase08_marker")
        if marker != store["phase08_marker"]:
            failures.append("session did not load from the database after cache loss")
        else:
            checks.append("session survives a lost cache entry")
        if cache.get(cache_key) is None:
            failures.append("the backend did not repopulate the cache after a database read")
        else:
            checks.append("cache repopulated from the database")
    finally:
        reloaded_store = SessionStore(session_key=key)
        reloaded_store.delete()
        cache.delete(cache_key)
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {session_table} WHERE session_key = %s", [key]
            )

    if failures:
        args.report.write_text("FAIL - " + "; ".join(failures) + "\n", encoding="utf-8")
        print("Phase 5 session checks failed: " + "; ".join(failures))
        return 1
    args.report.write_text(
        "PASS - disposable session fixture removed.\nChecks: "
        + ", ".join(checks)
        + "\n",
        encoding="utf-8",
    )
    print("Phase 5 session checks passed: " + ", ".join(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
