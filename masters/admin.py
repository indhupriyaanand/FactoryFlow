from django.contrib import admin
from django.utils.html import format_html
from .models import Product, RawMaterial, Customer, Supplier, BillOfMaterials

class BillOfMaterialsInline(admin.TabularInline):
    model = BillOfMaterials
    extra = 1
    fields = ('raw_material', 'quantity', 'unit', 'batch_size', 'is_active')
    show_change_link = True

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'packet_size', 'gst_rate_display', 'unit_price_display', 'bom_item_count', 'is_active')
    list_filter = ('is_active', 'gst_rate')
    search_fields = ('name', 'code', 'packet_size')
    inlines = [BillOfMaterialsInline]
    ordering = ('name',)

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


@admin.register(RawMaterial)
class RawMaterialAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'unit_badge', 'standard_rate_display', 'is_active', 'created_at')
    list_filter = ('unit', 'is_active')
    search_fields = ('name', 'code')
    ordering = ('name',)

    def unit_badge(self, obj):
        return format_html('<span class="badge bg-info text-dark">{}</span>', obj.get_unit_display())
    unit_badge.short_description = "UOM"

    def standard_rate_display(self, obj):
        return f"₹{obj.standard_rate} / {obj.unit}"
    standard_rate_display.short_description = "Standard Rate"


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_number', 'state_badge', 'gstin_display', 'is_active')
    list_filter = ('state', 'is_active')
    search_fields = ('name', 'contact_number', 'address', 'gstin')
    ordering = ('name',)

    def state_badge(self, obj):
        color = 'primary' if obj.state.lower() == 'tamil nadu' else 'warning text-dark'
        return format_html('<span class="badge bg-{}">{}</span>', color, obj.state)
    state_badge.short_description = "State"

    def gstin_display(self, obj):
        if obj.gstin:
            return format_html('<code>{}</code>', obj.gstin)
        return format_html('<span class="text-muted">Unregistered</span>')
    gstin_display.short_description = "GSTIN"


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_number', 'state', 'gstin_display', 'is_active')
    list_filter = ('state', 'is_active')
    search_fields = ('name', 'contact_number', 'address', 'gstin')
    ordering = ('name',)

    def gstin_display(self, obj):
        if obj.gstin:
            return format_html('<code>{}</code>', obj.gstin)
        return format_html('<span class="text-muted">Unregistered</span>')
    gstin_display.short_description = "GSTIN"


@admin.register(BillOfMaterials)
class BillOfMaterialsAdmin(admin.ModelAdmin):
    list_display = ('product', 'raw_material', 'quantity', 'unit', 'batch_size', 'is_active')
    list_filter = ('product', 'unit', 'is_active')
    search_fields = ('product__name', 'product__code', 'raw_material__name', 'raw_material__code')
    ordering = ('product', 'raw_material')
