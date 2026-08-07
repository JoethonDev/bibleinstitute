import re
import unicodedata
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

def validate_egyptian_national_id(value):
    if not re.fullmatch(r'\d{14}', value):
        raise ValidationError(_("National ID must be exactly 14 digits."))

def validate_passport(value):
    if not re.fullmatch(r'[A-Za-z0-9]{6,20}', value):
        raise ValidationError(_("Passport number must be 6–20 alphanumeric characters."))

def validate_identity_by_type(identity_type, identity_number):
    if identity_type == "national_id":
        validate_egyptian_national_id(identity_number)
    elif identity_type == "passport":
        validate_passport(identity_number)

def normalize_phone(value: str) -> str | None:
    """Return a canonical E.164-like phone or ``None`` when ambiguous.

    Explicit ``+`` (or ``00``) international numbers keep their supplied
    country code and are never re-interpreted as Egyptian national numbers;
    a leading zero after an international prefix is rejected. Only an
    unprefixed Egyptian national ``01xxxxxxxxx`` number is converted to the
    ``+20`` form.
    """
    if not isinstance(value, str):
        return None
    translated = []
    for char in value.strip():
        try:
            translated.append(str(unicodedata.digit(char)))
        except (TypeError, ValueError):
            translated.append(char)
    cleaned = re.sub(r'[\s\-\(\)]', '', ''.join(translated))
    if cleaned.startswith('00'):
        cleaned = '+' + cleaned[2:]
    had_international_prefix = cleaned.startswith('+')
    digits = cleaned[1:] if had_international_prefix else cleaned
    if not digits.isdigit() or not 7 <= len(digits) <= 15:
        return None
    if digits.startswith('0'):
        if had_international_prefix:
            return None
        if len(digits) == 11 and digits.startswith('01'):
            digits = '20' + digits[1:]
        else:
            return None
    elif not had_international_prefix:
        return None
    return '+' + digits
