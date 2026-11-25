"""
URL configuration for FactoryFlow project.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from core.health import health
from core.views import (
    home,
    inventory_valuation_csv,
    product_profitability_csv,
    product_profitability_report,
    production_csv,
    production_excel,
    production_pdf,
    production_variance_csv,
    production_cost_variance_csv,
    production_damage_csv,
    production_material_variance_csv,
    production_approval_history_csv,
    sales_orders_csv,
    sales_approval_audit_csv,
    dispatch_register_csv,
    sales_invoice_register_csv,
    payment_register_csv,
    outstanding_invoices_csv,
    inventory_ledger_audit_csv,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home, name='home'),
    path('health/', health, name='health'),
    path('reports/production.csv', production_csv, name='production_csv'),
    path('reports/production.xlsx', production_excel, name='production_excel'),
    path('reports/production.pdf', production_pdf, name='production_pdf'),
    path('reports/inventory-valuation.csv', inventory_valuation_csv, name='inventory_valuation_csv'),
    path('reports/production-variance.csv', production_variance_csv, name='production_variance_csv'),
    path('reports/production-cost-variance.csv', production_cost_variance_csv, name='production_cost_variance_csv'),
    path('reports/production-material-variance.csv', production_material_variance_csv, name='production_material_variance_csv'),
    path('reports/production-damage.csv', production_damage_csv, name='production_damage_csv'),
    path('reports/product-profitability.csv', product_profitability_csv, name='product_profitability_csv'),
    path('reports/product-profitability/', product_profitability_report, name='product_profitability_report'),
    path('reports/production-approval-history.csv', production_approval_history_csv, name='production_approval_history_csv'),
    path('reports/sales-orders.csv', sales_orders_csv, name='sales_orders_csv'),
    path('reports/sales-approvals.csv', sales_approval_audit_csv, name='sales_approval_audit_csv'),
    path('reports/dispatches.csv', dispatch_register_csv, name='dispatch_register_csv'),
    path('reports/invoices.csv', sales_invoice_register_csv, name='sales_invoice_register_csv'),
    path('reports/payments.csv', payment_register_csv, name='payment_register_csv'),
    path('reports/outstanding.csv', outstanding_invoices_csv, name='outstanding_invoices_csv'),
    path('reports/inventory-ledger.csv', inventory_ledger_audit_csv, name='inventory_ledger_audit_csv'),
    path('accounts/', include('accounts.urls')),
    path('masters/', include('masters.urls')),
    path('purchases/', include('purchases.urls')),
    path('inventory/', include('inventory.urls')),
    path('production/', include('production.urls')),
    path('sales/', include('sales.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
