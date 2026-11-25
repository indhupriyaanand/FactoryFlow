from django.shortcuts import render
from accounts.access import active_user_required
from .services import get_all_product_stock_balances, get_all_raw_material_stock_balances
from .models import StockLedgerEntry


@active_user_required
def inventory_summary(request):
    return render(request, "inventory/summary.html", {
        "raw_materials": get_all_raw_material_stock_balances(),
        "products": get_all_product_stock_balances(),
        "movements": StockLedgerEntry.objects.select_related("raw_material", "product")[:100],
    })
