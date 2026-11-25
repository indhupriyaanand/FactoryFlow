from django.contrib import admin
from django.utils.html import format_html
from .models import StockLedgerEntry, StockMovementType


@admin.register(StockLedgerEntry)
class StockLedgerEntryAdmin(admin.ModelAdmin):
    """
    Read-only Django Admin configuration for StockLedgerEntry.
    Enforces strict append-only audit trail rules:
    - Manual creation via admin form is disabled (has_add_permission = False).
    - Modification of existing records is disabled (has_change_permission = False).
    - Deletion of records is disabled (has_delete_permission = False).
    - Bulk admin actions (including bulk delete) are disabled.
    """
    list_display = (
        'id',
        'movement_date',
        'reference_number',
        'movement_type_badge',
        'item_category_display',
        'stock_item_display',
        'quantity_display',
        'created_by',
        'created_at'
    )
    list_filter = (
        'movement_type',
        'movement_date',
        'unit',
        'raw_material',
        'product'
    )
    search_fields = (
        'reference_number',
        'raw_material__name',
        'raw_material__code',
        'product__name',
        'product__code',
        'description'
    )
    date_hierarchy = 'movement_date'
    ordering = ('-movement_date', '-created_at', '-id')
    list_per_page = 25
    actions = None  # Disables bulk delete and other batch admin actions

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def movement_type_badge(self, obj):
        colors = {
            StockMovementType.IN: 'success',
            StockMovementType.OUT: 'warning text-dark',
            StockMovementType.REVERSAL: 'danger',
        }
        color = colors.get(obj.movement_type, 'secondary')
        return format_html(
            '<span class="badge bg-{}" style="padding: 4px 8px; font-size: 0.85rem;">{}</span>',
            color,
            obj.get_movement_type_display()
        )
    movement_type_badge.short_description = "Movement"

    def item_category_display(self, obj):
        if obj.raw_material:
            return format_html('<span class="badge bg-info text-dark">Raw Material</span>')
        return format_html('<span class="badge bg-primary">Finished Product</span>')
    item_category_display.short_description = "Category"

    def stock_item_display(self, obj):
        if obj.raw_material:
            return f"{obj.raw_material.name} ({obj.raw_material.code})"
        elif obj.product:
            return f"{obj.product.name} ({obj.product.code})"
        return "Unknown"
    stock_item_display.short_description = "Stock Item"

    def quantity_display(self, obj):
        return f"{obj.quantity:.4f} {obj.unit}"
    quantity_display.short_description = "Quantity"
