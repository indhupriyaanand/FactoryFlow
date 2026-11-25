from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User, Group, Permission
from accounts.models import UserProfile, Role
from accounts.roles import setup_role_groups, ROLE_GROUP_MAPPING

class UserRoleManagementTests(TestCase):
    """
    Tests for User Profile and 5 User Roles:
    Admin, Salesman, Production Supervisor, Accountant, Owner.
    """

    def setUp(self):
        setup_role_groups()

    def test_all_five_role_groups_exist(self):
        """Verify that all five standard role groups are created."""
        for role, group_name in ROLE_GROUP_MAPPING.items():
            self.assertTrue(Group.objects.filter(name=group_name).exists(), f"Group {group_name} missing")

    def test_create_users_with_different_roles(self):
        """Test creating users across all 5 roles and checking profile attributes."""
        test_roles = [
            ('admin_user', Role.ADMIN),
            ('sales_user', Role.SALESMAN),
            ('prod_user', Role.PRODUCTION_SUPERVISOR),
            ('acct_user', Role.ACCOUNTANT),
            ('owner_user', Role.OWNER),
        ]

        for username, role in test_roles:
            user = User.objects.create_user(
                username=username,
                password='ComplexPassword123!',
                email=f"{username}@factoryflow.com"
            )
            profile = user.profile
            profile.role = role
            profile.save()
            user.refresh_from_db()

            self.assertEqual(profile.role, role)
            self.assertTrue(profile.is_active)

            # Test role property helpers
            if role == Role.ADMIN:
                self.assertTrue(profile.is_admin)
            elif role == Role.SALESMAN:
                self.assertTrue(profile.is_salesman)
            elif role == Role.PRODUCTION_SUPERVISOR:
                self.assertTrue(profile.is_production_supervisor)
            elif role == Role.ACCOUNTANT:
                self.assertTrue(profile.is_accountant)
            elif role == Role.OWNER:
                self.assertTrue(profile.is_owner)

            # Test that user is synchronized to the corresponding group
            expected_group_name = ROLE_GROUP_MAPPING[role]
            self.assertTrue(user.groups.filter(name=expected_group_name).exists())

    def test_role_change_updates_group_membership(self):
        """Test that changing a user's role updates their group membership accordingly."""
        user = User.objects.create_user(username='flex_user', password='Password123!')
        profile = user.profile
        profile.role = Role.SALESMAN
        profile.save()
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name='Salesman').exists())

        # Switch to Production Supervisor
        profile.role = Role.PRODUCTION_SUPERVISOR
        profile.save()
        user.refresh_from_db()
        self.assertFalse(user.groups.filter(name='Salesman').exists())
        self.assertTrue(user.groups.filter(name='Production Supervisor').exists())

    def test_role_specific_permissions(self):
        """
        Test specific role permission boundaries:
        - Salesman can add customer, but cannot add BOM.
        - Production Supervisor can add BOM, but cannot add customer.
        - Accountant has view-only permissions and cannot add or delete.
        """
        # 1. Salesman
        salesman = User.objects.create_user(username='sales1', password='Password123!')
        salesman.profile.role = Role.SALESMAN
        salesman.profile.save()
        # Reload user to ensure backend permissions cache is refreshed
        salesman = User.objects.get(pk=salesman.pk)
        self.assertTrue(salesman.has_perm('masters.add_customer'))
        self.assertFalse(salesman.has_perm('masters.add_billofmaterials'))
        self.assertFalse(salesman.has_perm('masters.delete_product'))

        # 2. Production Supervisor
        prod_sup = User.objects.create_user(username='prod1', password='Password123!')
        prod_sup.profile.role = Role.PRODUCTION_SUPERVISOR
        prod_sup.profile.save()
        prod_sup = User.objects.get(pk=prod_sup.pk)
        self.assertTrue(prod_sup.has_perm('masters.add_billofmaterials'))
        self.assertTrue(prod_sup.has_perm('masters.view_rawmaterial'))
        self.assertFalse(prod_sup.has_perm('masters.add_customer'))

        # 3. Accountant (Read-Only)
        accountant = User.objects.create_user(username='acct1', password='Password123!')
        accountant.profile.role = Role.ACCOUNTANT
        accountant.profile.save()
        accountant = User.objects.get(pk=accountant.pk)
        self.assertTrue(accountant.has_perm('masters.view_product'))
        self.assertTrue(accountant.has_perm('masters.view_customer'))
        self.assertFalse(accountant.has_perm('masters.add_product'))
        self.assertFalse(accountant.has_perm('masters.delete_customer'))

    def test_admin_user_management_workflow(self):
        admin = User.objects.create_user(username='workflow-admin', password='Password123!')
        admin.profile.role = Role.ADMIN
        admin.profile.save()
        client = Client()
        client.force_login(admin)
        response = client.post(reverse('accounts:user_create'), {
            'username': 'new-user',
            'email': 'new@example.com',
            'password': 'ComplexPassword123!',
            'role': Role.ACCOUNTANT,
            'phone_number': '9999999999',
        })
        self.assertEqual(response.status_code, 302)
        created = User.objects.get(username='new-user')
        self.assertEqual(created.profile.role, Role.ACCOUNTANT)
        response = client.get(reverse('accounts:user_list'))
        self.assertEqual(response.status_code, 200)


class UserCrudRequestTests(TestCase):
    def setUp(self):
        setup_role_groups()
        self.admin = User.objects.create_user(
            username='crud-admin',
            password='Password123!',
        )
        self.admin.profile.role = Role.ADMIN
        self.admin.profile.save()
        self.client = Client()
        self.client.force_login(self.admin)

    def test_create_view_edit_and_confirm_deactivate_user(self):
        response = self.client.post(reverse('accounts:user_create'), {
            'username': 'managed-user',
            'email': 'managed@example.test',
            'password': 'StrongPass123!',
            'role': Role.SALESMAN,
            'phone_number': '9876543210',
        })
        list_url = reverse('accounts:user_list')
        self.assertRedirects(response, list_url)
        managed = User.objects.get(username='managed-user')

        detail_url = reverse('accounts:user_detail', args=[managed.pk])
        self.assertEqual(self.client.get(detail_url).status_code, 200)
        edit_url = reverse('accounts:user_update', args=[managed.pk])
        response = self.client.post(edit_url, {
            'role': Role.ACCOUNTANT,
            'phone_number': '9123456780',
            'is_active': 'on',
        })
        self.assertRedirects(response, detail_url)
        managed.refresh_from_db()
        self.assertEqual(managed.profile.role, Role.ACCOUNTANT)
        self.assertEqual(managed.profile.phone_number, '9123456780')

        deactivate_url = reverse('accounts:user_deactivate', args=[managed.pk])
        self.assertEqual(self.client.get(deactivate_url).status_code, 200)
        self.assertContains(self.client.get(deactivate_url), 'Confirm user deactivation')
        response = self.client.post(deactivate_url)
        self.assertRedirects(response, list_url)
        managed.refresh_from_db()
        self.assertFalse(managed.is_active)
        self.assertFalse(managed.profile.is_active)
        self.assertTrue(User.objects.filter(pk=managed.pk).exists())

    def test_admin_cannot_deactivate_self_or_last_admin(self):
        self_user_deactivate = reverse(
            'accounts:user_deactivate',
            args=[self.admin.pk],
        )
        response = self.client.post(self_user_deactivate)
        self.assertRedirects(
            response,
            reverse('accounts:user_detail', args=[self.admin.pk]),
        )
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

        other_admin = User.objects.create_user(
            username='other-admin',
            password='Password123!',
        )
        other_admin.profile.role = Role.ADMIN
        other_admin.profile.save()
        self.admin.profile.role = Role.SALESMAN
        self.admin.profile.save()
        self.admin.is_superuser = True
        self.admin.save(update_fields=['is_superuser'])
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('accounts:user_deactivate', args=[other_admin.pk])
        )
        self.assertRedirects(
            response,
            reverse('accounts:user_detail', args=[other_admin.pk]),
        )
        other_admin.refresh_from_db()
        self.assertTrue(other_admin.is_active)

    def test_admin_cannot_remove_the_last_active_admin_role(self):
        response = self.client.post(
            reverse('accounts:user_update', args=[self.admin.pk]),
            {
                'role': Role.SALESMAN,
                'phone_number': '',
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'last active admin account')
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.profile.role, Role.ADMIN)

    def test_non_admin_cannot_access_user_management_routes(self):
        viewer = User.objects.create_user(
            username='crud-viewer',
            password='Password123!',
        )
        viewer.profile.role = Role.SALESMAN
        viewer.profile.save()
        self.client.force_login(viewer)
        self.assertEqual(
            self.client.get(reverse('accounts:user_list')).status_code,
            403,
        )
