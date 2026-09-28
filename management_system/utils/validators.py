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


def normalize_username(value: str) -> str:
    """Canonicalize a username by Unicode normalization, trimming, and casing."""
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFKC", value).strip().lower()

def normalize_phone(value: str) -> str | None:
    """Return a canonical E.164-like phone or ``None`` when ambiguous.

    Explicit ``+`` (or ``00``) international numbers keep their supplied
    country code and are never re-interpreted as Egyptian national numbers;
    common formatting marks are ignored. Egyptian numbers are also accepted
    with a missing ``+`` before country code 20 or a trunk zero after it.
    """
    if not isinstance(value, str):
        return None
    translated = []
    for char in unicodedata.normalize("NFKC", value).strip():
        # Contact payloads may contain bidi/zero-width formatting characters
        # copied from localized address books; they are not phone digits.
        if unicodedata.category(char) == "Cf":
            continue
        try:
            translated.append(str(unicodedata.digit(char)))
        except (TypeError, ValueError):
            translated.append(char)
    cleaned = "".join(
        char for char in translated
        if not char.isspace() and unicodedata.category(char) != "Pd" and char not in "()."
    )
    if cleaned.startswith('00'):
        cleaned = '+' + cleaned[2:]
    had_international_prefix = cleaned.startswith('+')
    digits = cleaned[1:] if had_international_prefix else cleaned
    if not digits.isdigit():
        return None

    # Some address books omit '+' from the Egyptian international form or
    # retain the domestic trunk zero after +20. Normalize only the explicit
    # Egyptian country-code shapes; other unprefixed international numbers
    # remain ambiguous and are rejected.
    if digits.startswith("20") and len(digits) == 13 and digits[2:4] == "01":
        digits = digits[:2] + digits[3:]
        had_international_prefix = True
    elif not had_international_prefix and digits.startswith("20") and len(digits) == 12:
        had_international_prefix = True

    if digits.startswith('0'):
        if had_international_prefix:
            return None
        if len(digits) == 11 and digits.startswith('01'):
            digits = '20' + digits[1:]
        else:
            return None
    elif not had_international_prefix:
        return None
    if not 7 <= len(digits) <= 15:
        return None
    return '+' + digits
