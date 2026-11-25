from functools import lru_cache
from importlib import import_module
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.template import TemplateDoesNotExist, loader
from django.urls import NoReverseMatch, reverse


def _routes_exist(*routes):
    for route_name, kwargs in routes:
        try:
            reverse(route_name, kwargs=kwargs)
        except NoReverseMatch:
            return False
    return True


def _model_exists(app_label, model_name):
    try:
        return apps.get_model(app_label, model_name) is not None
    except LookupError:
        return False


def _model_has_fields(app_label, model_name, *field_names):
    try:
        model = apps.get_model(app_label, model_name)
        existing = {field.name for field in model._meta.get_fields()}
    except LookupError:
        return False
    return all(name in existing for name in field_names)


def _callables_exist(module_name, *names):
    try:
        module = import_module(module_name)
    except ImportError:
        return False
    return all(callable(getattr(module, name, None)) for name in names)


def _files_exist(*paths):
    return all((settings.BASE_DIR / Path(path)).is_file() for path in paths)


def _templates_exist(*names):
    try:
        for name in names:
            loader.get_template(name)
    except TemplateDoesNotExist:
        return False
    return True


@lru_cache(maxsize=1)
def get_phase_statuses():
    master_routes = [
        ('masters:summary', None),
        *[
            (route, {'resource': resource})
            for resource in ('products', 'raw-materials', 'customers', 'suppliers', 'bom')
            for route in ('masters:list', 'masters:create')
        ],
        *[
            (route, {'resource': resource, 'pk': 1})
            for resource in ('products', 'raw-materials', 'customers', 'suppliers', 'bom')
            for route in ('masters:detail', 'masters:update', 'masters:delete')
        ],
    ]
    purchase_routes = [
        ('purchases:list', None),
        ('purchases:create', None),
        *[
            (route, {'invoice_id': 1})
            for route in (
                'purchases:detail',
                'purchases:update',
                'purchases:receive',
                'purchases:cancel',
            )
        ],
    ]
    production_routes = [
        ('production:list', None),
        ('production:create', None),
        *[
            (route, {'order_id': 1})
            for route in (
                'production:detail',
                'production:edit',
                'production:approve',
                'production:complete',
                'production:cancel',
            )
        ],
    ]
    damage_routes = [
        ('production:damage_list', None),
        ('production:damage_create', {'damage_type': 'SCRAP'}),
        *[
            (route, {'record_id': 1})
            for route in ('production:damage_edit', 'production:damage_void')
        ],
    ]
    sales_order_routes = [
        ('sales:order_list', None),
        ('sales:approval_queue', None),
        ('sales:create_order', None),
        ('sales:detail', {'order_id': 1}),
        ('sales:edit_order', {'order_id': 1}),
        ('sales:approve_order', {'order_id': 1}),
        ('sales:reject_order', {'order_id': 1}),
        ('sales:confirm_order', {'order_id': 1}),
        ('sales:cancel_order', {'order_id': 1}),
    ]
    dispatch_routes = [
        ('sales:dispatch', {'order_id': 1}),
        ('sales:shortage_list', None),
    ]
    invoice_routes = [
        ('sales:generate_invoice', {'order_id': 1}),
        ('sales:invoice_pdf', {'invoice_id': 1}),
        ('sales:add_payment', {'invoice_id': 1}),
        ('sales:collection_history', None),
        ('sales:payment_receipt', {'payment_id': 1}),
        ('sales:reverse_payment', {'payment_id': 1}),
    ]
    report_routes = [
        (name, None)
        for name in (
            'inventory_valuation_csv',
            'product_profitability_report',
            'production_csv',
            'production_excel',
            'production_pdf',
            'production_variance_csv',
            'production_cost_variance_csv',
            'production_material_variance_csv',
            'production_damage_csv',
            'production_approval_history_csv',
            'product_profitability_csv',
            'sales_orders_csv',
            'sales_approval_audit_csv',
            'dispatch_register_csv',
            'sales_invoice_register_csv',
            'payment_register_csv',
            'outstanding_invoices_csv',
            'inventory_ledger_audit_csv',
        )
    ]
    checks = [
        {
            'number': 1,
            'title': 'Overview & Setup',
            'deliverables': 'Django entry point, health endpoint, environment settings and static assets',
            'url_name': 'home',
            'url_kwargs': None,
            'requirements': [
                ('Overview, health and login routes', lambda: _routes_exist(
                    ('home', None), ('health', None), ('accounts:login', None)
                )),
                ('Configured Django settings and static files', lambda: (
                    bool(settings.DATABASES.get('default', {}).get('ENGINE'))
                    and bool(settings.STATIC_URL)
                    and bool(settings.STATIC_ROOT)
                    and 'whitenoise' in settings.STORAGES['staticfiles']['BACKEND']
                )),
                ('Health endpoint and dashboard template', lambda: (
                    _callables_exist('core.health', 'health')
                    and _templates_exist('core/home.html')
                )),
            ],
        },
        {
            'number': 2,
            'title': 'Users & Roles',
            'deliverables': 'Five roles, protected user creation, detail, edit and safe deactivation',
            'url_name': 'accounts:user_list',
            'url_kwargs': None,
            'requirements': [
                ('User profile and five role definitions', lambda: (
                    _model_exists('accounts', 'UserProfile')
                    and len(apps.get_model('accounts', 'UserProfile')
                            ._meta.get_field('role').choices) == 5
                )),
                ('User list/create/detail/edit/deactivate routes', lambda: _routes_exist(
                    ('accounts:user_list', None),
                    ('accounts:user_create', None),
                    ('accounts:user_detail', {'user_id': 1}),
                    ('accounts:user_update', {'user_id': 1}),
                    ('accounts:user_deactivate', {'user_id': 1}),
                )),
                ('User management pages', lambda: _templates_exist(
                    'accounts/user_list.html',
                    'accounts/user_form.html',
                    'accounts/user_detail.html',
                    'accounts/user_confirm_deactivate.html',
                )),
            ],
        },
        {
            'number': 3,
            'title': 'Masters & BOM',
            'deliverables': 'Product, raw material, customer, supplier and BOM management with benchmark seeding',
            'url_name': 'masters:summary',
            'url_kwargs': None,
            'requirements': [
                ('Master models', lambda: all(
                    _model_exists('masters', name)
                    for name in ('Product', 'RawMaterial', 'Customer', 'Supplier', 'BillOfMaterials')
                )),
                ('List/add/details/edit/delete routes for all master records', lambda: _routes_exist(
                    *master_routes
                )),
                ('BOM calculation and benchmark seed command', lambda: (
                    _callables_exist('masters.services', 'calculate_bom_requirements')
                    and _callables_exist(
                        'masters.management.commands.seed_benchmark_data',
                        'Command',
                    )
                )),
            ],
        },
        {
            'number': 4,
            'title': 'Purchases & Ledger',
            'deliverables': 'Multi-line draft purchases, safe edits, receipt and confirmed cancellation',
            'url_name': 'purchases:list',
            'url_kwargs': None,
            'requirements': [
                ('Purchase models and multi-item formset', lambda: (
                    _model_exists('purchases', 'PurchaseInvoice')
                    and _model_exists('purchases', 'PurchaseItem')
                    and _callables_exist('purchases.forms', 'PurchaseItemFormSet')
                )),
                ('Purchase list/create/detail/edit/receive/cancel routes', lambda: _routes_exist(
                    *purchase_routes
                )),
                ('Atomic receipt and cancellation services', lambda: _callables_exist(
                    'purchases.services',
                    'receive_purchase_invoice',
                    'cancel_purchase_invoice',
                )),
            ],
        },
        {
            'number': 5,
            'title': 'Inventory & FIFO',
            'deliverables': 'Append-only stock ledger, FIFO source allocation and linked reversal',
            'url_name': 'inventory:summary',
            'url_kwargs': None,
            'requirements': [
                ('Ledger and source-allocation models', lambda: all(
                    _model_exists('inventory', name)
                    for name in (
                        'StockLedgerEntry',
                        'InventorySource',
                        'InventorySourceAllocation',
                    )
                )),
                ('Stock creation, FIFO costing and atomic reversal services', lambda: _callables_exist(
                    'inventory.services',
                    'create_stock_ledger_entry',
                    'create_reversal_ledger_entry',
                    'get_inventory_source_cost',
                )),
                ('Inventory summary and audit export routes', lambda: _routes_exist(
                    ('inventory:summary', None),
                    ('inventory_ledger_audit_csv', None),
                )),
            ],
        },
        {
            'number': 6,
            'title': 'Production Runs',
            'deliverables': 'Approved production, actual consumption, completion costing and safe cancellation',
            'url_name': 'production:list',
            'url_kwargs': None,
            'requirements': [
                ('Production orders, material use and approval history', lambda: all(
                    _model_exists('production', name)
                    for name in (
                        'ProductionOrder',
                        'ProductionMaterialUsage',
                        'ProductionApprovalHistory',
                    )
                )),
                ('Production create/detail/edit/approve/complete/cancel routes', lambda: _routes_exist(
                    *production_routes
                )),
                ('Production business services', lambda: _callables_exist(
                    'production.services',
                    'create_production_order',
                    'approve_production_order',
                    'complete_production_order',
                    'cancel_production_order',
                )),
            ],
        },
        {
            'number': 7,
            'title': 'Damage & Scrap',
            'deliverables': 'Recorded material/product losses with editable notes and auditable void reversals',
            'url_name': 'production:damage_list',
            'url_kwargs': None,
            'requirements': [
                ('Damage record model and ledger-linked services', lambda: (
                    _model_exists('production', 'ProductionDamageRecord')
                    and _callables_exist(
                        'production.services',
                        'record_damage',
                        'void_damage_record',
                    )
                )),
                ('Damage list/create/edit/void routes', lambda: _routes_exist(
                    *damage_routes
                )),
            ],
        },
        {
            'number': 8,
            'title': 'Sales Orders',
            'deliverables': 'Multi-product order entry, draft editing, approval, confirmation and cancellation',
            'url_name': 'sales:order_list',
            'url_kwargs': None,
            'requirements': [
                ('Sales order and line-item models', lambda: (
                    _model_exists('sales', 'SalesOrder')
                    and _model_exists('sales', 'SalesOrderItem')
                    and _model_exists('sales', 'SalesApprovalHistory')
                )),
                ('Sales order lifecycle routes', lambda: _routes_exist(
                    *sales_order_routes
                )),
                ('Transactional order creation, edit and approval services', lambda: _callables_exist(
                    'sales.services',
                    'create_sales_order',
                    'update_sales_order',
                    'approve_sales_order',
                    'confirm_sales_order',
                    'cancel_sales_order',
                )),
            ],
        },
        {
            'number': 9,
            'title': 'Dispatch',
            'deliverables': 'Partial/full dispatch records, stock checks, shortages and order reversal',
            'url_name': 'sales:order_list',
            'url_kwargs': None,
            'requirements': [
                ('Dispatch records and line items', lambda: (
                    _model_exists('sales', 'DispatchRecord')
                    and _model_exists('sales', 'DispatchItem')
                    and _model_exists('sales', 'SalesShortage')
                )),
                ('Dispatch, shortage and cancellation routes', lambda: _routes_exist(
                    *dispatch_routes,
                    ('sales:dispatch', {'order_id': 1}),
                    ('sales:cancel_order', {'order_id': 1}),
                )),
                ('Transactional dispatch service', lambda: _callables_exist(
                    'sales.services',
                    'dispatch_sales_order',
                    'cancel_sales_order',
                )),
            ],
        },
        {
            'number': 10,
            'title': 'Invoices & Payments',
            'deliverables': 'GST invoices, receipts, partial balances and reversal history',
            'url_name': 'sales:collection_history',
            'url_kwargs': None,
            'requirements': [
                ('Invoice, payment and reversal models', lambda: all(
                    _model_exists('sales', name)
                    for name in (
                        'SalesInvoice',
                        'SalesInvoiceLine',
                        'Payment',
                        'PaymentReversal',
                    )
                )),
                ('Invoice, payment, receipt and reversal routes', lambda: _routes_exist(
                    *invoice_routes
                )),
                ('GST billing and payment services', lambda: _callables_exist(
                    'sales.billing',
                    'calculate_gst_breakdown',
                    'create_sales_invoice',
                    'record_payment',
                    'reverse_payment',
                )),
            ],
        },
        {
            'number': 11,
            'title': 'Costing & Reports',
            'deliverables': 'Filtered profitability report and financial, production, inventory and audit exports',
            'url_name': 'inventory_valuation_csv',
            'url_kwargs': None,
            'requirements': [
                ('Production, inventory, sales and audit export routes', lambda: _routes_exist(
                    *report_routes
                )),
                ('FIFO material cost and export services', lambda: (
                    _callables_exist(
                        'core.reports',
                        'production_source_basis',
                        'product_profitability_rows',
                        'production_cost_variance_csv_response',
                        'product_profitability_csv_response',
                    )
                    and _callables_exist(
                        'inventory.reports',
                        'inventory_valuation_csv_response',
                    )
                )),
                ('Finished-goods source and dispatch ledger links', lambda: (
                    _model_has_fields(
                        'inventory',
                        'InventorySource',
                        'product',
                        'stock_ledger_entry',
                    )
                    and _model_has_fields(
                        'inventory',
                        'InventorySourceAllocation',
                        'source',
                        'stock_ledger_entry',
                    )
                    and _model_has_fields(
                        'sales',
                        'DispatchItem',
                        'stock_ledger_entry',
                    )
                )),
                ('FIFO finished-goods allocation and dispatch costing', lambda: (
                    _callables_exist(
                        'inventory.services',
                        'create_inventory_source_record',
                        'get_inventory_source_cost',
                    )
                    and _callables_exist(
                        'core.reports',
                        'dispatch_item_cost',
                        'product_profitability_csv_response',
                    )
                    and _callables_exist('sales.services', 'dispatch_sales_order')
                )),
                ('Persisted planned unit-cost snapshot and variance', lambda: (
                    _model_has_fields(
                        'production',
                        'ProductionMaterialUsage',
                        'planned_unit_rate',
                        'planned_quantity',
                    )
                    and _callables_exist(
                        'core.reports',
                        '_variance_costs',
                        'production_cost_variance_csv_response',
                    )
                    and _callables_exist(
                        'production.services',
                        'create_production_order',
                    )
                )),
            ],
        },
        {
            'number': 12,
            'title': 'Owner Dashboard',
            'deliverables': 'Role-protected sales, collection, outstanding, production and stock summaries',
            'url_name': 'home',
            'url_kwargs': None,
            'requirements': [
                ('Owner dashboard route and template', lambda: (
                    _routes_exist(('home', None))
                    and _templates_exist('core/home.html')
                )),
                ('Dashboard date and trend calculations', lambda: _callables_exist(
                    'core.views',
                    '_monthly_totals',
                    '_monthly_dispatch_revenue',
                    '_dispatch_item_revenue',
                )),
                ('Inventory and outstanding-balance calculations', lambda: (
                    _callables_exist(
                        'inventory.services',
                        'get_all_raw_material_stock_balances',
                        'get_all_product_stock_balances',
                    )
                    and _model_exists('sales', 'SalesInvoice')
                )),
            ],
        },
        {
            'number': 13,
            'title': 'Benchmark & Deployment',
            'deliverables': 'Repeatable benchmark verification, container configuration, CI and health check',
            'url_name': 'health',
            'url_kwargs': None,
            'requirements': [
                ('Benchmark verification command and end-to-end test', lambda: (
                    _callables_exist(
                        'masters.management.commands.verify_benchmark',
                        'Command',
                    )
                    and _files_exist('core/tests.py')
                )),
                ('Docker, Compose and CI configuration', lambda: _files_exist(
                    'Dockerfile',
                    'docker-compose.yml',
                    '.github/workflows/ci.yml',
                )),
                ('Health endpoint', lambda: _routes_exist(('health', None))),
            ],
        },
    ]

    results = []
    for phase in checks:
        missing = [
            label
            for label, check in phase['requirements']
            if not check()
        ]
        result = {
            'number': phase['number'],
            'title': phase['title'],
            'deliverables': phase['deliverables'],
            'url': reverse(phase['url_name'], kwargs=phase['url_kwargs']),
            'total_checks': len(phase['requirements']),
            'completed_checks': len(phase['requirements']) - len(missing),
            'missing': missing,
            'status': 'Complete' if not missing else 'Partial',
        }
        results.append(result)
    return results
