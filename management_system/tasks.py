"""
Celery shared tasks for application notification emails and quiz recalculation.
Autodiscovered by learning_platform.celery.app.
"""
from __future__ import annotations

import os
from logging import getLogger

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils import translation
from django.utils import timezone
from .admin_tables import quiz_queryset
from .models import Quiz
from .quiz_grading import regrade_quiz_submissions

User = get_user_model()
logger = getLogger(__name__)

@shared_task(ignore_result=True, acks_late=True, reject_on_worker_lost=True)
def recalculate_quizzes_task(job_id: str) -> None:
    from datetime import timedelta
    from uuid import uuid4
    from django.db import transaction
    from .models import QuizOperationJob
    from .quiz_attempts import reconcile_snapshots

    token = uuid4()
    with transaction.atomic():
        job = QuizOperationJob.objects.select_for_update().get(pk=job_id)
        if job.status in (job.Status.COMPLETED, job.Status.FAILED):
            return
        if job.status == job.Status.RUNNING and job.lease_until and job.lease_until > timezone.now():
            return
        job.status, job.lease_token = job.Status.RUNNING, token
        job.lease_until = timezone.now() + timedelta(minutes=2)
        job.save()
    try:
        while True:
            # Lock progress and quiz mutation together. A crash rolls back both;
            # a reclaimed worker cannot replay an already committed batch.
            with transaction.atomic(), translation.override(job.language):
                job = QuizOperationJob.objects.select_for_update().get(pk=job_id)
                if job.lease_token != token or job.status != job.Status.RUNNING:
                    return
                if not User.objects.filter(pk=job.actor_id, is_active=True, role__role="admin").exists():
                    raise PermissionError("Quiz operations require an active administrator")
                selection = job.selection
                target = quiz_queryset(selection["scope"]).filter(pk__gt=job.last_pk, pk__lte=job.upper_pk)
                target = target.exclude(pk__in=selection["excluded_ids"]) if selection["select_all"] else target.filter(pk__in=selection["ids"])
                quiz_id = target.order_by("pk").values_list("pk", flat=True).first()
                if quiz_id is None:
                    job.skipped = max(job.total - job.processed, 0)
                    job.status = job.Status.COMPLETED
                    job.lease_until = None
                    job.save()
                    return
                more = False
                try:
                    if job.kind == job.Kind.RECONCILE:
                        count, more = reconcile_snapshots(quiz_id)
                    else:
                        count = regrade_quiz_submissions(quiz_id, None)
                    job.answers += count
                except Quiz.DoesNotExist:
                    pass
                if not more:
                    job.last_pk = quiz_id
                    job.processed += 1
                job.lease_until = timezone.now() + timedelta(minutes=2)
                job.save()
    except Exception:
        logger.exception("Quiz operation %s failed", job_id)
        QuizOperationJob.objects.filter(pk=job_id, lease_token=token).update(
            status=QuizOperationJob.Status.FAILED, lease_until=None,
            error=str(_("Quiz operation failed. Completed work remains saved.")), updated_at=timezone.now())
        raise


@shared_task(ignore_result=True)
def recover_quiz_operations() -> None:
    from .models import QuizOperationJob
    from django.db.models import Q
    jobs = QuizOperationJob.objects.filter(
        Q(status=QuizOperationJob.Status.QUEUED) |
        Q(status=QuizOperationJob.Status.RUNNING, lease_until__lte=timezone.now())
    ).order_by("updated_at").values_list("pk", flat=True)[:100]
    for job_id in jobs:
        recalculate_quizzes_task.delay(str(job_id))


@shared_task(ignore_result=True)
def reconcile_closed_quiz_snapshots() -> None:
    from .quiz_attempts import due_snapshots, reconcile_snapshots
    quiz_ids = list(due_snapshots().order_by("quiz_id").values_list("quiz_id", flat=True).distinct()[:50])
    for quiz_id in quiz_ids:
        try:
            more = True
            while more:
                _count, more = reconcile_snapshots(quiz_id)
        except Quiz.DoesNotExist:
            pass
        except Exception:
            logger.exception("Snapshot reconciliation failed for quiz %s", quiz_id)

SITE_DOMAIN = os.getenv("DJANGO_SITE_DOMAIN", "http://localhost:8000")


def _send_html_email(subject: str, template_name: str, context: dict, recipient_email: str) -> None:
    """Low-level render-and-send helper used only by tasks. Does not catch exceptions."""
    ctx = {
        **context,
        "site_domain": SITE_DOMAIN.rstrip("/"),
        "static_asset_version": settings.STATIC_ASSET_VERSION,
    }
    html_message = render_to_string(template_name, ctx)
    send_mail(
        subject=subject,
        message="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
        html_message=html_message,
        fail_silently=False,
    )


def _absolute_login_url() -> str:
    return f"{SITE_DOMAIN}{reverse(settings.LOGIN_URL_REVERSE)}"


def _send_or_skip(user_id: int, subject: str, template_name: str, extra_context: dict | None = None) -> None:
    """Load user by pk, skip cleanly if missing or no email, then send."""
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("Email task skipped: user %s no longer exists", user_id)
        return
    if not user.email:
        logger.info("Email task skipped: user %s has no email", user_id)
        return
    context = {
        "name": user.get_full_name() or user.username,
        **(extra_context or {}),
    }
    _send_html_email(subject, template_name, context, user.email)


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    ignore_result=True,
)
def send_application_received_task(user_id: int) -> None:
    _send_or_skip(
        user_id,
        subject=_("Application Received"),
        template_name="emails/application_received.html",
    )


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    ignore_result=True,
)
def send_application_activated_task(user_id: int) -> None:
    _send_or_skip(
        user_id,
        subject=_("Application Approved"),
        template_name="emails/application_activated.html",
        extra_context={"login_url": _absolute_login_url()},
    )


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    ignore_result=True,
)
def send_application_declined_task(user_id: int) -> None:
    _send_or_skip(
        user_id,
        subject=_("Application Status"),
        template_name="emails/application_declined.html",
    )
