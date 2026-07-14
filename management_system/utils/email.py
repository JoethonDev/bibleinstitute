from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from logging import getLogger

logger = getLogger(__name__)


def _send_html_email(subject, template_name, context, recipient_email):
    html_message = render_to_string(template_name, context)
    send_mail(
        subject=subject,
        message="",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
        html_message=html_message,
        fail_silently=True,
    )


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
                "login_url": f"{settings.DEFAULT_FROM_EMAIL}",
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
