"""Browser password recovery.

Django's token generation/validation is used unchanged. This module adds the
site's branded queued email, a per-address request throttle, a host-header
independent reset URL, and mobile-session revocation after a reset.
"""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import PasswordResetForm
from django.core.cache import cache
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy

from .mobile_auth import revoke_user_mobile_access

logger = logging.getLogger(__name__)

#: One address may request this many links per window. Keyed on the address (not
#: the client IP) because REMOTE_ADDR is the proxy address in production, so an
#: IP key would lock every user out of recovery at once.
RESET_REQUEST_WINDOW_SECONDS = 15 * 60
RESET_REQUEST_LIMIT = 3


def _throttle_key(email: str) -> str:
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return f"password-reset:{digest}"


def password_reset_request_allowed(email: str) -> bool:
    """Count this request and report whether the window still allows sending."""
    key = _throttle_key(email)
    try:
        if cache.add(key, 1, timeout=RESET_REQUEST_WINDOW_SECONDS):
            return True
        return cache.incr(key) <= RESET_REQUEST_LIMIT
    except ValueError:
        cache.set(key, 1, timeout=RESET_REQUEST_WINDOW_SECONDS)
        return True
    except Exception:
        logger.warning(
            "password reset throttle unavailable",
            extra={"event": "password_reset_throttle_error"},
        )
        return True


def _reset_url(context: dict) -> str:
    """Build the absolute reset link from the canonical site domain.

    Falls back to the request host only when no canonical domain is configured,
    so a forged Host header cannot point the emailed link at another site.
    """
    path = reverse(
        "password_reset_confirm",
        kwargs={"uidb64": context["uid"], "token": context["token"]},
    )
    site_domain = (getattr(settings, "DJANGO_SITE_DOMAIN", "") or "").rstrip("/")
    if site_domain:
        return f"{site_domain}{path}"
    return f"{context['protocol']}://{context['domain']}{path}"


class QueuedPasswordResetForm(PasswordResetForm):
    """Hand the reset link to the email worker instead of blocking the request."""

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        user = context.get("user")
        user_id = getattr(user, "pk", None)
        if user_id is None:
            return

        from .tasks import send_password_reset_email_task

        reset_url = _reset_url(context)
        try:
            send_password_reset_email_task.delay(user_id, reset_url)
        except Exception:
            logger.exception(
                "password reset email could not be queued",
                extra={"event": "password_reset_queue_failed", "user_id": user_id},
            )
            try:
                send_password_reset_email_task(user_id, reset_url)
            except Exception:
                logger.exception(
                    "password reset email could not be sent",
                    extra={"event": "password_reset_send_failed", "user_id": user_id},
                )


class PasswordResetView(auth_views.PasswordResetView):
    """Request a reset link. The response never reveals whether the email exists."""

    template_name = "password_reset/request.html"
    form_class = QueuedPasswordResetForm
    success_url = reverse_lazy("password_reset_done")

    def form_valid(self, form):
        email = (form.cleaned_data.get("email") or "").strip()
        if email and not password_reset_request_allowed(email):
            logger.info(
                "password reset throttled",
                extra={"event": "password_reset_throttled"},
            )
            return redirect(self.get_success_url())
        return super().form_valid(form)


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Set a new password, then revoke every mobile bearer session."""

    template_name = "password_reset/confirm.html"
    success_url = reverse_lazy("password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = getattr(form, "user", None)
        user_id = getattr(user, "pk", None)
        if user_id is not None:
            try:
                revoke_user_mobile_access(user)
            except Exception:
                logger.exception(
                    "mobile access revoke failed after password reset",
                    extra={"event": "password_reset_revoke_failed", "user_id": user_id},
                )
        logger.info(
            "password reset completed",
            extra={"event": "password_reset_completed", "user_id": user_id},
        )
        return response
