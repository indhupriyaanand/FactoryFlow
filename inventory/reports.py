import csv

from django.http import HttpResponse

from .services import get_all_product_stock_balances, get_all_raw_material_stock_balances


def inventory_valuation_csv_response(
    start_date=None,
    end_date=None,
    material_id=None,
    product_id=None,
):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="inventory-valuation.csv"'
    writer = csv.writer(response)
    writer.writerow(['Type', 'Item', 'Quantity', 'Period Net', 'Unit', 'Rate', 'Value'])
    for row in get_all_raw_material_stock_balances():
        if material_id and row['raw_material'].pk != material_id:
            continue
        entries = row['raw_material'].stock_ledger_entries.all()
        if start_date:
            entries = entries.filter(movement_date__date__gte=start_date)
        if end_date:
            entries = entries.filter(movement_date__date__lte=end_date)
        period_net = sum((entry.net_quantity for entry in entries), 0)
        writer.writerow([
            'Raw material',
            row['raw_material'].name,
            row['balance'],
            period_net,
            row['unit'],
            row['weighted_average_rate'],
            row['stock_value'],
        ])
    for row in get_all_product_stock_balances():
        if product_id and row['product'].pk != product_id:
            continue
        entries = row['product'].stock_ledger_entries.all()
        if start_date:
            entries = entries.filter(movement_date__date__gte=start_date)
        if end_date:
            entries = entries.filter(movement_date__date__lte=end_date)
        period_net = sum((entry.net_quantity for entry in entries), 0)
        writer.writerow([
            'Finished product',
            row['product'].name,
            row['balance'],
            period_net,
            row['unit'],
            row['unit_price'],
            row['stock_value'],
        ])
    return response
