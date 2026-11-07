from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.db import transaction

from management_system.models import Role, User


class Command(BaseCommand):
    help = 'Set up the database with initial migrations and canonical roles'

    def handle(self, *args, **options):
        self.stdout.write('Starting database setup...')

        # Step 1: Run migrations
        self.stdout.write('Running migrations...')
        try:
            call_command('migrate', verbosity=0)
        except Exception as exc:
            raise CommandError(f'Migration failed: {exc}') from exc
        self.stdout.write(self.style.SUCCESS('Migrations completed successfully'))

        # Step 2: Create the canonical LMS roles idempotently.
        self.stdout.write('Creating canonical roles...')
        with transaction.atomic():
            for role_code, role_label in Role.ROLES:
                role, created = Role.objects.get_or_create(role=role_code)
                if created:
                    self.stdout.write(f'Created role: {role_label} ({role_code})')
                else:
                    self.stdout.write(f'- Role already exists: {role_label} ({role_code})')

        unexpected = set(
            Role.objects.exclude(role__in=[code for code, _label in Role.ROLES])
            .values_list('role', flat=True)
        )
        if unexpected:
            self.stdout.write(self.style.WARNING(
                'Retained non-canonical roles (review manually, no users were changed): '
                + ', '.join(sorted(unexpected))
            ))

        # Step 3: Report accounts without a role; never guess one for them.
        users_without_role = User.objects.filter(role__isnull=True).count()
        if users_without_role:
            self.stdout.write(self.style.WARNING(
                f'{users_without_role} user(s) have no role; assign one explicitly.'
            ))
        else:
            self.stdout.write('- All users already have roles assigned')

        self.stdout.write(self.style.SUCCESS('Database setup completed successfully!'))
