import os
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import transaction
from accounts.models import UserProfile, Role
from accounts.roles import setup_role_groups


class Command(BaseCommand):
    help = (
        "Securely bootstraps an initial Django superuser from environment variables "
        "(ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_PASSWORD) if it does not already exist. "
        "Safe to run repeatedly during deployments without overwriting existing data."
    )

    def handle(self, *args, **options):
        username = os.environ.get('ADMIN_USERNAME', '').strip()
        email = os.environ.get('ADMIN_EMAIL', '').strip()
        password = os.environ.get('ADMIN_PASSWORD', '').strip()

        # 1. Check for required environment variables
        if not username or not password:
            self.stdout.write(
                self.style.WARNING(
                    "ADMIN_USERNAME and/or ADMIN_PASSWORD environment variables are not set. "
                    "Skipping admin bootstrap."
                )
            )
            return

        # 2. Check if user already exists (Idempotent & non-destructive)
        if User.objects.filter(username=username).exists():
            self.stdout.write(
                self.style.SUCCESS(
                    f"Superuser '{username}' already exists. No changes made."
                )
            )
            return

        # 3. Create superuser inside atomic transaction
        try:
            with transaction.atomic():
                # Ensure standard role groups exist
                setup_role_groups()

                user = User.objects.create_superuser(
                    username=username,
                    email=email,
                    password=password
                )

                # Ensure UserProfile exists with ADMIN role
                profile, _ = UserProfile.objects.get_or_create(user=user)
                profile.role = Role.ADMIN
                profile.is_active = True
                profile.save()

                # Success message - NEVER log the password!
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Superuser '{username}' successfully created with ADMIN role."
                    )
                )
        except Exception as e:
            self.stderr.write(
                self.style.ERROR(
                    f"Failed to bootstrap superuser '{username}': {e}"
                )
            )
            raise
