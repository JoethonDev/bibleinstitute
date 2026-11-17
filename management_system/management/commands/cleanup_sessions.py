import time
from math import isfinite

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from ...session_cleanup import SessionCleanupError, cleanup_expired_viewing_sessions


class Command(BaseCommand):
    help = _("Clean up viewing-session evidence after the replay window ends.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help=_("Show bounded backlog counts without deleting."))
        parser.add_argument("--child-batch-size", type=int, default=5000)
        parser.add_argument("--session-batch-size", type=int, default=500)
        parser.add_argument("--deadline-seconds", type=float, default=25)

    def handle(self, *args, **options):
        if not isfinite(options["deadline_seconds"]) or options["deadline_seconds"] <= 0:
            raise CommandError(_("The deadline must be greater than zero."))
        try:
            result = cleanup_expired_viewing_sessions(
                dry_run=options["dry_run"],
                child_batch_size=options["child_batch_size"],
                session_batch_size=options["session_batch_size"],
                deadline=time.monotonic() + min(options["deadline_seconds"], 25),
            )
        except SessionCleanupError as exc:
            raise CommandError(str(exc)) from exc
        if not result["lease_acquired"]:
            self.stdout.write(_("Another viewing-session cleanup is already running."))
            return
        if options["dry_run"]:
            self.stdout.write(_("No rows were changed. The counts below are exact."))
        else:
            self.stdout.write(_("Deleted %(segments)s segment receipts, detached %(events)s progress events, and deleted %(sessions)s sessions.") % {
                "segments": result["segments_deleted"],
                "events": result["events_detached"],
                "sessions": result["sessions_deleted"],
            })
        for label, key, more_key in (
            (_("segment receipts"), "remaining_segments", "segments_more"),
            (_("progress events"), "remaining_events", "events_more"),
            (_("sessions"), "remaining_sessions", "sessions_more"),
        ):
            count = result[key]
            if result[more_key]:
                self.stdout.write(_("At least %(count)s %(label)s remain.") % {"count": count, "label": label})
            elif count:
                self.stdout.write(_("%(count)s %(label)s remain.") % {"count": count, "label": label})
        self.stdout.write(_("Maximum transaction time: %(seconds).3f seconds.") % {
            "seconds": result["max_transaction_seconds"],
        })
        if result["contended"]:
            self.stdout.write(self.style.WARNING(_("Lock contention reduced the batch size; the next scheduled run will continue.")))
        elif result["has_backlog"]:
            self.stdout.write(_("Cleanup reached its time or batch limit. Run this command again to continue."))
