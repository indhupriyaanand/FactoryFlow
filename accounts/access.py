from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Role


def active_user_required(view_func):
    """Require authentication plus an active FactoryFlow profile."""
    @login_required
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        profile = getattr(request.user, "profile", None)
        if not profile or not profile.is_active:
            raise PermissionDenied("Your FactoryFlow account is inactive.")
        return view_func(request, *args, **kwargs)

    return wrapped


def role_required(*roles):
    """Require an active user with one of the supplied FactoryFlow roles."""
    def decorator(view_func):
        @active_user_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            profile = request.user.profile
            if not (request.user.is_superuser or profile.role in roles):
                raise PermissionDenied("You do not have permission for this operation.")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


MANAGEMENT_ROLES = (Role.ADMIN, Role.OWNER)
PRODUCTION_ROLES = (Role.ADMIN, Role.PRODUCTION_SUPERVISOR)
SALES_ROLES = (Role.ADMIN, Role.SALESMAN)
PURCHASE_ROLES = (Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)


def require_role(user, *roles):
    """Enforce an active authenticated user at service boundaries."""
    if not user or not getattr(user, 'is_authenticated', False):
        raise PermissionDenied("Authentication is required for this operation.")
    if getattr(user, 'is_superuser', False):
        return
    profile = getattr(user, 'profile', None)
    if not profile or not profile.is_active or profile.role not in roles:
        raise PermissionDenied("You do not have permission for this operation.")
