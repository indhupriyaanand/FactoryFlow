from decimal import Decimal
from typing import Iterable, Optional, Union

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.models import StockLedgerEntry, StockMovementType
from inventory.services import (
    create_reversal_ledger_entry,
    create_stock_ledger_entry,
    get_product_stock_balance,
)
from masters.models import Customer, Product
from accounts.access import MANAGEMENT_ROLES, SALES_ROLES, require_role
from sales.models import (
    DispatchRecord,
    DispatchItem,
    SalesApprovalHistory,
    SalesApprovalStatus,
    SalesOrder,
    SalesOrderItem,
    SalesShortage,
    SalesStatus,
)


class _DispatchShortage(Exception):
    """Abort the transaction while preserving the informational shortage record."""


def create_sales_order(
    customer: Union[Customer, int],
    items: Iterable[dict],
    user: Optional[User] = None,
    order_number: Optional[str] = None,
    notes: str = "",
) -> SalesOrder:
    """Create a draft sales order and calculate totals from its line items."""
    require_role(user, *SALES_ROLES)
    if isinstance(customer, int):
        customer = Customer.objects.get(pk=customer)
    if not customer.pk:
        raise ValidationError("Customer must be saved before creating a sales order.")
    item_data = list(items)
    if not item_data:
        raise ValidationError("A sales order must contain at least one item.")
    product_ids = [data.get("product").pk for data in item_data if data.get("product")]
    if len(product_ids) != len(set(product_ids)):
        raise ValidationError("Each product may appear only once in a sales order.")

    with transaction.atomic():
        order = SalesOrder(
            order_number=(order_number or _next_order_number()).strip().upper(),
            customer=customer,
            created_by=user,
            notes=notes.strip() if notes else "",
        )
        order.full_clean()
        order.save()
        for data in item_data:
            product = data.get("product")
            if isinstance(product, int):
                product = Product.objects.get(pk=product)
            quantity = data.get("quantity")
            if isinstance(quantity, float):
                raise ValidationError("Sales quantities must use Decimal.")
            item = SalesOrderItem(
                sales_order=order,
                product=product,
                quantity=quantity,
                unit_price=data.get("unit_price", product.unit_price),
                gst_rate=data.get("gst_rate", product.gst_rate),
                discount_amount=data.get("discount_amount", Decimal('0.00')),
            )
            item.full_clean()
            item.save()
        SalesApprovalHistory.objects.create(
            sales_order=order,
            approval_status=SalesApprovalStatus.PENDING,
            action_by=user,
            notes='Order created; approval required.',
        )
        order.refresh_from_db()
        return order


def update_sales_order(
    order_id: int,
    customer: Customer,
    items: Iterable[dict],
    user: Optional[User] = None,
    order_date=None,
    notes: str = "",
) -> SalesOrder:
    require_role(user, *SALES_ROLES)
    with transaction.atomic():
        order = SalesOrder.objects.select_for_update().get(pk=order_id)
        if order.status != SalesStatus.DRAFT:
            raise ValidationError("Only draft sales orders can be edited.")
        if order.approval_status == SalesApprovalStatus.APPROVED:
            raise ValidationError("Approved sales orders cannot be edited.")
        if (
            hasattr(order, 'invoice')
            or order.dispatch_records.exists()
            or order.items.filter(dispatched_quantity__gt=0).exists()
        ):
            raise ValidationError(
                "Sales orders with dispatches or invoices cannot be edited."
            )

        submitted_items = list(items)
        item_data = [
            data for data in submitted_items
            if not data.get('DELETE') and data.get('product')
        ]
        if not item_data:
            raise ValidationError("A sales order must contain at least one item.")
        product_ids = [data['product'].pk for data in item_data]
        if len(product_ids) != len(set(product_ids)):
            raise ValidationError("Each product may appear only once in a sales order.")

        existing_ids = set(order.items.values_list('pk', flat=True))
        submitted_ids = {
            data['id'].pk for data in submitted_items
            if data.get('id') is not None
        }
        if submitted_ids != existing_ids:
            raise ValidationError("Sales order items changed; reload and try again.")

        was_rejected = order.approval_status == SalesApprovalStatus.REJECTED
        order.customer = customer
        order.order_date = order_date or order.order_date
        order.notes = notes.strip()
        if was_rejected:
            order.approval_status = SalesApprovalStatus.PENDING
        order.save()

        # Draft orders have no dispatch or invoice references, so replacing their
        # lines atomically avoids stale quantities or transient product conflicts.
        order.items.all().delete()
        for data in item_data:
            item = SalesOrderItem(
                sales_order=order,
                product=data['product'],
                quantity=data['quantity'],
                unit_price=data['unit_price'],
                discount_amount=data.get('discount_amount', Decimal('0.00')),
                gst_rate=data['gst_rate'],
            )
            item.full_clean()
            item.save()
        if was_rejected:
            SalesApprovalHistory.objects.create(
                sales_order=order,
                approval_status=SalesApprovalStatus.PENDING,
                action_by=user,
                notes='Order updated and resubmitted after rejection.',
            )
        return order.refresh_from_db() or order


def confirm_sales_order(order_id: int, user: Optional[User] = None) -> SalesOrder:
    require_role(user, *MANAGEMENT_ROLES)
    with transaction.atomic():
        order = _get_order(order_id)
        if order.status != SalesStatus.DRAFT:
            raise ValidationError("Only draft sales orders can be confirmed.")
        if not order.items.exists():
            raise ValidationError("A sales order must contain at least one item.")
        if order.approval_status != SalesApprovalStatus.APPROVED:
            raise ValidationError("Sales order approval is required before confirmation.")
        order.status = SalesStatus.CONFIRMED
        order.save()
        return order


def approve_sales_order(order_id: int, user: User) -> SalesOrder:
    require_role(user, *MANAGEMENT_ROLES)
    with transaction.atomic():
        order = _get_order(order_id)
        if order.status != SalesStatus.DRAFT:
            raise ValidationError("Only draft sales orders can be approved.")
        if order.approval_status != SalesApprovalStatus.PENDING:
            raise ValidationError("Only pending sales orders can be approved.")
        if not order.items.exists():
            raise ValidationError("A sales order must contain at least one item.")
        order.approval_status = SalesApprovalStatus.APPROVED
        order.save()
        SalesApprovalHistory.objects.create(
            sales_order=order,
            approval_status=SalesApprovalStatus.APPROVED,
            action_by=user,
        )
        return order


def reject_sales_order(
    order_id: int,
    user: User,
    reason: str,
) -> SalesOrder:
    require_role(user, *MANAGEMENT_ROLES)
    rejection_reason = reason.strip() if reason else ''
    if not rejection_reason:
        raise ValidationError("A rejection reason is required.")
    with transaction.atomic():
        order = _get_order(order_id)
        if order.status != SalesStatus.DRAFT:
            raise ValidationError("Only draft sales orders can be rejected.")
        if order.approval_status != SalesApprovalStatus.PENDING:
            raise ValidationError("Only pending sales orders can be rejected.")
        order.approval_status = SalesApprovalStatus.REJECTED
        order.save()
        SalesApprovalHistory.objects.create(
            sales_order=order,
            approval_status=SalesApprovalStatus.REJECTED,
            action_by=user,
            notes=rejection_reason,
        )
        return order


def dispatch_sales_order(
    order_id: int,
    user: Optional[User] = None,
    dispatch_quantities: Optional[dict[int, Decimal]] = None,
    dispatch_date=None,
    vehicle_reference: str = "",
    notes: str = "",
) -> SalesOrder:
    """Dispatch all or selected remaining order quantities atomically."""
    require_role(user, *SALES_ROLES)
    shortage = None
    try:
        with transaction.atomic():
            order = SalesOrder.objects.select_for_update().get(pk=order_id)
            if order.status not in (SalesStatus.CONFIRMED, SalesStatus.PARTIALLY_DISPATCHED):
                raise ValidationError("Only confirmed orders can be dispatched.")
            if order.approval_status != SalesApprovalStatus.APPROVED:
                raise ValidationError("Sales order approval is required before dispatch.")
            items = list(order.items.select_related("product").order_by("pk"))
            requested = {}
            for item in items:
                remaining = item.quantity - item.dispatched_quantity
                quantity = (
                    remaining if dispatch_quantities is None
                    else dispatch_quantities.get(item.pk, Decimal('0.0000'))
                )
                if not isinstance(quantity, Decimal) or quantity <= Decimal('0.0000'):
                    raise ValidationError(
                        f"Dispatch quantity for '{item.product.name}' must be positive."
                    )
                if quantity > remaining:
                    raise ValidationError(
                        f"Dispatch quantity for '{item.product.name}' cannot exceed "
                        f"the remaining ordered quantity of {remaining}."
                    )
                requested[item.pk] = quantity
                balance = get_product_stock_balance(item.product)
                if balance < quantity:
                    shortage = (order, item, quantity, balance)
                    raise _DispatchShortage
            dispatch = DispatchRecord.objects.create(
                sales_order=order,
                dispatch_number=_next_dispatch_number(),
                dispatched_by=user,
                dispatch_date=dispatch_date or timezone.now(),
                vehicle_reference=vehicle_reference.strip(),
                notes=notes.strip(),
            )
            for item in items:
                quantity = requested[item.pk]
                ledger_entry = create_stock_ledger_entry(
                    movement_type=StockMovementType.OUT,
                    product=item.product,
                    quantity=quantity,
                    reference_number=dispatch.dispatch_number,
                    description=f"Sales dispatch {dispatch.dispatch_number} for order {order.order_number}",
                    created_by=user,
                )
                DispatchItem.objects.create(
                    dispatch=dispatch,
                    sales_order_item=item,
                    quantity=quantity,
                    stock_ledger_entry=ledger_entry,
                )
                SalesOrderItem.objects.filter(pk=item.pk).update(
                    dispatched_quantity=item.dispatched_quantity + quantity
                )
            order.status = (
                SalesStatus.DISPATCHED
                if all(item.dispatched_quantity + requested[item.pk] == item.quantity for item in items)
                else SalesStatus.PARTIALLY_DISPATCHED
            )
            order.dispatch_date = dispatch.dispatch_date
            order.save()
            return order
    except _DispatchShortage:
        order, item, quantity, balance = shortage
        SalesShortage.objects.update_or_create(
            sales_order=order,
            product=item.product,
            defaults={
                'requested_quantity': quantity,
                'available_quantity': balance,
                'shortage_quantity': quantity - balance,
            },
        )
        raise ValidationError(
            f"Insufficient stock for '{item.product.name}': "
            f"required {quantity}, available {balance}."
        )


def cancel_sales_order(
    order_id: int, user: Optional[User] = None, reason: str = ""
) -> SalesOrder:
    """Cancel an order, reversing dispatch entries when stock was dispatched."""
    require_role(user, *SALES_ROLES)
    with transaction.atomic():
        order = _get_order(order_id)
        if order.status == SalesStatus.CANCELLED:
            raise ValidationError("This sales order has already been cancelled.")
        if hasattr(order, 'invoice'):
            raise ValidationError("Invoiced sales orders cannot be cancelled.")
        if order.status in (SalesStatus.PARTIALLY_DISPATCHED, SalesStatus.DISPATCHED):
            for dispatch_item in DispatchItem.objects.filter(
                dispatch__sales_order=order
            ).select_related('sales_order_item__product', 'dispatch'):
                entry = StockLedgerEntry.objects.filter(
                    reference_number=dispatch_item.dispatch.dispatch_number,
                    product=dispatch_item.sales_order_item.product,
                    movement_type=StockMovementType.OUT,
                ).first()
                if not entry:
                    raise ValidationError("Dispatch ledger entry was not found.")
                create_reversal_ledger_entry(
                    original_entry=entry,
                    description=f"Cancellation of sales order {order.order_number}: {reason}".strip(),
                    created_by=user,
                )
        if reason:
            order.notes = f"{order.notes}\nCancellation reason: {reason.strip()}".strip()
        updated = SalesOrder.objects.filter(
            pk=order.pk,
            status__in=(
                SalesStatus.DRAFT,
                SalesStatus.CONFIRMED,
                SalesStatus.PARTIALLY_DISPATCHED,
                SalesStatus.DISPATCHED,
            ),
        ).update(status=SalesStatus.CANCELLED, notes=order.notes)
        if updated != 1:
            raise ValidationError("The sales order status changed before cancellation could be finalized.")
        order.status = SalesStatus.CANCELLED
        return order


def _get_order(order_id: int) -> SalesOrder:
    try:
        return SalesOrder.objects.select_related("customer").get(pk=order_id)
    except (SalesOrder.DoesNotExist, ValueError, TypeError) as exc:
        raise ValidationError(f"Sales order with ID '{order_id}' does not exist.") from exc


def _next_order_number() -> str:
    year = timezone.now().year
    prefix = f"SO-{year}-"
    last = SalesOrder.objects.filter(order_number__startswith=prefix).order_by("-order_number").first()
    number = int(last.order_number.rsplit("-", 1)[-1]) + 1 if last else 1
    return f"{prefix}{number:04d}"


def _next_dispatch_number() -> str:
    year = timezone.now().year
    prefix = f"DIS-{year}-"
    last = DispatchRecord.objects.filter(
        dispatch_number__startswith=prefix
    ).order_by('-dispatch_number').first()
    number = int(last.dispatch_number.rsplit('-', 1)[-1]) + 1 if last else 1
    return f"{prefix}{number:04d}"
