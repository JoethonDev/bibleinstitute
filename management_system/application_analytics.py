"""Canonical application-analytics scope, filter, and aggregation rules.

The analytics dashboard and its CSV export both build their dataset through
:func:`build_analytics_dataset`, so the on-screen metrics and the exported
rows can never apply a different population, date window, or filter set.

Scope rule (operator-approved): an application is every student sign-up
(``application_status`` in pending/active/declined). When a specific academic
scope is selected, the application belongs to it when the student is enrolled
in that exact ``AcademicYearLevel`` or when the student has no enrollment at
all (pending applicants targeting the current intake). Applications whose
student only has enrollments outside the selected scope are excluded.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from django.db.models import Count, Exists, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import get_language

from .models import AcademicYearLevel, Enrollment, User
from .utils.csv_export import _csv_safe_cell
from .utils.timezones import format_application_datetime

ANALYTICS_STATUSES = tuple(value for value, _label in User.APPLICATION_STATUSES)
ANALYTICS_STUDY_MODES = tuple(value for value, _label in User.STUDY_MODES)
ANALYTICS_GROUPINGS = ("day", "week", "month")
ANALYTICS_SCOPE_ALL = "all"
CITY_CHART_LIMIT = 12
DAY_GROUP_MAX_SPAN = 366

# A known Sunday, used to render localized weekday names in Sunday-first order.
_WEEKDAY_ANCHOR = date(2000, 1, 2)


def analytics_scope_queryset() -> QuerySet[AcademicYearLevel]:
    """Academic scopes ordered active year first, then newest year, then level."""
    return (
        AcademicYearLevel.objects.select_related("academic_year", "level")
        .order_by("-academic_year__is_active", "-academic_year__ordering", "level__ordering")
    )


def default_analytics_scope() -> AcademicYearLevel | None:
    """The lowest level opened in the single active academic year."""
    return (
        AcademicYearLevel.objects.select_related("academic_year", "level")
        .filter(academic_year__is_active=True)
        .order_by("level__ordering")
        .first()
    )


def resolve_analytics_scope(value) -> AcademicYearLevel | None:
    """Resolve ``?scope=``; ``None`` means all applications.

    An explicit ``all`` disables the enrollment scope, a valid academic-scope
    id selects it, and anything missing or invalid falls back to the default
    (active year, lowest level).
    """
    if value == ANALYTICS_SCOPE_ALL:
        return None
    if value and str(value).isdigit() and len(str(value)) <= 18:
        scope = (
            AcademicYearLevel.objects.select_related("academic_year", "level")
            .filter(pk=int(value))
            .first()
        )
        if scope is not None:
            return scope
    return default_analytics_scope()


def application_scope_queryset(scope: AcademicYearLevel | None) -> QuerySet[User]:
    """Student applications inside ``scope`` (or every application for ``None``)."""
    applications = User.objects.filter(
        role__role="student",
        application_status__in=ANALYTICS_STATUSES,
    )
    if scope is None:
        return applications
    return applications.filter(
        Q(Exists(Enrollment.objects.filter(student=OuterRef("pk"), academic_year_level=scope)))
        | ~Q(Exists(Enrollment.objects.filter(student=OuterRef("pk"))))
    )


def analytics_city_options(queryset: QuerySet[User]) -> list[str]:
    """Distinct non-empty city values of the scope population, alphabetized."""
    return list(
        queryset.exclude(city__isnull=True)
        .exclude(city="")
        .values_list("city", flat=True)
        .distinct()
        .order_by("city")
    )


def _parse_iso_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def analytics_date_range(scope: AcademicYearLevel | None, from_param, to_param) -> tuple[date, date]:
    """Validated date window; defaults to the scope year or the last year."""
    today = timezone.localdate()
    if scope is not None:
        default_from = scope.academic_year.starts_on
        default_to = min(today, scope.academic_year.ends_on)
        if default_to < default_from:
            default_to = scope.academic_year.ends_on
    else:
        default_from = today - timedelta(days=364)
        default_to = today
    date_from = _parse_iso_date(from_param) or default_from
    date_to = _parse_iso_date(to_param) or default_to
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def resolve_analytics_group(value, date_from: date, date_to: date) -> str:
    """Validated bucket grouping, bounded so the page never renders unbounded points."""
    span = (date_to - date_from).days
    if value in ANALYTICS_GROUPINGS:
        group = value
    else:
        group = "day" if span <= 62 else ("week" if span <= 365 else "month")
    if group == "day" and span > DAY_GROUP_MAX_SPAN:
        group = "week"
    if group == "week" and span > 365 * 3:
        group = "month"
    return group


def apply_analytics_filters(
    queryset: QuerySet[User],
    *,
    date_from: date,
    date_to: date,
    status: str,
    study_mode: str,
    cities: list[str],
) -> QuerySet[User]:
    """Apply the validated dashboard filters to the application population."""
    queryset = queryset.filter(date_joined__date__gte=date_from, date_joined__date__lte=date_to)
    if status in ANALYTICS_STATUSES:
        queryset = queryset.filter(application_status=status)
    if study_mode in ANALYTICS_STUDY_MODES:
        queryset = queryset.filter(study_mode=study_mode)
    if cities:
        queryset = queryset.filter(city__in=cities)
    return queryset


def build_analytics_dataset(request) -> dict:
    """Validated scope, window, filters, and population shared by page and export."""
    scope = resolve_analytics_scope(request.GET.get("scope"))
    base_queryset = application_scope_queryset(scope)
    city_options = analytics_city_options(base_queryset)

    date_from, date_to = analytics_date_range(
        scope, request.GET.get("from"), request.GET.get("to")
    )
    group = resolve_analytics_group(request.GET.get("group"), date_from, date_to)

    status = request.GET.get("status")
    status = status if status in ANALYTICS_STATUSES else ANALYTICS_SCOPE_ALL
    study_mode = request.GET.get("study_mode")
    study_mode = study_mode if study_mode in ANALYTICS_STUDY_MODES else ANALYTICS_SCOPE_ALL
    valid_cities = set(city_options)
    cities = [city for city in request.GET.getlist("city") if city in valid_cities]

    queryset = apply_analytics_filters(
        base_queryset,
        date_from=date_from,
        date_to=date_to,
        status=status,
        study_mode=study_mode,
        cities=cities,
    )
    return {
        "scope": scope,
        "queryset": queryset,
        "city_options": city_options,
        "date_from": date_from,
        "date_to": date_to,
        "group": group,
        "status": status,
        "study_mode": study_mode,
        "cities": cities,
    }


def analytics_daily_counts(queryset: QuerySet[User]) -> dict[date, list[int]]:
    """One SQL group-by: ``{day: [total, online, offline]}`` with timezone-aware dates."""
    counts: dict[date, list[int]] = defaultdict(lambda: [0, 0, 0])
    rows = (
        queryset.annotate(bucket=TruncDate("date_joined"))
        .values("bucket", "study_mode")
        .annotate(total=Count("id"))
    )
    for row in rows:
        bucket = row["bucket"]
        if isinstance(bucket, datetime):
            bucket = timezone.localtime(bucket).date() if timezone.is_aware(bucket) else bucket.date()
        entry = counts[bucket]
        entry[0] += row["total"]
        if row["study_mode"] == "online":
            entry[1] += row["total"]
        elif row["study_mode"] == "offline":
            entry[2] += row["total"]
    return counts


def _period_start(group: str, day: date) -> date:
    if group == "week":
        return day - timedelta(days=day.weekday())
    if group == "month":
        return day.replace(day=1)
    return day


def _period_starts(group: str, date_from: date, date_to: date) -> list[date]:
    if group == "day":
        return [date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)]
    if group == "week":
        start = date_from - timedelta(days=date_from.weekday())
        step = timedelta(days=7)
    else:
        start = date_from.replace(day=1)
        step = None
    periods = []
    while start <= date_to:
        periods.append(start)
        if step is not None:
            start += step
        elif start.month == 12:
            start = start.replace(year=start.year + 1, month=1)
        else:
            start = start.replace(month=start.month + 1)
    return periods


def _period_label(group: str, period: date) -> str:
    if group == "month":
        return formats.date_format(period, "F Y")
    return formats.date_format(period, "j M Y")


def analytics_series_rows(daily: dict[date, list[int]], group: str, date_from: date, date_to: date) -> dict:
    """Bucket the bounded daily group-by into the requested grouping with cumulative totals."""
    merged: dict[date, list[int]] = defaultdict(lambda: [0, 0, 0])
    for day, (total, online, offline) in daily.items():
        bucket = merged[_period_start(group, day)]
        bucket[0] += total
        bucket[1] += online
        bucket[2] += offline

    rows = []
    running = 0
    for period in _period_starts(group, date_from, date_to):
        total, online, offline = merged.get(period, (0, 0, 0))
        running += total
        rows.append(
            {
                "label": _period_label(group, period),
                "total": total,
                "online": online,
                "offline": offline,
                "cumulative": running,
            }
        )
    return {
        "rows": rows,
        "labels": [row["label"] for row in rows],
        "total": [row["total"] for row in rows],
        "online": [row["online"] for row in rows],
        "offline": [row["offline"] for row in rows],
        "cumulative": [row["cumulative"] for row in rows],
    }


def analytics_weekday_buckets(daily: dict[date, list[int]]) -> dict:
    """Weekday distribution derived from the bounded daily counts (Sunday first)."""
    values = [0] * 7
    for day, (total, _online, _offline) in daily.items():
        values[(day.weekday() + 1) % 7] += total
    labels = [formats.date_format(_WEEKDAY_ANCHOR + timedelta(days=index), "l") for index in range(7)]
    return {"labels": labels, "values": values}


def analytics_breakdowns(queryset: QuerySet[User]) -> dict:
    """Status, study-mode, and city aggregates, each one SQL group-by."""
    status_counts = {row["application_status"]: row["n"] for row in queryset.values("application_status").annotate(n=Count("id"))}
    mode_counts = {row["study_mode"] or "unspecified": row["n"] for row in queryset.values("study_mode").annotate(n=Count("id"))}
    city_rows = list(
        queryset.exclude(city__isnull=True)
        .exclude(city="")
        .values("city")
        .annotate(n=Count("id"))
        .order_by("-n", "city")
    )
    return {
        "status_counts": status_counts,
        "mode_counts": mode_counts,
        "city_rows": city_rows,
    }


def _city_chart(city_rows: list[dict]) -> dict:
    if len(city_rows) > CITY_CHART_LIMIT:
        visible = city_rows[:CITY_CHART_LIMIT]
        other = sum(row["n"] for row in city_rows[CITY_CHART_LIMIT:])
        labels = [row["city"] for row in visible] + [str(_("Other"))]
        values = [row["n"] for row in visible] + [other]
    else:
        labels = [row["city"] for row in city_rows]
        values = [row["n"] for row in city_rows]
    return {"labels": labels, "values": values}


def build_analytics_context(request) -> dict:
    """Complete presentation context for the analytics page (one dataset build)."""
    dataset = build_analytics_dataset(request)
    queryset = dataset["queryset"]

    daily = analytics_daily_counts(queryset)
    series = analytics_series_rows(daily, dataset["group"], dataset["date_from"], dataset["date_to"])
    weekdays = analytics_weekday_buckets(daily)
    breakdowns = analytics_breakdowns(queryset)

    status_counts = breakdowns["status_counts"]
    mode_counts = breakdowns["mode_counts"]
    total = sum(status_counts.values())
    active = status_counts.get("active", 0)
    online = mode_counts.get("online", 0)
    offline = mode_counts.get("offline", 0)

    scope_options = [
        {
            "value": ANALYTICS_SCOPE_ALL,
            "label": _("All applications"),
        }
    ] + [
        {
            "value": str(scope.pk),
            "label": (
                f"{scope.academic_year.name} — {scope.level} ({_('Active')})"
                if scope.academic_year.is_active
                else f"{scope.academic_year.name} — {scope.level}"
            ),
        }
        for scope in analytics_scope_queryset()
    ]

    charts = {
        "series": {
            "labels": series["labels"],
            "total": series["total"],
            "online": series["online"],
            "offline": series["offline"],
            "cumulative": series["cumulative"],
        },
        "status": {
            "labels": [str(_("Pending")), str(_("Active")), str(_("Declined"))],
            "values": [
                status_counts.get("pending", 0),
                status_counts.get("active", 0),
                status_counts.get("declined", 0),
            ],
        },
        "study_mode": {
            "labels": [str(_("Online")), str(_("Offline")), str(_("Unspecified"))],
            "values": [
                mode_counts.get("online", 0),
                mode_counts.get("offline", 0),
                mode_counts.get("unspecified", 0),
            ],
        },
        "cities": _city_chart(breakdowns["city_rows"]),
        "weekdays": weekdays,
    }

    export_params = request.GET.copy()
    export_params.pop("page", None)

    return {
        "title": _("Application Analytics"),
        "breadcrumb_items": [
            {"title": _("Admin"), "url": reverse("admin-panel")},
            {"title": _("Application Analytics"), "url": None},
        ],
        "scope_options": scope_options,
        "selected_scope": str(dataset["scope"].pk) if dataset["scope"] is not None else ANALYTICS_SCOPE_ALL,
        "date_from": dataset["date_from"].isoformat(),
        "date_to": dataset["date_to"].isoformat(),
        "group": dataset["group"],
        "group_options": [
            ("day", _("Daily")),
            ("week", _("Weekly")),
            ("month", _("Monthly")),
        ],
        "selected_status": dataset["status"],
        "status_options": [
            (ANALYTICS_SCOPE_ALL, _("All statuses")),
            ("pending", _("Pending")),
            ("active", _("Active")),
            ("declined", _("Declined")),
        ],
        "selected_study_mode": dataset["study_mode"],
        "study_mode_options": [
            (ANALYTICS_SCOPE_ALL, _("All modes")),
            ("online", _("Online")),
            ("offline", _("Offline")),
        ],
        "city_options": dataset["city_options"],
        "selected_cities": dataset["cities"],
        "kpis": {
            "total": total,
            "pending": status_counts.get("pending", 0),
            "active": active,
            "declined": status_counts.get("declined", 0),
            "online": online,
            "offline": offline,
            "conversion_rate": round(active * 100 / total, 1) if total else 0.0,
        },
        "series_rows": list(reversed(series["rows"])),
        "charts": charts,
        "export_query": export_params.urlencode(),
    }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

ANALYTICS_CSV_HEADERS = (
    "Submitted At", "Username", "First Name", "Last Name", "Status",
    "Study Mode", "City", "Country", "Academic Year", "Level",
)


def iter_analytics_csv_rows(queryset: QuerySet[User]):
    """Streamed, filtered application rows for the CSV export (bounded, chunked)."""
    latest_enrollment = Enrollment.objects.filter(student=OuterRef("pk")).order_by("-enrolled_at", "-pk")
    rows = (
        queryset.annotate(
            scope_year=Subquery(latest_enrollment.values("academic_year_level__academic_year__name")[:1]),
            scope_level_en=Subquery(latest_enrollment.values("academic_year_level__level__name_en")[:1]),
            scope_level_ar=Subquery(latest_enrollment.values("academic_year_level__level__name_ar")[:1]),
            scope_level_order=Subquery(latest_enrollment.values("academic_year_level__level__ordering")[:1]),
        )
        .order_by("date_joined", "pk")
        .values(
            "username", "first_name", "last_name", "application_status", "study_mode",
            "city", "country", "date_joined", "scope_year", "scope_level_en",
            "scope_level_ar", "scope_level_order",
        )
        .iterator(chunk_size=500)
    )
    language = get_language()
    status_labels = {"pending": _("Pending"), "active": _("Active"), "declined": _("Declined")}
    mode_labels = {"online": _("Online"), "offline": _("Offline")}
    for row in rows:
        level_name = row["scope_level_ar"] if language == "ar" and row["scope_level_ar"] else row["scope_level_en"]
        if not level_name and row["scope_level_order"] is not None:
            level_name = _("Level %(ordering)d") % {"ordering": row["scope_level_order"]}
        yield [
            format_application_datetime(row["date_joined"], "%Y-%m-%d %H:%M"),
            _csv_safe_cell(row["username"]),
            _csv_safe_cell(row["first_name"]),
            _csv_safe_cell(row["last_name"]),
            _csv_safe_cell(str(status_labels.get(row["application_status"], row["application_status"]))),
            _csv_safe_cell(str(mode_labels.get(row["study_mode"], _("Unspecified")))),
            _csv_safe_cell(row["city"]),
            _csv_safe_cell(row["country"]),
            _csv_safe_cell(row["scope_year"]),
            _csv_safe_cell(level_name),
        ]