"""Boundary-only Telegram bot configuration form.

The plaintext bot token is never a model field, so this deliberately uses a
plain ``forms.Form`` instead of a ModelForm: the token must never be loaded
from the database into an instance, rendered after POST, or logged by the form
itself. Persistence, encryption, and Telegram API calls belong to the admin
view/service layers.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .telegram.attachments import (
    CAPTION_MAX_LENGTH,
    SUPPORT_ATTACHMENT_ACCEPT,
    classify_attachment,
)


class MultipleFileInput(forms.ClearableFileInput):
    """File input that accepts several files for one optional field."""

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Validate each upload returned by the multi-file widget."""

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [forms.FileField.clean(self, item, initial) for item in data]
        if data in (None, ""):
            return []
        return [forms.FileField.clean(self, data, initial)]


class TelegramBotConfigForm(forms.Form):
    """Accept an optional replacement Telegram bot token without persisting it."""

    token = forms.CharField(required=False, label=_("Telegram bot token"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "spellcheck": "false", "class": "form-control"}))

    def clean_token(self) -> str:
        token = self.cleaned_data.get("token", "")
        if not token:
            return ""
        token = token.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in token):
            raise ValidationError(_("The token must not contain newlines or control characters."))
        if len(token) > 512:
            raise ValidationError(_("The token must be 512 characters or fewer."))
        return token


class TelegramSupportReplyForm(forms.Form):
    """Accept one bounded admin web-chat reply without database or delivery work.

    A reply carries either text or one Telegram-supported file (JPEG/PNG photo,
    MP4 video, or PDF document). This is a trust-boundary validator only.
    View-layer admin authorization, conversation ownership of
    ``reply_to_message_id``, and the shared claim/reply service belong
    to ``management_system/telegram/support.py``.
    """

    message = forms.CharField(
        required=False,
        label=_("Reply message"),
        max_length=4000,
        error_messages={
            "max_length": _("The reply message must be 4,000 characters or fewer."),
        },
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "maxlength": 4000,
                "aria-label": _("Reply message"),
                "class": "aw-textarea",
            }
        ),
    )
    attachment = forms.FileField(
        required=False,
        label=_("Attachment"),
        allow_empty_file=False,
        error_messages={
            "empty": _("Empty files are not accepted."),
        },
        widget=forms.ClearableFileInput(
            attrs={
                "class": "aw-file-input",
                "accept": SUPPORT_ATTACHMENT_ACCEPT,
                "aria-describedby": "telegram-support-attachment-help",
            }
        ),
    )
    reply_to_message_id = forms.IntegerField(
        required=False,
        min_value=1,
        error_messages={
            "invalid": _("The reply message ID must be a positive integer."),
            "min_value": _("The reply message ID must be a positive integer."),
        },
        widget=forms.HiddenInput(),
    )

    def clean_message(self) -> str:
        return self.cleaned_data.get("message", "").strip()

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if not attachment:
            return None
        head = attachment.read(16)
        attachment.seek(0)
        try:
            classify_attachment(
                filename=str(getattr(attachment, "name", "") or ""),
                size=int(getattr(attachment, "size", 0) or 0),
                head=head,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return attachment

    def clean(self):
        cleaned = super().clean()
        message = cleaned.get("message", "")
        attachment = cleaned.get("attachment")
        if not message and not attachment and not self.errors:
            self.add_error("message", _("A reply message or an attachment is required."))
        if attachment and len(message) > CAPTION_MAX_LENGTH:
            self.add_error(
                "message",
                _("Attachments support a message of 1,024 characters or fewer."),
            )
        return cleaned


class TelegramBroadcastForm(forms.Form):
    """Accept one bounded active-year broadcast draft without persistence work.

    This is a trust-boundary validator only. View-layer admin authorization,
    active-year/level validation, recipient resolution, attachment upload, and
    the confirmation transaction belong to the Phase 7 admin views and
    broadcast service. ``level`` choices are injected by the view so this form
    never queries the database during construction; the injected choice list
    represents the active academic year's levels and must never include a
    historical year.
    """

    message = forms.CharField(
        required=True,
        label=_("Broadcast message"),
        max_length=4000,
        error_messages={
            "required": _("A broadcast message is required."),
            "max_length": _("The broadcast message must be 4,000 characters or fewer."),
        },
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "maxlength": 4000,
                "aria-label": _("Broadcast message"),
                "class": "aw-textarea tg-broadcast-textarea",
            }
        ),
    )
    level = forms.ChoiceField(
        required=True,
        label=_("Target level"),
        widget=forms.Select(
            attrs={
                "aria-describedby": "telegram-broadcast-level-help",
                "class": "aw-select tg-broadcast-select",
            }
        ),
    )
    attachments = MultipleFileField(
        required=False,
        label=_("Attachments"),
        allow_empty_file=False,
        error_messages={
            "empty": _("Empty files are not accepted."),
        },
        widget=MultipleFileInput(attrs={"class": "tg-broadcast-files"}),
    )

    def __init__(self, *args, level_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if level_choices is not None:
            self.fields["level"].choices = level_choices

    def clean_message(self) -> str:
        message = self.cleaned_data.get("message", "")
        message = message.strip()
        if not message:
            raise ValidationError(_("A broadcast message is required."))
        return message
