from django.test import TestCase
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
