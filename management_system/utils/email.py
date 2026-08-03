import os
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from logging import getLogger

logger = getLogger(__name__)

SITE_DOMAIN = os.getenv('DJANGO_SITE_DOMAIN', 'http://localhost:8000')


def _send_html_email(subject, template_name, context, recipient_email):
    context = {**context, "site_domain": SITE_DOMAIN.rstrip("/")}
    html_message = render_to_string(template_name, context)
    send_mail(
        subject=subject,
        message="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
        html_message=html_message,
        fail_silently=True,
    )


def _absolute_login_url():
    return f"{SITE_DOMAIN}{reverse(settings.LOGIN_URL_REVERSE)}"


def send_application_received(user):
    if not user.email:
        return
    try:
        _send_html_email(
            subject=_("Application Received"),
            template_name="emails/application_received.html",
            context={"name": user.get_full_name() or user.username},
            recipient_email=user.email,
        )
    except Exception as e:
        logger.error(f"Failed to send application received email to {user.email}: {e}")


def send_application_activated(user):
    if not user.email:
        return
    try:
        _send_html_email(
            subject=_("Application Approved"),
            template_name="emails/application_activated.html",
            context={
                "name": user.get_full_name() or user.username,
                "login_url": _absolute_login_url(),
            },
            recipient_email=user.email,
        )
    except Exception as e:
        logger.error(f"Failed to send activation email to {user.email}: {e}")


def send_application_declined(user):
    if not user.email:
        return
    try:
        _send_html_email(
            subject=_("Application Status"),
            template_name="emails/application_declined.html",
            context={"name": user.get_full_name() or user.username},
            recipient_email=user.email,
        )
    except Exception as e:
        logger.error(f"Failed to send decline email to {user.email}: {e}")
