import sys
import django
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from django.shortcuts import render
from django.utils import timezone
from django.conf import settings
from django.contrib.auth.models import User
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from accounts.models import UserProfile, Role
from accounts.access import role_required
from masters.models import Product, RawMaterial, Customer, Supplier, BillOfMaterials
from purchases.models import PurchaseInvoice, PurchaseStatus
from production.models import (
    ProductionDamageRecord,
    ProductionOrder,
    ProductionStatus,
)
from sales.models import (
    DispatchRecord,
    DispatchItem,
    Payment,
    SalesApprovalHistory,
    SalesInvoice,
    SalesOrder,
    SalesStatus,
)
from inventory.models import StockLedgerEntry
from inventory.services import get_all_product_stock_balances, get_all_raw_material_stock_balances
from inventory.reports import inventory_valuation_csv_response
from .reports import (
    dispatch_item_cost,
    dispatch_csv_response,
    inventory_ledger_csv_response,
    invoice_csv_response,
    outstanding_csv_response,
    payment_csv_response,
    product_profitability_csv_response,
    product_profitability_rows,
    production_cost_variance_csv_response,
    production_damage_csv_response,
    production_material_variance_csv_response,
    production_approval_history_csv_response,
    production_csv_response,
    production_excel_response,
    production_pdf_response,
    production_variance_csv_response,
    sales_approval_history_csv_response,
    sales_order_csv_response,
)
from .phases import get_phase_statuses


REPORT_ROLES = (Role.ADMIN, Role.OWNER, Role.ACCOUNTANT)


def _report_queryset(request, queryset, date_field, product_field=None, customer_field=None):
    start_text = request.GET.get('from', '').strip()
    end_text = request.GET.get('to', '').strip()
    try:
        start_value = date.fromisoformat(start_text) if start_text else None
        end_value = date.fromisoformat(end_text) if end_text else None
    except ValueError:
        return HttpResponseBadRequest('Dates must use YYYY-MM-DD format.')
    if start_value and end_value and end_value < start_value:
        return HttpResponseBadRequest('End date must be on or after start date.')
    if start_value:
        queryset = queryset.filter(**{f'{date_field}__gte': start_value})
    if end_value:
        queryset = queryset.filter(**{f'{date_field}__lte': end_value})

    for parameter, field in (
        ('product', product_field),
        ('customer', customer_field),
    ):
        value = request.GET.get(parameter, '').strip()
        if value and field:
            try:
                identifier = int(value)
                if identifier <= 0:
                    raise ValueError
            except ValueError:
                return HttpResponseBadRequest(f'{parameter.title()} must be a positive ID.')
            queryset = queryset.filter(**{field: identifier})
    return queryset


def _monthly_totals(queryset, date_field, amount_field):
    rows = queryset.annotate(month=TruncMonth(date_field)).values('month').annotate(
        amount=Sum(amount_field)
    ).order_by('month')
    return {row['month'].strftime('%Y-%m'): row['amount'] for row in rows if row['month']}


def _dispatch_item_revenue(dispatch_items):
    revenue = Decimal('0.00')
    for dispatch_item in dispatch_items:
        order_item = dispatch_item.sales_order_item
        if order_item.quantity:
            revenue += (
                order_item.subtotal * dispatch_item.quantity / order_item.quantity
            )
    return revenue.quantize(Decimal('0.01'))


def _monthly_dispatch_revenue(dispatch_records):
    totals = {}
    for dispatch in dispatch_records:
        month = dispatch.dispatch_date.strftime('%Y-%m')
        amount = _dispatch_item_revenue(dispatch.items.all())
        totals[month] = totals.get(month, Decimal('0.00')) + amount
    return totals


@role_required(Role.OWNER, Role.ADMIN)
def home(request):
    """
    FactoryFlow Home & Health Check View.
    Displays project overview, technical environment info, master models status, and benchmark BOM recipe.
    """
    db_engine = settings.DATABASES['default']['ENGINE'].split('.')[-1]
    
    user_count = User.objects.count()
    product_count = Product.objects.count()
    material_count = RawMaterial.objects.count()
    customer_count = Customer.objects.count()
    supplier_count = Supplier.objects.count()
    bom_count = BillOfMaterials.objects.count()
    pending_purchase_count = PurchaseInvoice.objects.filter(status=PurchaseStatus.DRAFT).count()
    received_purchase_count = PurchaseInvoice.objects.filter(status=PurchaseStatus.RECEIVED).count()
    inventory_summary = (
        get_all_raw_material_stock_balances(),
        get_all_product_stock_balances(),
    )
    low_stock_materials = [
        row for row in inventory_summary[0] if row.get('is_low_stock')
    ]
    start_date = request.GET.get('from', '').strip()
    end_date = request.GET.get('to', '').strip()
    try:
        start_value = date.fromisoformat(start_date) if start_date else None
        end_value = date.fromisoformat(end_date) if end_date else None
    except ValueError:
        return HttpResponseBadRequest('Dates must use YYYY-MM-DD format.')
    if start_value and end_value and end_value < start_value:
        return HttpResponseBadRequest('End date must be on or after start date.')
    completed_batches = ProductionOrder.objects.filter(status=ProductionStatus.COMPLETED)
    dispatch_records = DispatchRecord.objects.filter(
        sales_order__status__in=(
            SalesStatus.PARTIALLY_DISPATCHED,
            SalesStatus.DISPATCHED,
        ),
    ).prefetch_related('items__sales_order_item')
    payments = Payment.objects.filter(reversal__isnull=True)
    invoices = SalesInvoice.objects.all()
    damage_records = ProductionDamageRecord.objects.filter(is_voided=False)
    if start_value:
        completed_batches = completed_batches.filter(completion_date__date__gte=start_value)
        dispatch_records = dispatch_records.filter(
            dispatch_date__date__gte=start_value
        )
        payments = payments.filter(payment_date__gte=start_value)
        invoices = invoices.filter(issue_date__gte=start_value)
        damage_records = damage_records.filter(recorded_at__date__gte=start_value)
    if end_value:
        completed_batches = completed_batches.filter(completion_date__date__lte=end_value)
        dispatch_records = dispatch_records.filter(
            dispatch_date__date__lte=end_value
        )
        payments = payments.filter(payment_date__lte=end_value)
        invoices = invoices.filter(issue_date__lte=end_value)
        damage_records = damage_records.filter(recorded_at__date__lte=end_value)
    completed_batch_list = list(completed_batches.select_related('product'))
    total_manufacturing_cost = sum(
        (batch.total_cost for batch in completed_batch_list),
        Decimal('0.00'),
    )
    dispatch_items = list(
        DispatchItem.objects.filter(
            dispatch__in=dispatch_records,
        ).select_related(
            'dispatch',
            'sales_order_item',
            'stock_ledger_entry',
        ).prefetch_related(
            'stock_ledger_entry__source_allocations__source',
        )
    )
    total_sales_revenue = _dispatch_item_revenue(dispatch_items)
    dispatch_costs = [dispatch_item_cost(item) for item in dispatch_items]
    dispatch_cost_complete = all(
        cost is not None for cost, _ in dispatch_costs
    )
    total_dispatch_cogs = (
        sum((cost for cost, _ in dispatch_costs), Decimal('0.00')).quantize(
            Decimal('0.01'),
            rounding=ROUND_HALF_UP,
        )
        if dispatch_cost_complete
        else None
    )
    gross_margin = (
        (total_sales_revenue - total_dispatch_cogs).quantize(
            Decimal('0.01'),
            rounding=ROUND_HALF_UP,
        )
        if dispatch_cost_complete
        else None
    )
    total_collections = sum(
        (payment.amount for payment in payments),
        Decimal('0.00'),
    )
    invoice_list = list(invoices.select_related('sales_order__customer'))
    total_outstanding = sum(
        (invoice.outstanding_amount for invoice in invoice_list),
        Decimal('0.00'),
    )
    outstanding_invoice_count = sum(
        1 for invoice in invoice_list if invoice.outstanding_amount > Decimal('0.00')
    )
    outstanding_invoice_rows = sorted(
        (
            invoice for invoice in invoice_list
            if invoice.outstanding_amount > Decimal('0.00')
        ),
        key=lambda invoice: invoice.issue_date,
        reverse=True,
    )[:5]
    profitability_rows = []
    for product in Product.objects.filter(is_active=True):
        dispatch_items = DispatchItem.objects.filter(
            sales_order_item__product=product,
            dispatch__sales_order__status__in=(
                SalesStatus.PARTIALLY_DISPATCHED,
                SalesStatus.DISPATCHED,
            ),
        )
        if start_value:
            dispatch_items = dispatch_items.filter(
                dispatch__dispatch_date__date__gte=start_value
            )
        if end_value:
            dispatch_items = dispatch_items.filter(
                dispatch__dispatch_date__date__lte=end_value
            )
        revenue = _dispatch_item_revenue(
            dispatch_items.select_related('sales_order_item')
        )
        product_costs = [
            dispatch_item_cost(item)
            for item in dispatch_items.select_related(
                'dispatch',
                'sales_order_item',
                'stock_ledger_entry',
            ).prefetch_related(
                'stock_ledger_entry__source_allocations__source',
            )
        ]
        cost_is_complete = all(cost is not None for cost, _ in product_costs)
        cost = (
            sum((cost for cost, _ in product_costs), Decimal('0.00')).quantize(
                Decimal('0.01'),
                rounding=ROUND_HALF_UP,
            )
            if cost_is_complete
            else None
        )
        profit = (
            (revenue - cost).quantize(
                Decimal('0.01'),
                rounding=ROUND_HALF_UP,
            )
            if cost is not None
            else None
        )
        profitability_rows.append({
            'name': product.name,
            'revenue': float(revenue),
            'cost': float(cost) if cost is not None else None,
            'profit': float(profit) if profit is not None else None,
            'cost_status': (
                'FIFO production sources'
                if cost_is_complete and product_costs
                else 'No dispatches'
                if cost_is_complete
                else 'Legacy or missing source cost'
            ),
        })
    trend_start = start_value or (timezone.localdate() - timedelta(days=180))
    trend_dispatches = DispatchRecord.objects.filter(
        sales_order__status__in=(
            SalesStatus.PARTIALLY_DISPATCHED,
            SalesStatus.DISPATCHED,
        ),
        dispatch_date__date__gte=trend_start,
    ).prefetch_related('items__sales_order_item')
    trend_payments = Payment.objects.filter(
        reversal__isnull=True,
        payment_date__gte=trend_start,
    )
    trend_production = ProductionOrder.objects.filter(
        status=ProductionStatus.COMPLETED,
        completion_date__date__gte=trend_start,
    )
    if end_value:
        trend_dispatches = trend_dispatches.filter(
            dispatch_date__date__lte=end_value
        )
        trend_payments = trend_payments.filter(payment_date__lte=end_value)
        trend_production = trend_production.filter(completion_date__date__lte=end_value)
    sales_trend = _monthly_dispatch_revenue(trend_dispatches)
    collections_trend = _monthly_totals(trend_payments, 'payment_date', 'amount')
    production_trend = _monthly_totals(trend_production, 'completion_date', 'total_cost')
    trend_months = sorted(set(sales_trend) | set(collections_trend) | set(production_trend))
    trend_data = {
        'months': trend_months,
        'sales': [float(sales_trend.get(month, 0) or 0) for month in trend_months],
        'collections': [
            float(collections_trend.get(month, 0) or 0) for month in trend_months
        ],
        'production': [
            float(production_trend.get(month, 0) or 0) for month in trend_months
        ],
    }
    inventory_value = sum(
        (row['stock_value'] for row in inventory_summary[0] + inventory_summary[1]),
        Decimal('0.00'),
    )
    total_dispatches = DispatchRecord.objects.filter(
        sales_order__status__in=(
            SalesStatus.PARTIALLY_DISPATCHED,
            SalesStatus.DISPATCHED,
        ),
    )
    if start_value:
        total_dispatches = total_dispatches.filter(dispatch_date__date__gte=start_value)
    if end_value:
        total_dispatches = total_dispatches.filter(dispatch_date__date__lte=end_value)
    damage_record_count = damage_records.count()
    pending_approval_orders = SalesOrder.objects.filter(
        approval_status='PENDING',
        status=SalesStatus.DRAFT,
    ).select_related('customer')
    recent_purchases = PurchaseInvoice.objects.select_related('supplier')[:5]
    recent_production = ProductionOrder.objects.select_related('product')[:5]
    recent_sales = SalesOrder.objects.select_related('customer')[:5]

    murukku_product = Product.objects.filter(code="PRD-MUR-200G").first()
    bom_items = BillOfMaterials.objects.filter(
        product=murukku_product
    ).select_related('raw_material') if murukku_product else []
    customers = Customer.objects.all()[:5]
    suppliers = Supplier.objects.all()[:5]
    raw_materials = RawMaterial.objects.all()[:10]

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
        'phases': get_phase_statuses(),
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
        'pending_purchase_count': pending_purchase_count,
        'received_purchase_count': received_purchase_count,
        'raw_material_stock': inventory_summary[0],
        'product_stock': inventory_summary[1],
        'recent_purchases': recent_purchases,
        'recent_production': recent_production,
        'recent_sales': recent_sales,
        'low_stock_materials': low_stock_materials,
        'total_manufacturing_cost': total_manufacturing_cost,
        'total_sales_revenue': total_sales_revenue,
        'gross_margin': gross_margin,
        'total_dispatch_cogs': total_dispatch_cogs,
        'dispatch_cost_complete': dispatch_cost_complete,
        'total_collections': total_collections,
        'total_outstanding': total_outstanding,
        'outstanding_invoice_count': outstanding_invoice_count,
        'outstanding_invoice_rows': outstanding_invoice_rows,
        'total_production_batches': len(completed_batch_list),
        'total_dispatches': total_dispatches.count(),
        'damage_record_count': damage_record_count,
        'inventory_value': inventory_value,
        'trend_data': trend_data,
        'pending_approval_orders': pending_approval_orders,
        'low_stock_threshold': settings.LOW_STOCK_RAW_MATERIAL_THRESHOLD,
        'from_date': start_date or '',
        'to_date': end_date or '',
        'profitability_rows': profitability_rows,
    }
    return render(request, 'core/home.html', context)


def _production_report(request, response_factory):
    if not request.user.is_authenticated:
        return HttpResponseForbidden("Authentication required.")
    profile = getattr(request.user, 'profile', None)
    if not request.user.is_superuser and (
        not profile
        or not profile.is_active
        or profile.role not in REPORT_ROLES
    ):
        return HttpResponseForbidden("You do not have permission to download audit reports.")
    queryset = _report_queryset(
        request,
        ProductionOrder.objects.select_related('product').order_by('-start_date'),
        'start_date',
        product_field='product_id',
    )
    if isinstance(queryset, HttpResponseBadRequest):
        return queryset
    return response_factory(queryset)


@role_required(*REPORT_ROLES)
def production_csv(request):
    return _production_report(request, production_csv_response)


@role_required(*REPORT_ROLES)
def production_excel(request):
    return _production_report(request, production_excel_response)


@role_required(*REPORT_ROLES)
def production_pdf(request):
    return _production_report(request, production_pdf_response)


@role_required(*REPORT_ROLES)
def inventory_valuation_csv(request):
    if not request.user.is_authenticated:
        return HttpResponseForbidden("Authentication required.")
    profile = getattr(request.user, 'profile', None)
    if not request.user.is_superuser and (
        not profile or not profile.is_active or profile.role not in REPORT_ROLES
    ):
        return HttpResponseForbidden("You do not have permission to download valuation reports.")
    try:
        start_date = date.fromisoformat(request.GET['from']) if request.GET.get('from') else None
        end_date = date.fromisoformat(request.GET['to']) if request.GET.get('to') else None
    except ValueError:
        return HttpResponseBadRequest('Dates must use YYYY-MM-DD format.')
    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest('End date must be on or after start date.')
    filters = {}
    for parameter, model in (
        ('material', RawMaterial),
        ('product', Product),
    ):
        value = request.GET.get(parameter, '').strip()
        if not value:
            continue
        try:
            identifier = int(value)
            if identifier <= 0:
                raise ValueError
        except ValueError:
            return HttpResponseBadRequest(f'{parameter.title()} must be a positive ID.')
        if not model.objects.filter(pk=identifier).exists():
            return HttpResponseBadRequest(f'{parameter.title()} was not found.')
        filters[f'{parameter}_id'] = identifier
    return inventory_valuation_csv_response(start_date, end_date, **filters)


@role_required(*REPORT_ROLES)
def production_variance_csv(request):
    return _production_report(request, production_variance_csv_response)


def _product_profitability_filters(request):
    try:
        start_date = date.fromisoformat(request.GET['from']) if request.GET.get('from') else None
        end_date = date.fromisoformat(request.GET['to']) if request.GET.get('to') else None
    except ValueError:
        return HttpResponseBadRequest('Dates must use YYYY-MM-DD format.')
    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest('End date must be on or after start date.')
    products = Product.objects.filter(is_active=True)
    product_id = request.GET.get('product', '').strip()
    if product_id:
        try:
            product_id = int(product_id)
            if product_id <= 0:
                raise ValueError
        except ValueError:
            return HttpResponseBadRequest('Product must be a positive ID.')
        if not products.filter(pk=product_id).exists():
            return HttpResponseBadRequest('Product was not found.')
        products = products.filter(pk=product_id)
    return products, start_date, end_date, str(product_id) if product_id else ''


@role_required(*REPORT_ROLES)
def product_profitability_csv(request):
    report_filters = _product_profitability_filters(request)
    if isinstance(report_filters, HttpResponseBadRequest):
        return report_filters
    products, start_date, end_date, _selected_product = report_filters
    return product_profitability_csv_response(
        products, start_date, end_date
    )


@role_required(*REPORT_ROLES)
def product_profitability_report(request):
    report_filters = _product_profitability_filters(request)
    if isinstance(report_filters, HttpResponseBadRequest):
        return report_filters
    products, start_date, end_date, selected_product = report_filters
    return render(request, 'core/product_profitability.html', {
        'rows': product_profitability_rows(products, start_date, end_date),
        'products': Product.objects.filter(is_active=True),
        'selected_product': selected_product,
        'from_date': request.GET.get('from', ''),
        'to_date': request.GET.get('to', ''),
        'csv_query': request.GET.urlencode(),
    })


@role_required(*REPORT_ROLES)
def production_cost_variance_csv(request):
    return _production_report(
        request,
        production_cost_variance_csv_response,
    )


@role_required(*REPORT_ROLES)
def production_material_variance_csv(request):
    return _production_report(request, production_material_variance_csv_response)


@role_required(*REPORT_ROLES)
def production_damage_csv(request):
    if not request.user.is_authenticated:
        return HttpResponseForbidden("Authentication required.")
    profile = getattr(request.user, 'profile', None)
    if not request.user.is_superuser and (
        not profile or not profile.is_active or profile.role not in REPORT_ROLES
    ):
        return HttpResponseForbidden("You do not have permission to download damage reports.")
    records = _report_queryset(
        request,
        ProductionDamageRecord.objects.select_related(
        'production_order', 'raw_material', 'product', 'recorded_by'
        ).order_by('-recorded_at'),
        'recorded_at__date',
        product_field='production_order__product_id',
    )
    if isinstance(records, HttpResponseBadRequest):
        return records
    return production_damage_csv_response(records)


@role_required(*REPORT_ROLES)
def production_approval_history_csv(request):
    return _production_report(request, production_approval_history_csv_response)


@role_required(*REPORT_ROLES)
def sales_orders_csv(request):
    orders = _report_queryset(
        request,
        SalesOrder.objects.select_related('customer').prefetch_related('items__product'),
        'order_date',
        product_field='items__product_id',
        customer_field='customer_id',
    )
    if isinstance(orders, HttpResponseBadRequest):
        return orders
    return sales_order_csv_response(orders.distinct())


@role_required(*REPORT_ROLES)
def sales_approval_audit_csv(request):
    orders = _report_queryset(
        request,
        SalesOrder.objects.select_related('customer').prefetch_related(
            'approval_history__action_by'
        ),
        'order_date',
        product_field='items__product_id',
        customer_field='customer_id',
    )
    if isinstance(orders, HttpResponseBadRequest):
        return orders
    return sales_approval_history_csv_response(orders.distinct())


@role_required(*REPORT_ROLES)
def dispatch_register_csv(request):
    dispatches = _report_queryset(
        request,
        DispatchRecord.objects.select_related(
            'sales_order__customer', 'dispatched_by'
        ).prefetch_related(
            'items__sales_order_item__product',
            'items__sales_order_item__sales_order__customer',
        ),
        'dispatch_date__date',
        product_field='items__sales_order_item__product_id',
        customer_field='sales_order__customer_id',
    )
    if isinstance(dispatches, HttpResponseBadRequest):
        return dispatches
    return dispatch_csv_response(dispatches.distinct())


@role_required(*REPORT_ROLES)
def sales_invoice_register_csv(request):
    invoices = _report_queryset(
        request,
        SalesInvoice.objects.select_related('sales_order__customer'),
        'issue_date',
        product_field='sales_order__items__product_id',
        customer_field='sales_order__customer_id',
    )
    if isinstance(invoices, HttpResponseBadRequest):
        return invoices
    return invoice_csv_response(invoices.distinct())


@role_required(*REPORT_ROLES)
def payment_register_csv(request):
    payments = _report_queryset(
        request,
        Payment.objects.select_related(
            'invoice__sales_order__customer', 'created_by', 'reversal__reversed_by'
        ),
        'payment_date',
        product_field='invoice__sales_order__items__product_id',
        customer_field='invoice__sales_order__customer_id',
    )
    if isinstance(payments, HttpResponseBadRequest):
        return payments
    return payment_csv_response(payments.distinct())


@role_required(*REPORT_ROLES)
def outstanding_invoices_csv(request):
    invoices = _report_queryset(
        request,
        SalesInvoice.objects.select_related('sales_order__customer'),
        'issue_date',
        product_field='sales_order__items__product_id',
        customer_field='sales_order__customer_id',
    )
    if isinstance(invoices, HttpResponseBadRequest):
        return invoices
    return outstanding_csv_response(invoices.distinct())


@role_required(*REPORT_ROLES)
def inventory_ledger_audit_csv(request):
    entries = _report_queryset(
        request,
        StockLedgerEntry.objects.select_related(
            'raw_material', 'product', 'reversed_entry'
        ).prefetch_related('source_allocations__source'),
        'movement_date__date',
        product_field='product_id',
    )
    if isinstance(entries, HttpResponseBadRequest):
        return entries
    raw_material_id = request.GET.get('material', '').strip()
    if raw_material_id:
        try:
            raw_material_id = int(raw_material_id)
            if raw_material_id <= 0:
                raise ValueError
        except ValueError:
            return HttpResponseBadRequest('Material must be a positive ID.')
        entries = entries.filter(raw_material_id=raw_material_id)
    return inventory_ledger_csv_response(entries.distinct())
