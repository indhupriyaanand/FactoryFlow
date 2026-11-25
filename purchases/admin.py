from django.contrib import admin
from django.utils.html import format_html
from .models import PurchaseInvoice, PurchaseItem, PurchaseStatus


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1
    fields = ('raw_material', 'quantity', 'unit', 'unit_price', 'total_price')
    readonly_fields = ('total_price',)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status in [PurchaseStatus.RECEIVED, PurchaseStatus.CANCELLED]:
            return ('raw_material', 'quantity', 'unit', 'unit_price', 'total_price')
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in [PurchaseStatus.RECEIVED, PurchaseStatus.CANCELLED]:
            return False
        return super().has_delete_permission(request, obj)

    def has_add_permission(self, request, obj=None):
        if obj and obj.status in [PurchaseStatus.RECEIVED, PurchaseStatus.CANCELLED]:
            return False
        return super().has_add_permission(request, obj)


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'invoice_number',
        'supplier',
        'purchase_date',
        'status_badge',
        'item_count',
        'total_amount_display',
        'created_by',
        'created_at'
    )
    list_filter = ('status', 'purchase_date', 'supplier')
    search_fields = ('invoice_number', 'supplier__name', 'notes')
    inlines = [PurchaseItemInline]
    ordering = ('-purchase_date', '-created_at')
    list_per_page = 20

    def get_readonly_fields(self, request, obj=None):
        """
        - For DRAFT / New invoices: status and total_amount_display are read-only.
          (Status cannot be manually switched to RECEIVED in the form).
        - For RECEIVED / CANCELLED invoices: all core header fields are locked.
        """
        if obj and obj.status in [PurchaseStatus.RECEIVED, PurchaseStatus.CANCELLED]:
            return (
                'invoice_number',
                'supplier',
                'purchase_date',
                'status',
                'total_amount_display',
                'created_at',
                'updated_at'
            )
        return ('status', 'total_amount_display', 'created_at', 'updated_at')

    def has_delete_permission(self, request, obj=None):
        """
        Prevent deleting invoices that have been received or cancelled.
        """
        if obj and obj.status in [PurchaseStatus.RECEIVED, PurchaseStatus.CANCELLED]:
            return False
        return super().has_delete_permission(request, obj)

    def status_badge(self, obj):
        colors = {
            PurchaseStatus.DRAFT: 'warning text-dark',
            PurchaseStatus.RECEIVED: 'success',
            PurchaseStatus.CANCELLED: 'danger',
        }
        color = colors.get(obj.status, 'secondary')
        return format_html(
            '<span class="badge bg-{}" style="padding: 4px 8px; font-size: 0.85rem;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = "Status"

    def total_amount_display(self, obj):
        return f"₹{obj.total_amount:,.2f}"
    total_amount_display.short_description = "Total Amount"

    def item_count(self, obj):
        count = obj.items.count()
        return format_html('<span class="badge bg-secondary">{} items</span>', count)
    item_count.short_description = "Line Items"

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(PurchaseItem)
class PurchaseItemAdmin(admin.ModelAdmin):
    list_display = (
        'purchase',
        'raw_material',
        'quantity',
        'unit',
        'unit_price_display',
        'total_price_display',
        'created_at'
    )
    list_filter = ('purchase__status', 'raw_material', 'unit')
    search_fields = ('purchase__invoice_number', 'raw_material__name', 'raw_material__code')
    readonly_fields = ('total_price',)
    ordering = ('-created_at',)
    list_per_page = 20

    def unit_price_display(self, obj):
        return f"₹{obj.unit_price} / {obj.unit}"
    unit_price_display.short_description = "Unit Rate"

    def total_price_display(self, obj):
        return f"₹{obj.total_price:,.2f}"
    total_price_display.short_description = "Line Total"
