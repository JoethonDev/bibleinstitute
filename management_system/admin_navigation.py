"""Canonical admin workspace navigation model.

One server-side source for the nine workspace domains, the contextual
sections of the active domain, the route aliases used for active-state
resolution and the capability-filtered quick-create menu. Templates render
these structures; they never keep a second navigation list or repeat role
strings. Capabilities are the exact functions the owning views use, so
navigation visibility and page access cannot drift apart.

The module performs no queries. Sections resolve their URL names lazily at
render time, which keeps reverse failures near the template that rendered
them and lets settings override a route without changing this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from django.utils.translation import gettext_lazy as _

from .utils.decorators import (
    can_manage_academic_setup,
    can_manage_applications,
    can_manage_content,
    can_scan_attendance,
    can_view_reports,
)

Capability = Callable[[object], bool]


@dataclass(frozen=True)
class AdminSection:
    """One canonical destination inside a workspace domain."""

    key: str
    label: str
    url_name: str
    icon: str
    capability: Capability
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdminDomain:
    """The rail destination plus the sections shown while it is active."""

    key: str
    label: str
    icon: str
    sections: tuple[AdminSection, ...]


@dataclass(frozen=True)
class QuickCreateAction:
    """One capability-filtered create action offered by the top bar."""

    key: str
    label: str
    url_name: str
    icon: str
    capability: Capability


@dataclass(frozen=True)
class AdminTopAction:
    """One capability-filtered top-bar utility destination.

    Each entry points at an existing authorized route. No entry invents an
    unread count or a notification feed: the admin attention inbox needs its
    own approved content/acknowledgment contract before a badge can ship.
    """

    key: str
    label: str
    url_name: str
    icon: str
    capability: Capability


ADMIN_DOMAINS: tuple[AdminDomain, ...] = (
    AdminDomain(
        key="overview",
        label=_("Overview"),
        icon="i-grid",
        sections=(
            AdminSection(
                key="today",
                label=_("Today"),
                url_name="admin-panel",
                icon="i-home",
                capability=can_manage_content,
            ),
            AdminSection(
                key="background-jobs",
                label=_("Background jobs"),
                url_name="background-jobs",
                icon="i-server",
                capability=can_manage_academic_setup,
                aliases=("background-job-detail", "background-job-status-batch"),
            ),
            AdminSection(
                key="notification-history",
                label=_("Notification history"),
                url_name="notification-history",
                icon="i-bell",
                capability=can_manage_academic_setup,
                aliases=("notification-redelivery", "notification-withdraw"),
            ),
        ),
    ),
    AdminDomain(
        key="people",
        label=_("People"),
        icon="i-users",
        sections=(
            AdminSection(
                key="applications",
                label=_("Applications"),
                url_name="applications-dashboard",
                icon="i-file",
                capability=can_manage_applications,
                aliases=(
                    "application-review",
                    "application-document",
                    "application-decision",
                    "application-delete",
                ),
            ),
            AdminSection(
                key="students",
                label=_("Students"),
                url_name="user-dashboard",
                icon="i-user",
                capability=can_manage_content,
                aliases=(
                    "user-create",
                    "user-update",
                    "user-profile",
                    "user-delete",
                    "user-bulk-create",
                    "student-overview",
                    "exceptional-course-assign",
                    "telegram-admin-unlink",
                ),
            ),
            AdminSection(
                key="historical-intake",
                label=_("Historical Intake"),
                url_name="historical-intake",
                icon="i-clock",
                capability=can_manage_academic_setup,
                aliases=("historical-promote",),
            ),
            AdminSection(
                key="signup-settings",
                label=_("Signup Settings"),
                url_name="signup-settings",
                icon="i-user-plus",
                capability=can_manage_applications,
            ),
        ),
    ),
    AdminDomain(
        key="curriculum",
        label=_("Curriculum"),
        icon="i-book",
        sections=(
            AdminSection(
                key="courses",
                label=_("Courses"),
                url_name="course-dashboard",
                icon="i-book",
                capability=can_manage_content,
                aliases=(
                    "course-create",
                    "course-update",
                    "course-delete",
                    "course-view",
                    "course-duplicate",
                ),
            ),
            AdminSection(
                key="lessons",
                label=_("Lessons"),
                url_name="lesson-dashboard",
                icon="i-video",
                capability=can_manage_content,
                aliases=(
                    "lesson-create",
                    "lesson-update",
                    "lesson-delete",
                    "lesson-view",
                    "lesson-duplicate",
                    "navigate-folder",
                    "navigate-folder-root",
                ),
            ),
            AdminSection(
                key="quizzes",
                label=_("Quizzes"),
                url_name="quiz-dashboard",
                icon="i-check-square",
                capability=can_manage_content,
                aliases=(
                    "quiz-create",
                    "quiz-update",
                    "quiz-delete",
                    "quiz-view",
                    "quiz-duplicate",
                    "quiz-json-preview",
                    "quiz-question-regrade",
                    "quiz-exceptional-opening",
                    "submission-dashboard",
                    "submission-user",
                    "quiz-submission-delete",
                ),
            ),
            AdminSection(
                key="content-qa",
                label=_("Content QA"),
                url_name="content-qa",
                icon="i-shield",
                capability=can_manage_content,
            ),
        ),
    ),
    AdminDomain(
        key="academic",
        label=_("Academic"),
        icon="i-calendar",
        sections=(
            AdminSection(
                key="academic-setup",
                label=_("Academic Setup"),
                url_name="academic-setup",
                icon="i-layers",
                capability=can_manage_academic_setup,
                aliases=(
                    "academic-year-create",
                    "academic-year-edit",
                    "academic-year-activate",
                    "academic-year-delete",
                    "academic-year-level-weekdays",
                    "academic-year-level-delete",
                    "academic-offerings-by-scope",
                    "course-offering-create",
                    "course-offering-edit",
                    "course-offering-delete",
                    "copy-course-offerings",
                ),
            ),
            AdminSection(
                key="levels",
                label=_("Levels"),
                url_name="levels-dashboard",
                icon="i-graduation",
                capability=can_manage_content,
                aliases=("level-create", "level-edit", "level-delete"),
            ),
            AdminSection(
                key="calendar",
                label=_("Calendar"),
                url_name="calendar-management",
                icon="i-calendar",
                capability=can_manage_content,
                aliases=("add-meeting", "delete-meeting", "add-holiday", "delete-holiday"),
            ),
            AdminSection(
                key="promotion",
                label=_("Promotion Formula"),
                url_name="promotion-formula",
                icon="i-chart",
                capability=can_view_reports,
                aliases=(
                    "evaluation-result-override",
                    "promotion-result",
                    "bulk-promotion-results",
                    "export-evaluation-xlsx",
                ),
            ),
            AdminSection(
                key="promotion-history",
                label=_("Promotion history"),
                url_name="promotion-history",
                icon="i-clock",
                capability=can_view_reports,
            ),
        ),
    ),
    AdminDomain(
        key="attendance",
        label=_("Attendance"),
        icon="i-check-square",
        sections=(
            AdminSection(
                key="attendance",
                label=_("Attendance"),
                url_name="attendance-management",
                icon="i-check-square",
                capability=can_view_reports,
                aliases=(
                    "attendance-policy-save",
                    "attendance-reconcile",
                    "attendance-correction",
                    "attendance-delete",
                ),
            ),
            AdminSection(
                key="scanner",
                label=_("Scanner"),
                url_name="scanner",
                icon="i-qr",
                capability=can_scan_attendance,
                aliases=("scan-preview", "record-attendance", "student-lookup"),
            ),
        ),
    ),
    AdminDomain(
        key="media",
        label=_("Media"),
        icon="i-upload",
        sections=(
            AdminSection(
                key="media-processing",
                label=_("Media processing"),
                url_name="media-processing-status",
                icon="i-upload",
                capability=can_manage_content,
                aliases=("media-job-create", "media-job-status", "media-job-source-complete"),
            ),
            AdminSection(
                key="upload",
                label=_("Upload Files"),
                url_name="upload-files",
                icon="i-cloud",
                capability=can_manage_content,
            ),
            AdminSection(
                key="r2-storage",
                label=_("R2 Storage"),
                url_name="r2-management-dashboard",
                icon="i-server",
                capability=can_manage_content,
            ),
            AdminSection(
                key="gallery",
                label=_("Graduation Gallery"),
                url_name="graduation-gallery-manage",
                icon="i-image",
                capability=can_manage_content,
            ),
        ),
    ),
    AdminDomain(
        key="reports",
        label=_("Reports"),
        icon="i-chart",
        sections=(
            AdminSection(
                key="report-combined",
                label=_("Combined"),
                url_name="report-dashboard",
                icon="i-chart",
                capability=can_view_reports,
                aliases=("export-report-csv", "export-report-xlsx"),
            ),
            AdminSection(
                key="report-grades",
                label=_("Grades"),
                url_name="grades-matrix-dashboard",
                icon="i-chart-line",
                capability=can_view_reports,
                aliases=("export-grades-matrix-xlsx",),
            ),
            AdminSection(
                key="report-payments",
                label=_("Payments"),
                url_name="payments-matrix-dashboard",
                icon="i-receipt",
                capability=can_view_reports,
                aliases=("academic-payment-document",),
            ),
            AdminSection(
                key="report-progress",
                label=_("Progress"),
                url_name="progress-dashboard",
                icon="i-chart-line",
                capability=can_manage_content,
            ),
            AdminSection(
                key="report-analytics",
                label=_("Analytics"),
                url_name="application-analytics",
                icon="i-chart",
                capability=can_view_reports,
                aliases=("export-application-analytics-csv",),
            ),
        ),
    ),
    AdminDomain(
        key="engagement",
        label=_("Engagement"),
        icon="i-message",
        sections=(
            AdminSection(
                key="announcements",
                label=_("Notifications"),
                url_name="notifications-management",
                icon="i-bell",
                capability=can_manage_academic_setup,
                aliases=("announcement-retry", "notifications-recipient-count"),
            ),
            AdminSection(
                key="telegram-inbox",
                label=_("Telegram Chat"),
                url_name="telegram-conversations",
                icon="i-message",
                capability=can_manage_academic_setup,
                aliases=(
                    "telegram-conversation-detail",
                    "telegram-conversation-reply",
                    "telegram-conversation-mark-read",
                    "telegram-conversation-claim",
                    "telegram-conversation-release",
                    "telegram-conversation-message-delete",
                    "telegram-conversations-mark-all-read",
                    "telegram-start-conversation",
                ),
            ),
            AdminSection(
                key="telegram-broadcasts",
                label=_("Telegram Broadcasts"),
                url_name="telegram-broadcasts",
                icon="i-send",
                capability=can_manage_academic_setup,
                aliases=(
                    "telegram-broadcast-create",
                    "telegram-broadcast-confirm-page",
                    "telegram-broadcast-confirm",
                    "telegram-broadcast-cancel",
                    "telegram-broadcast-detail",
                    "telegram-broadcast-recipient-delete",
                ),
            ),
            AdminSection(
                key="telegram-accounts",
                label=_("Telegram Accounts"),
                url_name="telegram-accounts",
                icon="i-user",
                capability=can_manage_academic_setup,
                aliases=("telegram-account-unlink", "telegram-attachment"),
            ),
            AdminSection(
                key="telegram-bot",
                label=_("Telegram Bot"),
                url_name="telegram-config",
                icon="i-settings",
                capability=can_manage_academic_setup,
            ),
        ),
    ),
    AdminDomain(
        key="site-apps",
        label=_("Site & Apps"),
        icon="i-mobile",
        sections=(
            AdminSection(
                key="app-releases",
                label=_("App Releases"),
                url_name="app-release-manage",
                icon="i-mobile",
                capability=can_manage_academic_setup,
                aliases=("app-release-delete",),
            ),
            AdminSection(
                key="banners",
                label=_("Banners"),
                url_name="banner-manage",
                icon="i-image",
                capability=can_manage_academic_setup,
                aliases=("banner-toggle", "banner-delete"),
            ),
        ),
    ),
)

QUICK_CREATE_ACTIONS: tuple[QuickCreateAction, ...] = (
    QuickCreateAction(
        key="lesson", label=_("New lesson"), url_name="lesson-create",
        icon="i-video", capability=can_manage_content,
    ),
    QuickCreateAction(
        key="quiz", label=_("New quiz"), url_name="quiz-create",
        icon="i-check-square", capability=can_manage_content,
    ),
    QuickCreateAction(
        key="course", label=_("New course"), url_name="course-create",
        icon="i-book", capability=can_manage_content,
    ),
    QuickCreateAction(
        key="media", label=_("Upload media"), url_name="upload-files",
        icon="i-upload", capability=can_manage_content,
    ),
    QuickCreateAction(
        key="announcement", label=_("New announcement"),
        url_name="notifications-management", icon="i-bell",
        capability=can_manage_academic_setup,
    ),
    QuickCreateAction(
        key="student", label=_("Add student"), url_name="user-create",
        icon="i-user-plus", capability=can_manage_applications,
    ),
    QuickCreateAction(
        key="offering", label=_("Add offering"), url_name="course-offering-create",
        icon="i-layers", capability=can_manage_academic_setup,
    ),
)

#: Top-bar utility destinations. Every entry is an existing authorized route;
#: adding an unread badge here requires an approved admin attention contract.
TOP_ACTIONS: tuple[AdminTopAction, ...] = (
    AdminTopAction(
        key="notifications",
        label=_("Notification history"),
        url_name="notification-history",
        icon="i-bell",
        capability=can_manage_academic_setup,
    ),
    AdminTopAction(
        key="background-jobs",
        label=_("Background jobs"),
        url_name="background-jobs",
        icon="i-server",
        capability=can_manage_academic_setup,
    ),
)

#: url_name -> (domain key, section key); built once from the canonical model.
ROUTE_INDEX: dict[str, tuple[str, str]] = {}
for _domain in ADMIN_DOMAINS:
    for _section in _domain.sections:
        for _name in (_section.url_name, *_section.aliases):
            ROUTE_INDEX.setdefault(_name, (_domain.key, _section.key))


def _capability_passes(user, capability: Capability) -> bool:
    try:
        return bool(capability(user))
    except (AttributeError, TypeError):
        return False


def _visible_sections(user, domain: AdminDomain) -> tuple[AdminSection, ...]:
    return tuple(
        section
        for section in domain.sections
        if _capability_passes(user, section.capability)
    )


def admin_navigation_for(user) -> tuple[AdminDomain, ...]:
    """Return only domains/sections whose canonical capability passes."""
    visible = []
    for domain in ADMIN_DOMAINS:
        sections = _visible_sections(user, domain)
        if not sections:
            continue
        visible.append(
            AdminDomain(
                key=domain.key,
                label=domain.label,
                icon=domain.icon,
                sections=sections,
            )
        )
    return tuple(visible)


def admin_route_location(url_name: str | None) -> tuple[str, str] | None:
    """Resolve a URL name (including detail/editor aliases) to a location."""
    if not url_name:
        return None
    return ROUTE_INDEX.get(url_name)


def first_visible_section(domain: AdminDomain) -> AdminSection | None:
    return domain.sections[0] if domain.sections else None



def admin_workspace_navigation(user, url_name: str | None) -> dict:
    """Build the template contract for the rail, sidebar and top bar.

    Returns the capability-filtered domains, the resolved active domain and
    section, and the route that activates a domain when its current section is
    not permitted. No database access and no template-side role checks.
    """
    domains = admin_navigation_for(user)
    location = admin_route_location(url_name)
    active_domain = None
    active_section = None

    if location is not None:
        domain_key, section_key = location
        for domain in domains:
            if domain.key != domain_key:
                continue
            for section in domain.sections:
                if section.key == section_key:
                    active_domain = domain
                    active_section = section
                    break
            break

    if active_domain is None and domains:
        # Unknown or now-denied route: keep a stable capability-safe landing so
        # the shell never renders a domain the actor cannot open.
        active_domain = domains[0]
        active_section = first_visible_section(active_domain)

    return {
        "domains": domains,
        "active_domain": active_domain,
        "active_section": active_section,
        "active_domain_key": active_domain.key if active_domain else "",
        "active_section_key": active_section.key if active_section else "",
    }


def quick_create_actions_for(user) -> tuple[QuickCreateAction, ...]:
    return tuple(
        action
        for action in QUICK_CREATE_ACTIONS
        if _capability_passes(user, action.capability)
    )


def top_actions_for(user) -> tuple[AdminTopAction, ...]:
    return tuple(
        action
        for action in TOP_ACTIONS
        if _capability_passes(user, action.capability)
    )
