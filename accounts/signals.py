from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import UserProfile, Role
from .roles import assign_user_to_role_group

@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    """
    Signal handler:
    1. Automatically creates a UserProfile whenever a new User is created.
    2. Synchronizes the user's role group.
    """
    if created:
        profile = UserProfile.objects.create(
            user=instance,
            role=Role.ADMIN if instance.is_superuser else Role.SALESMAN
        )
    else:
        # Ensure profile exists if user was created before signals or via external means
        profile, _ = UserProfile.objects.get_or_create(user=instance)

    if profile.role:
        assign_user_to_role_group(instance, profile.role)
