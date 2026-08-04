from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db import transaction
from management_system.models import Role, User
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = 'Set up the database with initial migrations and roles'

    def handle(self, *args, **options):
        self.stdout.write('Starting database setup...')
        
        # Step 1: Run migrations
        self.stdout.write('Running migrations...')
        try:
            call_command('migrate', verbosity=0)
            self.stdout.write(self.style.SUCCESS('✓ Migrations completed successfully'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Migration failed: {e}'))
            return
        
        # Step 2: Create roles in the specified order
        self.stdout.write('Creating roles...')
        roles_to_create = [
            ("junior", "First Year"),
            ("senior", "Second Year"), 
            ("teacher", "Teacher"),
            ("admin", "Admin"),
        ]
        
        with transaction.atomic():
            for role_code, role_name in roles_to_create:
                role, created = Role.objects.get_or_create(role=role_code)
                if created:
                    self.stdout.write(f'✓ Created role: {role_name} ({role_code})')
                else:
                    self.stdout.write(f'• Role already exists: {role_name} ({role_code})')
        
        # Step 3: Set default role for users without roles
        self.stdout.write('Setting default roles for users...')
        try:
            default_role = Role.objects.get(role='junior')
            users_without_role = User.objects.filter(role__isnull=True)
            count = users_without_role.count()
            
            if count > 0:
                users_without_role.update(role=default_role)
                self.stdout.write(f'✓ Set default role for {count} users')
            else:
                self.stdout.write('• All users already have roles assigned')
                
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR('✗ Default role (junior) not found'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Error setting default roles: {e}'))
        
        self.stdout.write(self.style.SUCCESS('Database setup completed successfully!'))