from datetime import datetime
import zoneinfo

from django.conf import settings
from django.utils import timezone


def application_timezone() -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(settings.TIME_ZONE)


def user_timezone(user) -> zoneinfo.ZoneInfo:
    name = getattr(user, "time_zone", None) or settings.TIME_ZONE
    try:
        return zoneinfo.ZoneInfo(name)
    except zoneinfo.ZoneInfoNotFoundError:
        return application_timezone()


def user_time_zone_choices():
    zones = sorted(
        zone for zone in zoneinfo.available_timezones()
        if "/" in zone and not zone.startswith(("Etc/", "posix/", "right/"))
    )
    default_zone = settings.TIME_ZONE
    return [(zone, zone.replace("_", " ")) for zone in ([default_zone] + [zone for zone in zones if zone != default_zone])]


def ensure_aware(value: datetime) -> datetime:
    if timezone.is_naive(value):
        return timezone.make_aware(value, application_timezone())
    return value


def parse_application_datetime(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    return timezone.make_aware(parsed, application_timezone())


def format_application_datetime(value: datetime, output_format: str) -> str:
    return timezone.localtime(ensure_aware(value), application_timezone()).strftime(output_format)


def format_user_datetime(value: datetime, user, output_format: str) -> str:
    return timezone.localtime(ensure_aware(value), user_timezone(user)).strftime(output_format)
