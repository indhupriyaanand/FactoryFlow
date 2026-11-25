"""Purchase receiving services."""

from decimal import Decimal
from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from inventory.models import (
    InventorySource,
    InventorySourceType,
    StockLedgerEntry,
    StockMovementType,
)
from inventory.services import (
    calculate_weighted_average_rate,
    create_inventory_source_record,
    create_stock_ledger_entry,
    create_reversal_ledger_entry,
    get_raw_material_stock_balance,
    get_raw_material_weighted_average_rate,
)
from accounts.access import PURCHASE_ROLES, require_role
from masters.models import RawMaterial
from purchases.models import PurchaseInvoice, PurchaseStatus


def receive_purchase_invoice(invoice_id, created_by=None):
    """
    Receive a DRAFT purchase invoice and create IN ledger entries atomically.

    Returns the received PurchaseInvoice. This function does not alter or reverse
    the ledger if any receiving operation fails.
    """
    require_role(created_by, *PURCHASE_ROLES)
    with transaction.atomic():
        try:
            invoice = (
                PurchaseInvoice.objects.select_related('supplier', 'created_by')
                .get(pk=invoice_id)
            )
        except (PurchaseInvoice.DoesNotExist, ValueError, TypeError):
            raise ValidationError(
                f"Purchase invoice with ID '{invoice_id}' does not exist."
            )

        if invoice.status == PurchaseStatus.RECEIVED:
            raise ValidationError("This purchase invoice has already been received.")
        if invoice.status == PurchaseStatus.CANCELLED:
            raise ValidationError("A cancelled purchase invoice cannot be received.")
        if invoice.status != PurchaseStatus.DRAFT:
            raise ValidationError(
                f"Only DRAFT purchase invoices can be received. Current status: {invoice.status}."
            )

        items = list(
            invoice.items.select_related('raw_material')
            .order_by('pk')
        )
        if not items:
            raise ValidationError(
                "Cannot receive a purchase invoice without purchase items."
            )

        for item in items:
            if item.quantity is None or item.quantity <= 0:
                raise ValidationError({
                    'quantity': (
                        f"Purchase item '{item.raw_material.name}' must have "
                        "a strictly positive quantity."
                    )
                })
            if item.unit != item.raw_material.unit:
                raise ValidationError({
                    'unit': (
                        f"Purchase item unit '{item.unit}' does not match raw material "
                        f"standard unit '{item.raw_material.unit}' for "
                        f"'{item.raw_material.name}'."
                    )
                })

        ledger_entries = []
        try:
            for item in items:
                raw_material = (
                    RawMaterial.objects
                    .select_for_update()
                    .get(pk=item.raw_material_id)
                )
                opening_quantity = get_raw_material_stock_balance(raw_material)
                opening_rate = (
                    get_raw_material_weighted_average_rate(raw_material)
                    if opening_quantity > Decimal('0.0000')
                    else Decimal('0.0000')
                )
                weighted_average_rate = calculate_weighted_average_rate(
                    opening_quantity=opening_quantity,
                    opening_average_rate=opening_rate,
                    received_quantity=item.quantity,
                    received_unit_rate=item.unit_price,
                )
                receipt_datetime = timezone.make_aware(
                    datetime.combine(invoice.purchase_date, time.min)
                )
                ledger_entry = create_stock_ledger_entry(
                    movement_type=StockMovementType.IN,
                    quantity=item.quantity,
                    reference_number=invoice.invoice_number,
                    raw_material=raw_material,
                    unit=item.unit,
                    description=(
                        f"Purchase receipt for invoice {invoice.invoice_number}"
                    ),
                    created_by=created_by,
                )
                create_inventory_source_record(
                    stock_item=raw_material,
                    stock_ledger_entry=ledger_entry,
                    purchase_item=item,
                    source_type=InventorySourceType.RECEIPT,
                    source_reference=invoice.invoice_number,
                    quantity=item.quantity,
                    unit=item.unit,
                    unit_cost=item.unit_price,
                    received_at=receipt_datetime,
                )
                ledger_entries.append(ledger_entry)
                RawMaterial.objects.filter(pk=raw_material.pk).update(
                    weighted_average_rate=weighted_average_rate
                )
        except IntegrityError as exc:
            raise ValidationError(
                "The purchase receipt could not be recorded because of a "
                "stock ledger integrity constraint."
            ) from exc

        updated = (
            PurchaseInvoice.objects
            .filter(pk=invoice.pk, status=PurchaseStatus.DRAFT)
            .update(status=PurchaseStatus.RECEIVED)
        )
        if updated != 1:
            raise ValidationError(
                "The purchase invoice status changed before it could be received."
            )

        invoice.status = PurchaseStatus.RECEIVED
        return invoice


def cancel_purchase_invoice(invoice_id, cancelled_by=None, reason=""):
    """
    Cancel a purchase invoice with atomic integrity.

    - For DRAFT invoices: transitions directly to CANCELLED without ledger changes.
    - For RECEIVED invoices:
      1. Verifies that all line items have sufficient on-hand stock to reverse.
         Prevents negative stock if items were partially consumed in production.
      2. Locates original IN ledger entries and creates compensatory REVERSAL entries.
      3. Transitions invoice status to CANCELLED atomically.
    - For already CANCELLED invoices: raises ValidationError.
    """
    require_role(cancelled_by, *PURCHASE_ROLES)
    with transaction.atomic():
        try:
            invoice = (
                PurchaseInvoice.objects.select_related('supplier', 'created_by')
                .get(pk=invoice_id)
            )
        except (PurchaseInvoice.DoesNotExist, ValueError, TypeError):
            raise ValidationError(
                f"Purchase invoice with ID '{invoice_id}' does not exist."
            )

        if invoice.status == PurchaseStatus.CANCELLED:
            raise ValidationError("This purchase invoice has already been cancelled.")

        if invoice.status == PurchaseStatus.DRAFT:
            # DRAFT invoices have no ledger entries; cancel directly
            updated = (
                PurchaseInvoice.objects
                .filter(pk=invoice.pk, status=PurchaseStatus.DRAFT)
                .update(status=PurchaseStatus.CANCELLED)
            )
            if updated != 1:
                raise ValidationError("The purchase invoice status changed before it could be cancelled.")
            invoice.status = PurchaseStatus.CANCELLED
            return invoice

        if invoice.status != PurchaseStatus.RECEIVED:
            raise ValidationError(
                f"Cannot cancel purchase invoice with invalid status '{invoice.status}'."
            )

        # Invoice is RECEIVED - verify stock availability for each item before reversing
        items = list(invoice.items.select_related('raw_material').order_by('pk'))
        for item in items:
            source_entry = StockLedgerEntry.objects.filter(
                reference_number=invoice.invoice_number,
                raw_material=item.raw_material,
                movement_type=StockMovementType.IN,
            ).first()
            source = InventorySource.objects.filter(
                purchase_item=item,
                source_type=InventorySourceType.RECEIPT,
            ).first()
            if source and source.remaining_quantity < item.quantity:
                raise ValidationError(
                    f"Cannot cancel invoice {invoice.invoice_number}: "
                    f"stock from receipt source '{invoice.invoice_number}' for "
                    f"'{item.raw_material.name}' has already been consumed."
                )
            if not source and source_entry and StockLedgerEntry.objects.filter(
                raw_material=item.raw_material,
                movement_type=StockMovementType.OUT,
                movement_date__gte=source_entry.movement_date,
            ).exists():
                raise ValidationError(
                    f"Cannot cancel purchase invoice {invoice.invoice_number}: "
                    f"stock from '{item.raw_material.name}' may have been consumed after receipt."
                )

        # Create compensatory REVERSAL entries for each item
        for item in items:
            orig_entry = StockLedgerEntry.objects.filter(
                reference_number=invoice.invoice_number,
                raw_material=item.raw_material,
                movement_type=StockMovementType.IN,
            ).first()

            if not orig_entry:
                raise ValidationError(
                    f"Cannot cancel invoice: Original stock ledger entry for '{item.raw_material.name}' "
                    f"under reference '{invoice.invoice_number}' was not found."
                )

            reversal_desc = f"Cancellation of purchase invoice {invoice.invoice_number}"
            if reason:
                reversal_desc += f": {reason.strip()}"

            create_reversal_ledger_entry(
                original_entry=orig_entry,
                description=reversal_desc,
                created_by=cancelled_by,
            )

        # Transition invoice status to CANCELLED atomically
        updated = (
            PurchaseInvoice.objects
            .filter(pk=invoice.pk, status=PurchaseStatus.RECEIVED)
            .update(status=PurchaseStatus.CANCELLED)
        )
        if updated != 1:
            raise ValidationError("The purchase invoice status changed before cancellation could be finalized.")

        invoice.status = PurchaseStatus.CANCELLED
        return invoice
