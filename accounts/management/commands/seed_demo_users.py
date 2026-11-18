from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from accounts.models import UserProfile, Role
from accounts.roles import setup_role_groups, assign_user_to_role_group

class Command(BaseCommand):
    help = "Seeds the five standard FactoryFlow demo user accounts with their respective roles."

    DEMO_USERS = [
        {
            'username': 'admin',
            'email': 'admin@factoryflow.com',
            'password': 'admin123',
            'role': Role.ADMIN,
            'first_name': 'Factory',
            'last_name': 'Administrator',
            'is_staff': True,
            'is_superuser': True,
        },
        {
            'username': 'salesman',
            'email': 'salesman@factoryflow.com',
            'password': 'sales123',
            'role': Role.SALESMAN,
            'first_name': 'Ramesh',
            'last_name': 'Kumar',
            'is_staff': True,
            'is_superuser': False,
        },
        {
            'username': 'supervisor',
            'email': 'supervisor@factoryflow.com',
            'password': 'prod123',
            'role': Role.PRODUCTION_SUPERVISOR,
            'first_name': 'Murugan',
            'last_name': 'Supervisor',
            'is_staff': True,
            'is_superuser': False,
        },
        {
            'username': 'accountant',
            'email': 'accountant@factoryflow.com',
            'password': 'audit123',
            'role': Role.ACCOUNTANT,
            'first_name': 'Sundaram',
            'last_name': 'Auditor',
            'is_staff': True,
            'is_superuser': False,
        },
        {
            'username': 'owner',
            'email': 'owner@factoryflow.com',
            'password': 'owner123',
            'role': Role.OWNER,
            'first_name': 'Indhumathi',
            'last_name': 'Proprietor',
            'is_staff': True,
            'is_superuser': False,
        },
    ]

    def handle(self, *args, **options):
        self.stdout.write("Initializing standard role groups and permissions...")
        setup_role_groups()

        for user_data in self.DEMO_USERS:
            username = user_data['username']
            user, created = User.objects.get_or_create(username=username, defaults={
                'email': user_data['email'],
                'first_name': user_data['first_name'],
                'last_name': user_data['last_name'],
                'is_staff': user_data['is_staff'],
                'is_superuser': user_data['is_superuser'],
            })
            user.set_password(user_data['password'])
            user.is_staff = user_data['is_staff']
            user.is_superuser = user_data['is_superuser']
            user.save()

            # Update UserProfile
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = user_data['role']
            profile.is_active = True
            profile.save()

            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(
                f"{action} [{user_data['role']}] user: '{username}' (Password: '{user_data['password']}')"
            ))

        self.stdout.write(self.style.SUCCESS("All 5 FactoryFlow demo role accounts ready."))
