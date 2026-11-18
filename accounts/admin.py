from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from .models import UserProfile, Role
from .roles import assign_user_to_role_group

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'ERP Role & Profile'
    fk_name = 'user'

class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'get_role', 'is_staff', 'is_active')
    list_filter = ('profile__role', 'is_staff', 'is_active')

    def get_role(self, obj):
        if hasattr(obj, 'profile'):
            return obj.profile.get_role_display()
        return '-'
    get_role.short_description = 'ERP Role'

# Unregister default User admin and register customized UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role_badge', 'phone_number', 'is_active', 'created_at', 'updated_at')
    list_filter = ('role', 'is_active')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'user__email', 'phone_number')
    readonly_fields = ('created_at', 'updated_at')

    def role_badge(self, obj):
        badge_colors = {
            Role.ADMIN: 'danger',
            Role.SALESMAN: 'primary',
            Role.PRODUCTION_SUPERVISOR: 'warning',
            Role.ACCOUNTANT: 'info',
            Role.OWNER: 'dark',
        }
        color = badge_colors.get(obj.role, 'secondary')
        return format_html(
            '<span class="badge bg-{}" style="padding: 4px 8px; border-radius: 4px; font-size: 0.85rem;">{}</span>',
            color,
            obj.get_role_display()
        )
    role_badge.short_description = 'Assigned Role'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        assign_user_to_role_group(obj.user, obj.role)
