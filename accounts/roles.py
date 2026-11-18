from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from .models import Role

ROLE_GROUP_MAPPING = {
    Role.ADMIN: 'Admin',
    Role.SALESMAN: 'Salesman',
    Role.PRODUCTION_SUPERVISOR: 'Production Supervisor',
    Role.ACCOUNTANT: 'Accountant / Auditor',
    Role.OWNER: 'Owner',
}

def setup_role_groups():
    """
    Idempotent utility to ensure that all 5 role groups exist in Django auth,
    and assigns role-specific permissions.
    """
    created_groups = {}
    for role_code, group_name in ROLE_GROUP_MAPPING.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        created_groups[role_code] = group

    # Setup standard permission mapping
    _assign_group_permissions(created_groups)
    return created_groups

def _assign_group_permissions(groups):
    """
    Configures role permissions for existing models:
    - Admin: Full access.
    - Salesman: View products, add/view/change customers.
    - Production Supervisor: View products, raw materials, add/view/change BOM.
    - Accountant / Auditor: Read-only access (view_*) across all models.
    - Owner: View access across all models and reports.
    """
    all_perms = Permission.objects.all()

    # Helper filter
    def get_perms(app_label, codenames):
        return Permission.objects.filter(content_type__app_label=app_label, codename__in=codenames)

    # 1. Admin
    admin_group = groups.get(Role.ADMIN)
    if admin_group:
        admin_group.permissions.set(all_perms)

    # 2. Salesman
    sales_group = groups.get(Role.SALESMAN)
    if sales_group:
        sales_perms = get_perms('masters', [
            'view_product',
            'view_customer', 'add_customer', 'change_customer'
        ])
        sales_group.permissions.set(sales_perms)

    # 3. Production Supervisor
    prod_group = groups.get(Role.PRODUCTION_SUPERVISOR)
    if prod_group:
        prod_perms = get_perms('masters', [
            'view_product',
            'view_rawmaterial',
            'view_billofmaterials', 'add_billofmaterials', 'change_billofmaterials'
        ])
        prod_group.permissions.set(prod_perms)

    # 4. Accountant / Auditor (Strictly read-only)
    acct_group = groups.get(Role.ACCOUNTANT)
    if acct_group:
        view_perms = Permission.objects.filter(codename__startswith='view_')
        acct_group.permissions.set(view_perms)

    # 5. Owner (Executive view)
    owner_group = groups.get(Role.OWNER)
    if owner_group:
        view_perms = Permission.objects.filter(codename__startswith='view_')
        owner_group.permissions.set(view_perms)

def assign_user_to_role_group(user, role):
    """
    Synchronizes the user's Django groups with their designated ERP role.
    Removes other ERP role groups and adds the user to the active role group.
    """
    setup_role_groups()
    erp_group_names = set(ROLE_GROUP_MAPPING.values())
    target_group_name = ROLE_GROUP_MAPPING.get(role)

    # Current groups for the user
    user_groups = list(user.groups.filter(name__in=erp_group_names))
    for g in user_groups:
        if g.name != target_group_name:
            user.groups.remove(g)

    if target_group_name:
        target_group = Group.objects.get(name=target_group_name)
        user.groups.add(target_group)
