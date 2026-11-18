import sys
import django
from django.shortcuts import render
from django.utils import timezone
from django.conf import settings
from django.contrib.auth.models import User
from accounts.models import UserProfile, Role
from masters.models import Product, RawMaterial, Customer, Supplier, BillOfMaterials

def home(request):
    """
    FactoryFlow Home & Health Check View.
    Displays project overview, technical environment info, master models status, and benchmark BOM recipe.
    """
    db_engine = settings.DATABASES['default']['ENGINE'].split('.')[-1]
    
    # Query summary counts & benchmark data safely
    try:
        user_count = User.objects.count()
        product_count = Product.objects.count()
        material_count = RawMaterial.objects.count()
        customer_count = Customer.objects.count()
        supplier_count = Supplier.objects.count()
        bom_count = BillOfMaterials.objects.count()

        murukku_product = Product.objects.filter(code="PRD-MUR-200G").first()
        bom_items = BillOfMaterials.objects.filter(product=murukku_product).select_related('raw_material') if murukku_product else []
        customers = Customer.objects.all()[:5]
        suppliers = Supplier.objects.all()[:5]
        raw_materials = RawMaterial.objects.all()[:10]
    except Exception:
        user_count = product_count = material_count = customer_count = supplier_count = bom_count = 0
        murukku_product = None
        bom_items = []
        customers = []
        suppliers = []
        raw_materials = []

    roles_list = [
        {'code': Role.ADMIN, 'name': 'Admin', 'desc': 'Manage users, masters, approvals, full access', 'badge': 'danger'},
        {'code': Role.SALESMAN, 'name': 'Salesman', 'desc': 'Create and view own sales orders', 'badge': 'primary'},
        {'code': Role.PRODUCTION_SUPERVISOR, 'name': 'Production Supervisor', 'desc': 'Manage BOM and production records', 'badge': 'warning text-dark'},
        {'code': Role.ACCOUNTANT, 'name': 'Accountant / Auditor', 'desc': 'Read-only access to financial and audit reports', 'badge': 'info text-dark'},
        {'code': Role.OWNER, 'name': 'Owner', 'desc': 'View overall business dashboard and reports', 'badge': 'dark'},
    ]

    context = {
        'project_name': 'FactoryFlow',
        'subtitle': 'Smart Order-to-Audit Management System',
        'organisation': 'NIT Tiruchirappalli - Winter Internship 2026',
        'python_version': sys.version.split(' ')[0],
        'django_version': django.get_version(),
        'db_engine': db_engine,
        'current_time': timezone.now(),
        'timezone': settings.TIME_ZONE,
        'debug_mode': settings.DEBUG,
        'phase': 3,
        'phase_title': 'Phase 3: Master Data Management & Benchmark Seed Data',
        'user_count': user_count,
        'product_count': product_count,
        'material_count': material_count,
        'customer_count': customer_count,
        'supplier_count': supplier_count,
        'bom_count': bom_count,
        'roles_list': roles_list,
        'murukku_product': murukku_product,
        'bom_items': bom_items,
        'customers': customers,
        'suppliers': suppliers,
        'raw_materials': raw_materials,
    }
    return render(request, 'core/home.html', context)
