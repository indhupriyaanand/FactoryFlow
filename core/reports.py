import csv
from collections import defaultdict
from io import BytesIO

from django.http import HttpResponse
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from decimal import Decimal, ROUND_HALF_UP

from inventory.models import InventorySourceType, StockLedgerEntry, StockMovementType
from inventory.services import get_inventory_source_cost


def production_source_basis(order):
    if order.status != 'COMPLETED':
        return 'Not completed'
    entries = StockLedgerEntry.objects.filter(
        raw_material__isnull=False,
        reference_number__in=(order.order_number, f'{order.order_number}-DAMAGE'),
        movement_type=StockMovementType.OUT,
    )
    if not entries.exists():
        return 'Missing source allocation'
    has_legacy_source = False
    for entry in entries:
        allocations = list(entry.source_allocations.select_related('source'))
        if not allocations or sum(
            (allocation.quantity for allocation in allocations),
            Decimal('0.0000'),
        ) != entry.quantity:
            return 'Missing source allocation'
        if any(
            allocation.source.source_type == InventorySourceType.LEGACY
            for allocation in allocations
        ):
            has_legacy_source = True
    return 'Legacy source used' if has_legacy_source else 'FIFO receipt sources'


def _production_rows(queryset):
    return [
        [
            order.order_number,
            order.product.name,
            order.status,
            order.produced_quantity,
            order.material_cost,
            order.labour_cost,
            order.power_cost,
            order.total_cost,
            order.unit_cost,
            production_source_basis(order),
        ]
        for order in queryset
    ]


def production_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="production-costing.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Order', 'Product', 'Status', 'Output', 'Material Cost', 'Labour Cost',
        'Power Cost', 'Total Cost', 'Unit Cost', 'Source Cost Basis',
    ])
    writer.writerows(_production_rows(queryset))
    return response


def production_variance_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="production-variance.csv"'
    writer = csv.writer(response)
    writer.writerow(['Order', 'Product', 'Target', 'Output', 'Yield Variance',
                     'Oil Variance', 'Damaged Pouches', 'Approval'])
    for order in queryset:
        writer.writerow([
            order.order_number,
            order.product.name,
            order.target_quantity,
            order.produced_quantity,
            order.yield_variance_quantity,
            order.oil_variance_quantity,
            order.damaged_pouch_quantity,
            order.get_approval_status_display(),
        ])
    return response


def production_excel_response(queryset):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Production Costing'
    sheet.append([
        'Order', 'Product', 'Status', 'Output', 'Material Cost', 'Labour Cost',
        'Power Cost', 'Total Cost', 'Unit Cost', 'Source Cost Basis',
    ])
    for row in _production_rows(queryset):
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="production-costing.xlsx"'
    return response


def production_pdf_response(queryset):
    buffer = BytesIO()
    document = canvas.Canvas(buffer, pagesize=A4)
    document.setTitle('FactoryFlow Production Audit')
    document.drawString(50, 800, 'FactoryFlow Production Costing Audit')
    y = 770
    for row in _production_rows(queryset):
        document.drawString(50, y, f'{row[0]} | {row[1]} | Output {row[3]} | Total ₹{row[7]}')
        y -= 18
        if y < 50:
            document.showPage()
            y = 800
    document.save()
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="production-audit.pdf"'
    return response


def product_profitability_rows(products, start_date=None, end_date=None):
    from sales.models import DispatchItem, SalesStatus

    rows = []
    for product in products:
        dispatched_items = DispatchItem.objects.filter(
            sales_order_item__product=product,
            dispatch__sales_order__status__in=(
                SalesStatus.PARTIALLY_DISPATCHED,
                SalesStatus.DISPATCHED,
            ),
        ).select_related('sales_order_item')
        if start_date:
            dispatched_items = dispatched_items.filter(
                dispatch__dispatch_date__date__gte=start_date
            )
        if end_date:
            dispatched_items = dispatched_items.filter(
                dispatch__dispatch_date__date__lte=end_date
            )
        dispatched_items = list(dispatched_items.select_related(
            'stock_ledger_entry',
            'sales_order_item',
            'dispatch',
        ))
        dispatched_quantity = sum(
            (item.quantity for item in dispatched_items),
            Decimal('0.0000'),
        ).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
        revenue = sum(
            (
                item.sales_order_item.subtotal * item.quantity
                / item.sales_order_item.quantity
                for item in dispatched_items
                if item.sales_order_item.quantity
            ),
            Decimal('0.00'),
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        dispatch_costs = [
            dispatch_item_cost(item)
            for item in dispatched_items
        ]
        unknown_costs = [
            status for cost, status in dispatch_costs if cost is None
        ]
        if unknown_costs:
            cost = profit = margin = None
            source_status = (
                'Legacy source cost unavailable'
                if 'Legacy source used' in unknown_costs
                else 'Missing source allocation'
            )
        else:
            cost = sum(
                (value for value, _ in dispatch_costs),
                Decimal('0.00'),
            ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            profit = (revenue - cost).quantize(
                Decimal('0.01'),
                rounding=ROUND_HALF_UP,
            )
            margin = (
                (profit / revenue * 100).quantize(
                    Decimal('0.01'),
                    rounding=ROUND_HALF_UP,
                )
                if revenue
                else None
            )
            source_status = (
                'FIFO production sources'
                if dispatch_costs
                else 'No dispatches'
            )
        rows.append({
            'product': product,
            'product_name': product.name,
            'product_code': product.code,
            'dispatched_quantity': dispatched_quantity,
            'net_sales_revenue': revenue,
            'fifo_cogs': cost,
            'cost_available': cost is not None,
            'gross_profit': profit,
            'gross_margin': margin,
            'margin_available': margin is not None,
            'cost_source_status': source_status,
        })
    return rows


def product_profitability_csv_response(products, start_date=None, end_date=None):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="product-profitability.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Product', 'Product Code', 'Dispatched Quantity',
        'Line-item Revenue (ex GST)', 'Dispatch COGS', 'Gross Profit',
        'Gross Contribution', 'Margin %', 'Gross Margin %',
        'Cost Source Status',
    ])
    for row in product_profitability_rows(products, start_date, end_date):
        writer.writerow([
            row['product_name'],
            row['product_code'],
            row['dispatched_quantity'],
            row['net_sales_revenue'],
            row['fifo_cogs'],
            row['gross_profit'],
            row['gross_profit'],
            row['gross_margin'],
            row['gross_margin'],
            row['cost_source_status'],
        ])
    return response


def dispatch_item_cost(dispatch_item):
    entry = dispatch_item.stock_ledger_entry
    if entry is None:
        entry = StockLedgerEntry.objects.filter(
            reference_number=dispatch_item.dispatch.dispatch_number,
            product_id=dispatch_item.sales_order_item.product_id,
            movement_type=StockMovementType.OUT,
        ).first()
    if entry is None:
        return None, 'Missing source allocation'
    allocations = list(entry.source_allocations.select_related('source'))
    if not allocations or sum(
        (allocation.quantity for allocation in allocations),
        Decimal('0.0000'),
    ) != dispatch_item.quantity:
        return None, 'Missing source allocation'
    if any(
        allocation.source.source_type == InventorySourceType.LEGACY
        for allocation in allocations
    ):
        return None, 'Legacy source used'
    if any(allocation.source.unit_cost is None for allocation in allocations):
        return None, 'Missing source allocation'
    return (
        sum(
            (
                allocation.quantity * allocation.source.unit_cost
                for allocation in allocations
            ),
            Decimal('0.00'),
        ),
        'FIFO production sources',
    )


def _usage_fifo_cost(usage):
    from production.models import ProductionDamageType

    entry = StockLedgerEntry.objects.filter(
        raw_material_id=usage.raw_material_id,
        reference_number=usage.production_order.order_number,
        movement_type=StockMovementType.OUT,
    ).first()
    if not entry:
        return None, 'Missing source allocation'
    cost = get_inventory_source_cost(entry)
    if cost is None:
        return None, _entry_source_status(entry)
    source_status = _entry_source_status(entry)
    if source_status == 'Legacy source used':
        return None, source_status
    for damage in usage.production_order.damage_records.filter(
        raw_material_id=usage.raw_material_id,
        damage_type=ProductionDamageType.DAMAGED_POUCH,
        is_voided=False,
        stock_ledger_entry__reference_number=(
            f'{usage.production_order.order_number}-DAMAGE'
        ),
    ).select_related('stock_ledger_entry'):
        damage_cost = get_inventory_source_cost(damage.stock_ledger_entry)
        if damage_cost is None:
            return None, _entry_source_status(damage.stock_ledger_entry)
        damage_status = _entry_source_status(damage.stock_ledger_entry)
        if damage_status == 'Legacy source used':
            return None, damage_status
        cost += damage_cost
        if damage_status != source_status:
            source_status = 'Mixed FIFO sources'
    return cost, source_status


def _entry_source_status(entry):
    allocations = list(entry.source_allocations.select_related('source'))
    if not allocations or sum(
        (allocation.quantity for allocation in allocations),
        Decimal('0.0000'),
    ) != entry.quantity:
        return 'Missing source allocation'
    if any(
        allocation.source.source_type == InventorySourceType.LEGACY
        for allocation in allocations
    ):
        return 'Legacy source used'
    if any(allocation.source.unit_cost is None for allocation in allocations):
        return 'Source cost unavailable'
    return 'FIFO receipt sources'


def _variance_costs(usage):
    actual_cost, basis = _usage_fifo_cost(usage)
    planned_cost = usage.planned_cost
    if actual_cost is None:
        if usage.planned_unit_rate is None:
            basis = f'{basis}; missing planned cost baseline'
        return (
            usage.planned_unit_rate if usage.planned_unit_rate is not None else '',
            planned_cost if planned_cost is not None else '',
            '',
            '',
            '',
            basis,
        )
    actual_cost = actual_cost.quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )
    if planned_cost is None:
        actual_unit_rate = (
            actual_cost / usage.actual_consumed_quantity
            if usage.actual_consumed_quantity > Decimal('0.0000')
            else Decimal('0.0000')
        ).quantize(Decimal('0.0001'))
        return (
            '',
            '',
            actual_unit_rate,
            actual_cost,
            '',
            f'Missing planned cost baseline; {basis}',
        )
    cost_variance = (actual_cost - planned_cost).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )
    actual_unit_rate = (
        actual_cost / usage.actual_consumed_quantity
        if usage.actual_consumed_quantity > Decimal('0.0000')
        else Decimal('0.0000')
    ).quantize(Decimal('0.0001'))
    return (
        usage.planned_unit_rate,
        planned_cost,
        actual_unit_rate,
        actual_cost,
        cost_variance,
        basis,
    )


def production_cost_variance_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="production-cost-variance.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Order', 'Material', 'Planned Quantity', 'Actual Quantity',
        'Quantity Variance', 'Planned Unit Cost Snapshot', 'Planned Cost',
        'Actual FIFO Unit Cost', 'Actual FIFO Cost', 'Cost Variance',
        'Source Cost Basis',
    ])
    for order in queryset:
        for usage in order.material_usages.select_related('raw_material'):
            planned_rate, planned_cost, actual_rate, actual_cost, cost_variance, basis = (
                _variance_costs(usage)
            )
            writer.writerow([
                order.order_number,
                usage.raw_material.name,
                usage.planned_quantity,
                usage.actual_consumed_quantity,
                usage.variance_quantity,
                planned_rate,
                planned_cost,
                actual_rate,
                actual_cost,
                cost_variance,
                basis,
            ])
    return response


def production_material_variance_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="production-material-variance.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Order', 'Product', 'Material', 'Unit', 'Planned Quantity',
        'Actual Quantity', 'Quantity Variance',
        'Planned Unit Cost Snapshot', 'Planned Cost',
        'Actual FIFO Unit Cost', 'Actual FIFO Cost', 'Cost Variance',
        'Source Cost Basis',
    ])
    for order in queryset:
        for usage in order.material_usages.select_related('raw_material'):
            planned_rate, planned_cost, actual_rate, actual_cost, cost_variance, basis = (
                _variance_costs(usage)
            )
            writer.writerow([
                order.order_number,
                order.product.name,
                usage.raw_material.name,
                usage.unit,
                usage.planned_quantity,
                usage.actual_consumed_quantity,
                usage.variance_quantity,
                planned_rate,
                planned_cost,
                actual_rate,
                actual_cost,
                cost_variance,
                basis,
            ])
    return response


def production_damage_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="production-damage-scrap.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Date', 'Order', 'Type', 'Material', 'Product', 'Quantity',
        'Unit', 'Reason', 'Recorded By', 'Status',
    ])
    for record in queryset:
        writer.writerow([
            record.recorded_at,
            record.production_order.order_number,
            record.get_damage_type_display(),
            record.raw_material.name if record.raw_material else '',
            record.product.name if record.product else '',
            record.quantity,
            record.unit,
            record.reason,
            record.recorded_by.username if record.recorded_by else '',
            'Voided' if record.is_voided else 'Active',
        ])
    return response


def production_approval_history_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="production-approval-history.csv"'
    writer = csv.writer(response)
    writer.writerow(['Order', 'Status', 'Action by', 'Timestamp', 'Notes'])
    for order in queryset:
        for event in order.approval_history.select_related('action_by'):
            writer.writerow([
                order.order_number,
                event.get_approval_status_display(),
                event.action_by.username if event.action_by else '',
                event.created_at,
                event.notes,
            ])
    return response


def sales_order_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="sales-orders.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Order', 'Order Date', 'Customer', 'Product', 'Status', 'Approval',
        'Quantity', 'Unit Rate', 'Discount', 'Line Subtotal', 'Line Tax', 'Line Total',
    ])
    for order in queryset:
        for item in order.items.select_related('product'):
            writer.writerow([
                order.order_number,
                order.order_date,
                order.customer.name,
                item.product.name,
                order.get_status_display(),
                order.get_approval_status_display(),
                item.quantity,
                item.unit_price,
                item.discount_amount,
                item.subtotal,
                item.tax_amount,
                item.total_price,
            ])
    return response


def sales_approval_history_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="sales-approval-history.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Order', 'Customer', 'Approval Status', 'Action By', 'Timestamp', 'Notes',
    ])
    for order in queryset:
        events = order.approval_history.select_related('action_by').order_by('created_at')
        if not events.exists():
            writer.writerow([
                order.order_number,
                order.customer.name,
                f'{order.get_approval_status_display()} (legacy status; no event history)',
                '',
                '',
                '',
            ])
        for event in events:
            writer.writerow([
                order.order_number,
                order.customer.name,
                event.get_approval_status_display(),
                event.action_by.username if event.action_by else '',
                event.created_at,
                event.notes,
            ])
    return response


def dispatch_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="dispatch-register.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Dispatch', 'Dispatch Date', 'Order', 'Customer', 'Product',
        'Quantity', 'Vehicle', 'Dispatched By',
    ])
    for dispatch in queryset:
        for item in dispatch.items.select_related(
            'sales_order_item__product',
            'sales_order_item__sales_order__customer',
        ):
            order_item = item.sales_order_item
            writer.writerow([
                dispatch.dispatch_number,
                dispatch.dispatch_date,
                dispatch.sales_order.order_number,
                dispatch.sales_order.customer.name,
                order_item.product.name,
                item.quantity,
                dispatch.vehicle_reference,
                dispatch.dispatched_by.username if dispatch.dispatched_by else '',
            ])
    return response


def invoice_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="sales-invoice-register.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Invoice', 'Issue Date', 'Order', 'Customer', 'Subtotal', 'Tax',
        'Grand Total', 'Paid', 'Outstanding', 'Payment Status',
    ])
    for invoice in queryset:
        writer.writerow([
            invoice.invoice_number,
            invoice.issue_date,
            invoice.sales_order.order_number,
            invoice.sales_order.customer.name,
            invoice.subtotal,
            invoice.tax_amount,
            invoice.grand_total,
            invoice.amount_paid,
            invoice.outstanding_amount,
            invoice.get_payment_status_display(),
        ])
    return response


def payment_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="payment-reversal-register.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Payment Date', 'Payment ID', 'Invoice', 'Customer', 'Amount', 'Method',
        'Reference', 'Status', 'Reversal Amount', 'Reversal Reason', 'Reversed By',
    ])
    for payment in queryset:
        reversal = getattr(payment, 'reversal', None)
        writer.writerow([
            payment.payment_date,
            payment.pk,
            payment.invoice.invoice_number,
            payment.invoice.sales_order.customer.name,
            payment.amount,
            payment.method,
            payment.reference,
            'Reversed' if reversal else 'Active',
            reversal.amount if reversal else '',
            reversal.reason if reversal else '',
            reversal.reversed_by.username if reversal and reversal.reversed_by else '',
        ])
    return response


def outstanding_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="outstanding-invoices.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Invoice', 'Issue Date', 'Order', 'Customer', 'Grand Total',
        'Paid', 'Outstanding', 'Payment Status',
    ])
    for invoice in queryset:
        if invoice.outstanding_amount <= Decimal('0.00'):
            continue
        writer.writerow([
            invoice.invoice_number,
            invoice.issue_date,
            invoice.sales_order.order_number,
            invoice.sales_order.customer.name,
            invoice.grand_total,
            invoice.amount_paid,
            invoice.outstanding_amount,
            invoice.get_payment_status_display(),
        ])
    return response


def inventory_ledger_csv_response(queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="inventory-ledger-audit.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Date', 'Movement', 'Item', 'Quantity', 'Unit', 'Reference', 'Source',
        'Allocated Quantity', 'Source Unit Cost', 'Reversed Entry',
    ])
    for entry in queryset:
        item = entry.stock_item
        allocations = list(entry.source_allocations.select_related('source'))
        allocated_quantity = sum(
            (allocation.quantity for allocation in allocations),
            Decimal('0.0000'),
        )
        requires_allocation = (
            entry.raw_material_id
            and entry.movement_type in (
                StockMovementType.OUT,
                StockMovementType.REVERSAL,
            )
        )
        if requires_allocation and allocated_quantity != entry.quantity:
            writer.writerow([
                entry.movement_date,
                entry.get_movement_type_display(),
                item.name,
                entry.quantity,
                entry.unit,
                entry.reference_number,
                'Missing source allocation',
                allocated_quantity,
                '',
                entry.reversed_entry_id or '',
            ])
            continue
        if allocations:
            for allocation in allocations:
                writer.writerow([
                    entry.movement_date,
                    entry.get_movement_type_display(),
                    item.name,
                    entry.quantity,
                    entry.unit,
                    entry.reference_number,
                    allocation.source.source_type,
                    allocation.quantity,
                    allocation.source.unit_cost,
                    entry.reversed_entry_id or '',
                ])
        else:
            writer.writerow([
                entry.movement_date,
                entry.get_movement_type_display(),
                item.name,
                entry.quantity,
                entry.unit,
                entry.reference_number,
                'Not applicable',
                '',
                '',
                entry.reversed_entry_id or '',
            ])
    return response
