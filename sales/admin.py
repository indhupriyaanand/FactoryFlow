from django.contrib import admin
from .models import SalesApprovalHistory


@admin.register(SalesApprovalHistory)
class SalesApprovalHistoryAdmin(admin.ModelAdmin):
    list_display = ('sales_order', 'approval_status', 'action_by', 'created_at')
    list_filter = ('approval_status', 'created_at')
    search_fields = ('sales_order__order_number', 'sales_order__customer__name')
    readonly_fields = ('sales_order', 'approval_status', 'action_by', 'notes', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
