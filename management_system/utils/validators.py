import re
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

def normalize_phone(value):
    cleaned = re.sub(r'[\s\-\+\(\)]', '', value)
    if cleaned.startswith('00'):
        cleaned = '+' + cleaned[2:]
    if not cleaned.startswith('+'):
        cleaned = '+' + cleaned
    return cleaned
