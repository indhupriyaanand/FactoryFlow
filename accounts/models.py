from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from core.models import TimeStampedModel

class Role(models.TextChoices):
    """
    Five core user roles for FactoryFlow ERP.
    """
    ADMIN = 'ADMIN', 'Admin'
    SALESMAN = 'SALESMAN', 'Salesman'
    PRODUCTION_SUPERVISOR = 'PRODUCTION_SUPERVISOR', 'Production Supervisor'
    ACCOUNTANT = 'ACCOUNTANT', 'Accountant / Auditor'
    OWNER = 'OWNER', 'Owner'


class UserProfile(TimeStampedModel):
    """
    Extends Django's standard User model with ERP-specific metadata,
    role assignments, and active status tracking.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile',
        help_text="The underlying Django user account."
    )
    role = models.CharField(
        max_length=30,
        choices=Role.choices,
        default=Role.SALESMAN,
        help_text="Designated functional role within the factory."
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="Contact number for internal coordination."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this user profile is actively authorized."
    )

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'
        ordering = ['user__username']

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from .roles import assign_user_to_role_group
        assign_user_to_role_group(self.user, self.role)

    @property
    def is_admin(self):
        return self.role == Role.ADMIN or self.user.is_superuser

    @property
    def is_salesman(self):
        return self.role == Role.SALESMAN

    @property
    def is_production_supervisor(self):
        return self.role == Role.PRODUCTION_SUPERVISOR

    @property
    def is_accountant(self):
        return self.role == Role.ACCOUNTANT

    @property
    def is_owner(self):
        return self.role == Role.OWNER
