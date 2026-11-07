"""One authorization rule for shared storage-bucket object keys.

The bucket mixes public/content objects (lesson media, portal assets) with
private user documents under ``applications/`` and worker-only durable-job
inputs under ``admin-operations/``. Content managers (admin and staff) may only
operate on content keys. Private application documents stay with the owning
student through the dedicated document endpoint and with administrators; job
inputs are never reachable through the generic browser. File transitions must
keep private application documents within the same student's namespace. A
generic storage key is never authorization by itself.

Callers pass the raw key/prefix from a request. Every generic storage
endpoint (list, search, metadata, sign/download, move, rename, create folder,
delete) must call this module before touching the provider, and delayed jobs
must recheck with the recorded actor before mutating.
"""

from __future__ import annotations

from urllib.parse import unquote

from management_system.utils.decorators import can_manage_applications, can_manage_content

# Private per-student application documents (originals, previews, audit copies).
PRIVATE_USER_DOCUMENT_PREFIX = "applications/"
# Private durable-job artifacts (raw import inputs and generated export files)
# must never appear in the generic content browser or be manageable there for
# any role. Exports are streamed only by their owner-authorized job endpoint.
PRIVATE_OPERATION_ARTIFACT_PREFIX = "admin-operations/"


def normalize_object_key(value: object) -> str:
    """Return a trimmed bucket key without leading slashes, or ``""``."""

    if not isinstance(value, str):
        return ""
    return value.strip().lstrip("/")


def _matches_prefix(key: str, prefix: str) -> bool:
    return key == prefix.rstrip("/") or key.startswith(prefix)


def is_private_user_document_key(value: object) -> bool:
    """True when the key or folder prefix belongs to private user documents."""

    key = normalize_object_key(value)
    return bool(key) and _matches_prefix(key, PRIVATE_USER_DOCUMENT_PREFIX)


def is_operation_artifact_key(value: object) -> bool:
    """True when the key belongs to a private durable-job artifact."""

    key = normalize_object_key(value)
    return bool(key) and _matches_prefix(key, PRIVATE_OPERATION_ARTIFACT_PREFIX)


def is_private_storage_key(value: object) -> bool:
    """True for private application or worker-artifact keys, including encoding."""

    key = normalize_object_key(value)
    if not key:
        return False
    decoded_key = normalize_object_key(unquote(key))
    return any(
        predicate(candidate)
        for candidate in (key, decoded_key)
        for predicate in (is_private_user_document_key, is_operation_artifact_key)
    )


def user_may_manage_storage_key(user, value: object) -> bool:
    """Return whether ``user`` may operate on this bucket key or prefix.

    Private job-input artifacts are worker-only and are denied to every browser
    role, including administrators: they are unguessable, read by the executor
    and purged automatically. Private application documents require the
    application-management capability; every other content key requires content
    management. Empty or malformed keys are always denied so a caller cannot
    widen scope by omitting the key. Percent-encoded input is decoded before the
    decision so an encoded private prefix cannot slip through.
    """

    key = normalize_object_key(value)
    if not key:
        return False
    decoded_key = normalize_object_key(unquote(key))
    if is_operation_artifact_key(key) or is_operation_artifact_key(decoded_key):
        return False
    if is_private_user_document_key(key) or is_private_user_document_key(decoded_key):
        return can_manage_applications(user)
    return can_manage_content(user)


def storage_key_transition_allowed(source: object, destination: object) -> bool:
    """Keep private application documents inside the same student's namespace."""

    def private_owner(value: object) -> tuple[bool, str | None]:
        key = normalize_object_key(value)
        candidates = (key, normalize_object_key(unquote(key)))
        for candidate in candidates:
            if is_private_user_document_key(candidate):
                parts = candidate.split("/")
                return True, parts[1] if len(parts) > 1 and parts[1] else None
        return False, None

    source_private, source_owner = private_owner(source)
    destination_private, destination_owner = private_owner(destination)
    if not source_private and not destination_private:
        return True
    return bool(
        source_private
        and destination_private
        and source_owner
        and destination_owner
        and source_owner == destination_owner
    )


def filter_visible_storage_entries(user, entries: list[dict]) -> list[dict]:
    """Drop private entries from a list/search page for non-privileged users.

    Private job-input artifacts are never listed for any role; private
    application documents stay visible to application administrators.
    """

    administrator = can_manage_applications(user)
    visible = []
    for entry in entries:
        entry_key = entry.get("id") or entry.get("name") or ""
        if is_private_storage_key(entry_key) and not (
            administrator
            and (
                is_private_user_document_key(entry_key)
                or is_private_user_document_key(unquote(normalize_object_key(entry_key)))
            )
        ):
            continue
        visible.append(entry)
    return visible


def filter_content_storage_entries(entries: list[dict]) -> list[dict]:
    """Hide every private prefix from the lesson-media picker, including admins."""

    return [
        entry for entry in entries
        if not is_private_storage_key(entry.get("id") or entry.get("name") or "")
    ]
