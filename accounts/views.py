from django.contrib import messages
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render

from .access import role_required
from .forms import UserCreateForm, UserProfileForm
from .models import Role, UserProfile


@role_required(Role.ADMIN)
def user_list(request):
    return render(request, 'accounts/user_list.html', {
        'users': User.objects.select_related('profile').order_by('username'),
    })


@role_required(Role.ADMIN)
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'User created.')
        return redirect('accounts:user_list')
    return render(request, 'accounts/user_form.html', {'form': form, 'title': 'Create user'})


@role_required(Role.ADMIN)
def user_detail(request, user_id):
    user = get_object_or_404(
        User.objects.select_related('profile'),
        pk=user_id,
    )
    return render(request, 'accounts/user_detail.html', {'managed_user': user})


@role_required(Role.ADMIN)
def user_update(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    original_role = user.profile.role
    original_profile_active = user.profile.is_active
    original_user_active = user.is_active
    form = UserProfileForm(request.POST or None, instance=user.profile)
    if request.method == 'POST' and form.is_valid():
        if not form.cleaned_data['is_active']:
            error = _deactivation_error(
                user,
                request.user,
                role=original_role,
                profile_active=original_profile_active,
                user_active=original_user_active,
            )
            if error:
                form.add_error('is_active', error)
            else:
                profile = form.save(commit=False)
                profile.user.is_active = False
                profile.save()
                profile.user.save(update_fields=['is_active'])
                messages.success(request, 'User permissions updated.')
                return redirect('accounts:user_detail', user_id=user.pk)
        else:
            error = _role_change_error(
                user,
                form.cleaned_data['role'],
                original_role,
                original_profile_active,
                original_user_active,
            )
            if error:
                form.add_error('role', error)
            else:
                profile = form.save()
                profile.user.is_active = True
                profile.user.save(update_fields=['is_active'])
                messages.success(request, 'User permissions updated.')
                return redirect('accounts:user_detail', user_id=user.pk)
    return render(request, 'accounts/user_form.html', {'form': form, 'title': f'Edit {user.username}'})


def _deactivation_error(
    user,
    actor,
    role=None,
    profile_active=None,
    user_active=None,
):
    role = role if role is not None else user.profile.role
    profile_active = (
        profile_active if profile_active is not None else user.profile.is_active
    )
    user_active = user_active if user_active is not None else user.is_active
    if user.pk == actor.pk:
        return 'You cannot deactivate your own account.'
    if role == Role.ADMIN and profile_active and user_active:
        active_admins = UserProfile.objects.filter(
            role=Role.ADMIN,
            is_active=True,
            user__is_active=True,
        ).count()
        if active_admins <= 1:
            return 'The last active admin account cannot be deactivated.'
    return None


def _role_change_error(
    user,
    new_role,
    original_role,
    original_profile_active,
    original_user_active,
):
    if (
        original_role == Role.ADMIN
        and original_profile_active
        and original_user_active
        and new_role != Role.ADMIN
    ):
        active_admins = UserProfile.objects.filter(
            role=Role.ADMIN,
            is_active=True,
            user__is_active=True,
        ).count()
        if active_admins <= 1:
            return 'The last active admin account cannot lose its admin role.'
    return None


def _deactivate_user(user):
    user.profile.is_active = False
    user.profile.save(update_fields=['is_active', 'updated_at'])
    user.is_active = False
    user.save(update_fields=['is_active'])


@role_required(Role.ADMIN)
def user_deactivate(request, user_id):
    user = get_object_or_404(
        User.objects.select_related('profile'),
        pk=user_id,
    )
    if request.method == 'POST':
        error = _deactivation_error(user, request.user)
        if error:
            messages.error(request, error)
            return redirect('accounts:user_detail', user_id=user.pk)
        _deactivate_user(user)
        messages.success(request, 'User deactivated; transaction history is preserved.')
        return redirect('accounts:user_list')
    return render(request, 'accounts/user_confirm_deactivate.html', {
        'managed_user': user,
    })
