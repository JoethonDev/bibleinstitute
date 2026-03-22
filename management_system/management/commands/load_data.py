from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db import transaction
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
            self.stdout.write(self.style.ERROR(f'✗ Fixture file not found: {fixture_path}'))
            return
            
        self.stdout.write(f'Loading data from: {fixture_path}')
        
        try:
            if options['skip_contenttypes']:
                # Load data excluding content types and permissions
                call_command('loaddata', fixture_path, verbosity=1, 
                           exclude=['contenttypes', 'auth.permission'])
            else:
                call_command('loaddata', fixture_path, verbosity=1)
                
            self.stdout.write(self.style.SUCCESS('✓ Data loaded successfully'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Error loading data: {e}'))
            self.stdout.write('Try running with --skip-contenttypes flag')