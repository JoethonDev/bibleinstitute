"""
Celery shared tasks for application notification emails.
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

User = get_user_model()
logger = getLogger(__name__)

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
