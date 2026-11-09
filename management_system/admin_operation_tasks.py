"""Celery entrypoints for the durable management-operation ledger."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from .admin_operations import (
    AdminOperationError,
    admin_operation_heartbeat,
    require_admin_operation_actor,
    claim_admin_operation,
    fail_admin_operation,
    finish_admin_operation,
    purge_expired_admin_operation_artifacts,
    wait_for_notification_delivery,
)
from .models import AdminOperationJob

from .notification_redelivery import refresh_notification_redeliveries as refresh
from .notification_redelivery import execute_notification_redelivery
from .academic_evaluation import execute_formula_recalculation
from .academic_enrollment import execute_bulk_promotion
from .historical_intake import execute_historical_intake
from .legacy_user_import import execute_legacy_user_import
from .utils.attendance import execute_attendance_reconcile
from .academic_copy import execute_course_copy
from .evaluation_export import execute_evaluation_export
from .report_export import execute_report_export
from .storage_jobs import execute_storage_delete, execute_storage_rename, execute_storage_usage
from .utils.csv_export import execute_quiz_export


logger = logging.getLogger(__name__)

RECOVERY_LIMIT = 50
RECOVERY_RETRY_SECONDS = 180


@shared_task(ignore_result=True, name="management_system.admin_operation_tasks.refresh_notification_redeliveries")
def refresh_notification_redeliveries() -> dict:

    return {"checked": refresh()}


def execute_admin_operation(job: AdminOperationJob, token: uuid.UUID) -> dict:
    """Run the owning domain executor for this job kind.

    Each operation adds its explicit branch here (or in its owning module) when
    that operation moves onto the ledger. The ledger itself never interprets
    domain input. Until an operation is wired, a delivered job fails truthfully
    instead of reporting success.
    """

    if job.kind == AdminOperationJob.Kind.NOTIFICATION_REDELIVERY:

        return execute_notification_redelivery(job, token)
    if job.kind == AdminOperationJob.Kind.FORMULA_RECALCULATION:

        return execute_formula_recalculation(job, token)
    if job.kind == AdminOperationJob.Kind.BULK_PROMOTION:

        return execute_bulk_promotion(job, token)
    if job.kind == AdminOperationJob.Kind.HISTORICAL_INTAKE:

        return execute_historical_intake(job, token)
    if job.kind == AdminOperationJob.Kind.LEGACY_USER_IMPORT:

        return execute_legacy_user_import(job, token)
    if job.kind == AdminOperationJob.Kind.ATTENDANCE_RECONCILE:

        return execute_attendance_reconcile(job, token)
    if job.kind == AdminOperationJob.Kind.COURSE_COPY:

        return execute_course_copy(job, token)
    if job.kind == AdminOperationJob.Kind.EVALUATION_EXPORT:

        return execute_evaluation_export(job, token)
    if job.kind == AdminOperationJob.Kind.GRADES_REPORT_EXPORT:

        return execute_report_export(job, token)
    if job.kind == AdminOperationJob.Kind.STORAGE_RENAME:

        return execute_storage_rename(job, token)
    if job.kind == AdminOperationJob.Kind.STORAGE_USAGE_REFRESH:

        return execute_storage_usage(job, token)
    if job.kind == AdminOperationJob.Kind.STORAGE_DELETE:

        return execute_storage_delete(job, token)
    if job.kind in {
        AdminOperationJob.Kind.QUIZ_DETAIL_EXPORT,
        AdminOperationJob.Kind.QUIZ_SUMMARY_EXPORT,
    }:

        return execute_quiz_export(job, token)

    raise AdminOperationError(
        "executor_not_available",
        _("This management operation has not been enabled yet."),
    )


@shared_task(
    acks_late=True,
    reject_on_worker_lost=True,
    name="management_system.admin_operation_tasks.run_admin_operation",
)
def run_admin_operation(job_id: str) -> dict:
    token = uuid.uuid4()
    job, claimed = claim_admin_operation(job_id, token)
    if job is None:
        return {"status": "missing", "job_id": job_id}
    if not claimed:
        # A live duplicate delivery or a worker that lost the lease.
        return {"status": "skipped", "job_id": job_id}
    try:
        with translation.override(job.language or "ar"), admin_operation_heartbeat(job.pk, token):
            require_admin_operation_actor(job)
            outcome = execute_admin_operation(job, token) or {}
        status = outcome.get("status", AdminOperationJob.Status.COMPLETED)
        if status == AdminOperationJob.Status.WAITING:
            waiting = wait_for_notification_delivery(job.pk, token, outcome.get("result") or {})
            return {"status": "waiting" if waiting else "lease_lost", "job_id": job_id}
        if status not in {
            AdminOperationJob.Status.COMPLETED,
            AdminOperationJob.Status.PARTIAL,
        }:
            status = AdminOperationJob.Status.COMPLETED
        finished = finish_admin_operation(
            job.pk,
            token,
            status=status,
            result=outcome.get("result") or {},
        )
        return {"status": "finished" if finished else "lease_lost", "job_id": job_id}
    except AdminOperationError as exc:
        fail_admin_operation(job.pk, token, exc.message)
        return {"status": "failed", "code": exc.code, "job_id": job_id}
    except Exception as exc:  # noqa: BLE001 - final failure boundary
        logger.exception("admin operation failed", extra={"job_id": str(job_id)})
        with translation.override(job.language or "ar"):
            fail_admin_operation(
                job.pk,
                token,
                _("The operation failed. Review the results and try again."),
            )
        return {"status": "failed", "code": type(exc).__name__, "job_id": job_id}


@shared_task(
    ignore_result=True,
    name="management_system.admin_operation_tasks.recover_admin_operations",
)
def recover_admin_operations() -> dict:
    """Requeue missing deliveries and expired leases without queue flooding."""

    now = timezone.now()
    retry_before = now - timedelta(seconds=RECOVERY_RETRY_SECONDS)
    with transaction.atomic():
        queued = list(
            AdminOperationJob.objects.filter(
                status=AdminOperationJob.Status.QUEUED,
                updated_at__lte=retry_before,
            )
            .order_by("updated_at", "created_at")
            .select_for_update(skip_locked=True)
            .values_list("pk", flat=True)[:RECOVERY_LIMIT]
        )
        expired = list(
            AdminOperationJob.objects.filter(
                status=AdminOperationJob.Status.RUNNING,
                lease_until__lte=now,
                updated_at__lte=retry_before,
            )
            .order_by("lease_until", "updated_at")
            .select_for_update(skip_locked=True)
            .values_list("pk", flat=True)[:RECOVERY_LIMIT]
        )
        # updated_at doubles as a dispatch cooldown, not task progress. Claim,
        # batch progress and completion also update it, so a worker takeover
        # becomes eligible immediately when its lease later expires.
        AdminOperationJob.objects.filter(pk__in=[*queued, *expired]).update(updated_at=now)
    for job_id in dict.fromkeys([*queued, *expired]):
        try:
            run_admin_operation.apply_async(args=[str(job_id)], queue="admin")
        except Exception:
            logger.warning(
                "admin operation requeue failed",
                extra={"job_id": str(job_id)},
                exc_info=True,
            )
    return {"queued": len(queued), "expired": len(expired)}


@shared_task(
    ignore_result=True,
    name="management_system.admin_operation_tasks.cleanup_admin_operation_artifacts",
)
def cleanup_admin_operation_artifacts() -> dict:
    """Delete expired private job inputs from one bounded provider page."""


    deleted = purge_expired_admin_operation_artifacts()
    return {"deleted": deleted}
