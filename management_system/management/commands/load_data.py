from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
import os


class Command(BaseCommand):
    help = 'Load data from fixture files, handling conflicts gracefully'

    def add_arguments(self, parser):
        parser.add_argument('fixture', help='Path to the fixture file')
        parser.add_argument('--skip-contenttypes', action='store_true',
                            help='Skip content type fixtures to avoid duplicates')

    def handle(self, *args, **options):
        fixture_path = options['fixture']

        if not os.path.exists(fixture_path):
            raise CommandError(f'Fixture file not found: {fixture_path}')

        self.stdout.write(f'Loading data from: {fixture_path}')

        try:
            if options['skip_contenttypes']:
                # Load data excluding content types and permissions
                call_command('loaddata', fixture_path, verbosity=1,
                             exclude=['contenttypes', 'auth.permission'])
            else:
                call_command('loaddata', fixture_path, verbosity=1)
        except Exception as exc:
            raise CommandError(
                f'Error loading data from {fixture_path}: {exc}. '
                'Try running with --skip-contenttypes.'
            ) from exc

        self.stdout.write(self.style.SUCCESS('Data loaded successfully'))
