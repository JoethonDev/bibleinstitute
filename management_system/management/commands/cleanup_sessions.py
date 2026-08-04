from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from ...models import ViewingSession


class Command(BaseCommand):
    help = "Clean up expired viewing sessions and segment requests."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting.")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=7)
        sessions = ViewingSession.objects.filter(expires_at__lt=cutoff)
        count = sessions.count()
        if options["dry_run"]:
            self.stdout.write(f"Would delete {count} expired sessions.")
            return
        sessions.delete()
        self.stdout.write(f"Deleted {count} expired sessions.")
