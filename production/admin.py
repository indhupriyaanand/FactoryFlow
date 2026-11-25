from django.contrib import admin
from django.utils.html import format_html
from .models import (
    ProductionDamageRecord,
    ProductionMaterialUsage,
    ProductionOrder,
    ProductionStatus,
)


class ProductionMaterialUsageInline(admin.TabularInline):
    model = ProductionMaterialUsage
    extra = 0
    fields = (
        'raw_material',
        'planned_quantity',
        'planned_unit_rate',
        'actual_quantity',
        'unit',
    )
    readonly_fields = (
        'raw_material',
        'planned_quantity',
        'planned_unit_rate',
        'actual_quantity',
        'unit',
    )
    can_delete = False


@admin.register(ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_number',
        'product',
        'target_quantity',
        'produced_quantity',
        'status_badge',
        'start_date',
        'completion_date',
        'created_by'
    )
    list_filter = ('status', 'product', 'start_date')
    search_fields = ('order_number', 'product__name', 'notes')
    inlines = [ProductionMaterialUsageInline]
    ordering = ('-start_date', '-created_at')

    def status_badge(self, obj):
        colors = {
            ProductionStatus.DRAFT: 'secondary',
            ProductionStatus.PLANNED: 'info text-dark',
            ProductionStatus.IN_PROGRESS: 'warning text-dark',
            ProductionStatus.COMPLETED: 'success',
            ProductionStatus.CANCELLED: 'danger',
        }
        color = colors.get(obj.status, 'secondary')
        return format_html(
            '<span class="badge bg-{}" style="padding: 4px 8px; font-size: 0.85rem;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = "Status"


@admin.register(ProductionMaterialUsage)
class ProductionMaterialUsageAdmin(admin.ModelAdmin):
    list_display = (
        'production_order',
        'raw_material',
        'planned_quantity',
        'planned_unit_rate',
        'actual_quantity',
        'unit',
    )
    list_filter = ('raw_material', 'unit')
    search_fields = ('production_order__order_number', 'raw_material__name')


@admin.register(ProductionDamageRecord)
class ProductionDamageRecordAdmin(admin.ModelAdmin):
    list_display = (
        'recorded_at',
        'production_order',
        'damage_type',
        'raw_material',
        'product',
        'quantity',
        'unit',
        'is_voided',
        'recorded_by',
    )
    list_filter = ('damage_type', 'is_voided', 'recorded_at')
    search_fields = ('production_order__order_number', 'raw_material__name', 'product__name', 'reason')
    readonly_fields = ('stock_ledger_entry', 'recorded_by', 'voided_at', 'voided_by')
