from django.contrib import admin
from django.utils.html import format_html
from .models import Product, RawMaterial, Customer, Supplier, BillOfMaterials


def format_bom_per_packet(obj):
    """
    Safely formats the BOM per-packet quantity to 4 decimal places.
    Avoids passing numeric format specifiers like '{:.4f}' into format_html()
    because format_html() escapes arguments into SafeString, which causes
    'ValueError: Unknown format code f for object of type SafeString'.
    """
    if not obj:
        return '-'
    
    qty = getattr(obj, 'quantity_per_packet', None)
    if qty is None and hasattr(obj, 'quantity') and hasattr(obj, 'batch_size') and obj.batch_size:
        try:
            qty = obj.quantity / obj.batch_size
        except Exception:
            qty = None

    if qty is None:
        formatted_qty = "0.0000"
    else:
        try:
            formatted_qty = f"{qty:.4f}"
        except (ValueError, TypeError):
            try:
                formatted_qty = f"{float(qty):.4f}"
            except (ValueError, TypeError):
                formatted_qty = str(qty)

    unit = getattr(obj, 'unit', '')
    return format_html('<strong>{}</strong> {} / pkt', formatted_qty, unit)


class BillOfMaterialsInline(admin.TabularInline):
    model = BillOfMaterials
    extra = 0
    fields = ('raw_material', 'quantity', 'unit', 'batch_size', 'quantity_per_packet_display', 'is_active')
    readonly_fields = ('quantity_per_packet_display',)
    show_change_link = True

    def quantity_per_packet_display(self, obj):
        if obj and obj.pk:
            return format_bom_per_packet(obj)
        return '-'
    quantity_per_packet_display.short_description = "Per Packet Qty"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'packet_size', 'gst_rate_display', 'unit_price_display', 'bom_item_count', 'status_badge')
    list_filter = ('is_active', 'gst_rate')
    search_fields = ('name', 'code', 'packet_size')
    inlines = [BillOfMaterialsInline]
    ordering = ('name',)
    list_per_page = 20

    def gst_rate_display(self, obj):
        return f"{obj.gst_rate}%"
    gst_rate_display.short_description = "GST Rate"

    def unit_price_display(self, obj):
        return f"₹{obj.unit_price}"
    unit_price_display.short_description = "Unit Price"

    def bom_item_count(self, obj):
        count = obj.bom_items.count()
        return format_html('<span class="badge bg-secondary">{} ingredients</span>', count)
    bom_item_count.short_description = "BOM Recipe"

    def status_badge(self, obj):
        if obj.is_active:
            return format_html('<span class="badge bg-success" style="padding: 4px 8px;">Active</span>')
        return format_html('<span class="badge bg-danger" style="padding: 4px 8px;">Inactive</span>')
    status_badge.short_description = "Status"


@admin.register(RawMaterial)
class RawMaterialAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'unit_badge', 'standard_rate_display', 'bom_usage_count', 'status_badge')
    list_filter = ('unit', 'is_active')
    search_fields = ('name', 'code')
    ordering = ('name',)
    list_per_page = 20

    def unit_badge(self, obj):
        return format_html('<span class="badge bg-info text-dark">{}</span>', obj.get_unit_display())
    unit_badge.short_description = "UOM"

    def standard_rate_display(self, obj):
        return f"₹{obj.standard_rate} / {obj.unit}"
    standard_rate_display.short_description = "Standard Rate"

    def bom_usage_count(self, obj):
        count = obj.bom_usages.count()
        return format_html('<span class="badge bg-secondary">{} products</span>', count)
    bom_usage_count.short_description = "BOM Usages"

    def status_badge(self, obj):
        if obj.is_active:
            return format_html('<span class="badge bg-success" style="padding: 4px 8px;">Active</span>')
        return format_html('<span class="badge bg-danger" style="padding: 4px 8px;">Inactive</span>')
    status_badge.short_description = "Status"


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_number', 'state_badge', 'state_code_display', 'gstin_display', 'status_badge')
    list_filter = ('state', 'is_active')
    search_fields = ('name', 'contact_number', 'address', 'gstin', 'state')
    ordering = ('name',)
    list_per_page = 20

    def state_badge(self, obj):
        color = 'primary' if obj.state.lower() == 'tamil nadu' else 'warning text-dark'
        return format_html('<span class="badge bg-{}">{}</span>', color, obj.state)
    state_badge.short_description = "State"

    def state_code_display(self, obj):
        return obj.state_code or '-'
    state_code_display.short_description = "State Code"

    def gstin_display(self, obj):
        if obj.gstin:
            return format_html('<code>{}</code>', obj.gstin)
        return format_html('<span class="text-muted">Unregistered</span>')
    gstin_display.short_description = "GSTIN"

    def status_badge(self, obj):
        if obj.is_active:
            return format_html('<span class="badge bg-success" style="padding: 4px 8px;">Active</span>')
        return format_html('<span class="badge bg-danger" style="padding: 4px 8px;">Inactive</span>')
    status_badge.short_description = "Status"


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_number', 'state', 'state_code_display', 'gstin_display', 'status_badge')
    list_filter = ('state', 'is_active')
    search_fields = ('name', 'contact_number', 'address', 'gstin', 'state')
    ordering = ('name',)
    list_per_page = 20

    def state_code_display(self, obj):
        return obj.state_code or '-'
    state_code_display.short_description = "State Code"

    def gstin_display(self, obj):
        if obj.gstin:
            return format_html('<code>{}</code>', obj.gstin)
        return format_html('<span class="text-muted">Unregistered</span>')
    gstin_display.short_description = "GSTIN"

    def status_badge(self, obj):
        if obj.is_active:
            return format_html('<span class="badge bg-success" style="padding: 4px 8px;">Active</span>')
        return format_html('<span class="badge bg-danger" style="padding: 4px 8px;">Inactive</span>')
    status_badge.short_description = "Status"


@admin.register(BillOfMaterials)
class BillOfMaterialsAdmin(admin.ModelAdmin):
    list_display = ('product', 'raw_material', 'batch_quantity_display', 'batch_size_display', 'per_packet_display', 'status_badge')
    list_filter = ('product', 'unit', 'is_active')
    search_fields = ('product__name', 'product__code', 'raw_material__name', 'raw_material__code')
    ordering = ('product', 'raw_material')
    list_per_page = 20

    def batch_quantity_display(self, obj):
        return f"{obj.quantity} {obj.unit}"
    batch_quantity_display.short_description = "Batch Qty"

    def batch_size_display(self, obj):
        return f"{obj.batch_size} pkts"
    batch_size_display.short_description = "Batch Size"

    def per_packet_display(self, obj):
        return format_bom_per_packet(obj)
    per_packet_display.short_description = "Per Packet"

    def status_badge(self, obj):
        if obj.is_active:
            return format_html('<span class="badge bg-success" style="padding: 4px 8px;">Active</span>')
        return format_html('<span class="badge bg-danger" style="padding: 4px 8px;">Inactive</span>')
    status_badge.short_description = "Status"
