from django.core.mail import send_mail
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from logging import getLogger

logger = getLogger(__name__)

def send_application_received(user):
    if not user.email:
        return
    try:
        send_mail(
            subject=_("Application Received"),
            message=_("Dear %(name)s, your application has been received.") % {"name": user.get_full_name() or user.username},
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )
    except Exception as e:
        logger.error(f"Failed to send application received email to {user.email}: {e}")

def send_application_activated(user):
    if not user.email:
        return
    try:
        send_mail(
            subject=_("Application Approved"),
            message=_("Dear %(name)s, your application has been approved. You can now log in.") % {"name": user.get_full_name() or user.username},
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )
    except Exception as e:
        logger.error(f"Failed to send activation email to {user.email}: {e}")

def send_application_declined(user):
    if not user.email:
        return
    try:
        send_mail(
            subject=_("Application Declined"),
            message=_("Dear %(name)s, your application has been declined.") % {"name": user.get_full_name() or user.username},
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )
    except Exception as e:
        logger.error(f"Failed to send decline email to {user.email}: {e}")
