from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from management_system.models import (
    StudentExperienceOverride,
    StudentExperienceSettings,
    StudentMode,
    User,
)
from management_system.utils.validators import normalize_username


class Command(BaseCommand):
    help = "Set the global student view mode or one student's rollout override."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument(
            "--global-mode",
            choices=(StudentMode.LEGACY, StudentMode.NEW),
            help="Set the default mode for student accounts that inherit it.",
        )
        target.add_argument(
            "--username",
            help="Select one student account for a per-account override.",
        )
        parser.add_argument(
            "--override",
            choices=tuple(StudentExperienceOverride.values),
            help="Set the selected account to inherit, legacy, or new.",
        )

    def handle(self, *args, **options):
        if options["global_mode"]:
            if options["override"] is not None:
                raise CommandError("Use --override only with --username.")
            return self._set_global_mode(options["global_mode"])

        if options["override"] is None:
            raise CommandError("--username requires --override.")
        return self._set_account_override(
            options["username"], options["override"]
        )

    def _set_global_mode(self, mode):
        with transaction.atomic():
            row, _created = StudentExperienceSettings.objects.get_or_create(
                singleton="default",
                defaults={"mode": StudentMode.LEGACY},
            )
            row = StudentExperienceSettings.objects.select_for_update().get(pk=row.pk)
            row.mode = mode
            row.updated_by = None
            row.save(update_fields=("mode", "updated_at", "updated_by"))
        self.stdout.write(self.style.SUCCESS(f"Global student view mode set to {mode}."))

    def _set_account_override(self, username, override):
        normalized = normalize_username(username)
        try:
            user = User.objects.select_related("role").get(username=normalized)
        except User.DoesNotExist as exc:
            raise CommandError("No account exists with that username.") from exc
        if getattr(user.role, "role", None) != "student":
            raise CommandError("Per-account overrides are limited to student accounts.")

        user.student_experience_override = override
        user.save(update_fields=("student_experience_override",))
        self.stdout.write(
            self.style.SUCCESS(
                f"Student account {user.username} override set to {override}."
            )
        )
