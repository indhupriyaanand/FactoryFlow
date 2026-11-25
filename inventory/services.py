"""
FactoryFlow ERP - Inventory Services
Phase 4, Stage 2, Step 2: Stock Ledger Service Layer and Integrity Protection.

This module provides the authorized business-logic interface for recording
and reversing stock ledger entries in FactoryFlow ERP. All inventory mutations
must flow through these service functions to maintain an auditable, append-only
ledger with atomic consistency and idempotency.
"""

from decimal import Decimal
from datetime import datetime, timezone as datetime_timezone
from typing import Optional, Union
from django.db import transaction
from django.db.models import F
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.auth.models import User

from masters.models import RawMaterial, Product, UnitChoices
from inventory.models import (
    InventorySource,
    InventorySourceAllocation,
    InventorySourceType,
    StockLedgerEntry,
    StockMovementType,
)


def _insert_ledger_entry(
    *,
    movement_type: str,
    quantity: Decimal,
    reference_number: str,
    raw_material: Optional[RawMaterial] = None,
    product: Optional[Product] = None,
    unit: Optional[str] = None,
    movement_date: Optional[timezone.datetime] = None,
    description: str = "",
    created_by: Optional[User] = None,
    reversed_entry: Optional[StockLedgerEntry] = None,
) -> StockLedgerEntry:
    """
    Internal helper to validate and persist a stock ledger entry inside an atomic transaction.
    Enforces Decimal validation, mutual exclusivity, unit consistency, and duplicate reference protection.
    """
    # 1. Quantity validation
    if isinstance(quantity, float):
        raise ValidationError({
            'quantity': "Floating-point quantities are forbidden to prevent rounding inaccuracies. Use Decimal."
        })
    if not isinstance(quantity, Decimal):
        raise ValidationError({
            'quantity': f"Quantity must be a Decimal instance, got {type(quantity).__name__}."
        })
    if quantity <= Decimal('0.0000'):
        raise ValidationError({
            'quantity': "Quantity must be strictly greater than zero."
        })

    # 2. Movement type validation
    if movement_type not in StockMovementType.values:
        raise ValidationError({
            'movement_type': f"Invalid movement type '{movement_type}'. Must be one of {StockMovementType.values}."
        })

    # 3. Reference number validation
    if not reference_number or not str(reference_number).strip():
        raise ValidationError({
            'reference_number': "Transaction reference number is required."
        })
    cleaned_reference = str(reference_number).strip().upper()

    # 4. Stock item reference: exactly one of raw_material or product
    if not raw_material and not product:
        raise ValidationError(
            "A stock ledger entry must reference either a raw material or a finished product."
        )
    if raw_material and product:
        raise ValidationError(
            "A stock ledger entry cannot reference both a raw material and a finished product."
        )

    # Verify model instances are persisted
    if raw_material and not raw_material.pk:
        raise ValidationError({'raw_material': "Raw material must be saved in the database before creating ledger entries."})
    if product and not product.pk:
        raise ValidationError({'product': "Product must be saved in the database before creating ledger entries."})

    # 5. Unit validation and defaulting
    if raw_material:
        if not unit:
            unit = raw_material.unit
        elif unit != raw_material.unit:
            raise ValidationError({
                'unit': f"Ledger entry unit '{unit}' does not match raw material standard unit '{raw_material.unit}' for '{raw_material.name}'."
            })
    elif product:
        if not unit:
            unit = UnitChoices.PACKET
        elif unit not in [UnitChoices.PACKET, UnitChoices.PIECES]:
            raise ValidationError({
                'unit': f"Ledger entry unit '{unit}' is not a valid unit for finished product packets."
            })

    # 6. Application-level idempotency / duplicate check before DB write
    filter_kwargs = {
        'reference_number': cleaned_reference,
        'movement_type': movement_type,
    }
    if raw_material:
        filter_kwargs['raw_material'] = raw_material
        item_name = raw_material.name
    else:
        filter_kwargs['product'] = product
        item_name = product.name

    if StockLedgerEntry.objects.filter(**filter_kwargs).exists():
        raise ValidationError(
            f"A stock ledger entry for '{item_name}' with reference '{cleaned_reference}' and movement '{movement_type}' already exists."
        )

    # 7. Atomic transaction write
    with transaction.atomic():
        entry = StockLedgerEntry(
            raw_material=raw_material,
            product=product,
            movement_type=movement_type,
            quantity=quantity,
            unit=unit,
            movement_date=movement_date or timezone.now(),
            reference_number=cleaned_reference,
            description=description.strip() if description else "",
            created_by=created_by,
            reversed_entry=reversed_entry,
        )
        entry.full_clean()
        entry.save()
        if movement_type == StockMovementType.OUT:
            _allocate_out_movement(entry)
        return entry


def _stock_item_filter(stock_item):
    return (
        {'raw_material': stock_item}
        if isinstance(stock_item, RawMaterial)
        else {'product': stock_item}
    )


def _legacy_source_reference(stock_item) -> str:
    return f'LEGACY-UNALLOCATED-{stock_item.pk}'


def _ensure_legacy_source(
    stock_item,
    pre_movement_balance: Decimal,
    unit: str,
) -> InventorySource | None:
    item_filter = _stock_item_filter(stock_item)
    traceable_type = (
        InventorySourceType.RECEIPT
        if isinstance(stock_item, RawMaterial)
        else InventorySourceType.PRODUCTION
    )
    traceable_balance = sum(
        InventorySource.objects.filter(
            **item_filter,
            source_type=traceable_type,
        ).values_list('remaining_quantity', flat=True),
        Decimal('0.0000'),
    )
    legacy_balance = pre_movement_balance - traceable_balance
    if legacy_balance <= Decimal('0.0000'):
        return None
    source, created = InventorySource.objects.select_for_update().get_or_create(
        **item_filter,
        source_reference=_legacy_source_reference(stock_item),
        defaults={
            'source_type': InventorySourceType.LEGACY,
            'original_quantity': legacy_balance,
            'remaining_quantity': legacy_balance,
            'unit': unit,
            'unit_cost': (
                get_raw_material_weighted_average_rate(stock_item)
                if isinstance(stock_item, RawMaterial)
                else None
            ),
            'received_at': datetime(1970, 1, 1, tzinfo=datetime_timezone.utc),
        },
    )
    if not created and source.remaining_quantity < legacy_balance:
        # The source already reflects prior allocations; only the current
        # ledger balance determines whether additional legacy stock exists.
        additional = legacy_balance - source.remaining_quantity
        InventorySource.objects.filter(pk=source.pk).update(
            original_quantity=source.original_quantity + additional,
            remaining_quantity=source.remaining_quantity + additional,
        )
        source.refresh_from_db()
    return source


def _allocate_out_movement(entry: StockLedgerEntry) -> None:
    """Allocate a new stock OUT movement FIFO, including uncosted legacy stock."""
    stock_item = entry.stock_item
    item_filter = _stock_item_filter(stock_item)
    get_balance = (
        get_raw_material_stock_balance
        if entry.raw_material_id
        else get_product_stock_balance
    )
    traceable_type = (
        InventorySourceType.RECEIPT
        if entry.raw_material_id
        else InventorySourceType.PRODUCTION
    )
    pre_movement_balance = get_balance(stock_item) + entry.quantity
    legacy_source = _ensure_legacy_source(
        stock_item,
        pre_movement_balance,
        entry.unit,
    )
    sources = list(
        InventorySource.objects.select_for_update().filter(
            **item_filter,
            remaining_quantity__gt=Decimal('0.0000'),
            source_type=traceable_type,
        ).order_by('received_at', 'id')
    )
    if legacy_source and legacy_source.remaining_quantity > Decimal('0.0000'):
        sources.insert(0, legacy_source)

    remaining = entry.quantity
    for source in sources:
        if remaining <= Decimal('0.0000'):
            break
        allocated = min(source.remaining_quantity, remaining)
        if allocated <= Decimal('0.0000'):
            continue
        updated = InventorySource.objects.filter(
            pk=source.pk,
            remaining_quantity__gte=allocated,
        ).update(remaining_quantity=source.remaining_quantity - allocated)
        if updated != 1:
            raise ValidationError(
                f"Inventory source {source.source_reference} changed during allocation."
            )
        InventorySourceAllocation.objects.create(
            stock_ledger_entry=entry,
            source=source,
            quantity=allocated,
        )
        remaining -= allocated

    if remaining > Decimal('0.0000'):
        raise ValidationError(
            f"Insufficient source-allocated stock for '{stock_item.name}': "
            f"{remaining} {entry.unit} could not be allocated."
        )


def _apply_reversal_source_effects(
    original: StockLedgerEntry,
    reversal: StockLedgerEntry,
) -> None:
    """Restore OUT allocations or consume the source of a reversed receipt."""
    if original.movement_type == StockMovementType.OUT:
        allocations = list(original.source_allocations.select_related('source'))
        for allocation in allocations:
            InventorySource.objects.filter(pk=allocation.source_id).update(
                remaining_quantity=F('remaining_quantity') + allocation.quantity
            )
            InventorySourceAllocation.objects.create(
                stock_ledger_entry=reversal,
                source=allocation.source,
                quantity=allocation.quantity,
            )
        return

    if original.movement_type != StockMovementType.IN:
        return
    item_filter = _stock_item_filter(original.stock_item)
    source_type = (
        InventorySourceType.RECEIPT
        if original.raw_material_id
        else InventorySourceType.PRODUCTION
    )
    source = InventorySource.objects.select_for_update().filter(
        **item_filter,
        source_reference=original.reference_number,
        source_type=source_type,
    ).first()
    if not source:
        return
    if source.remaining_quantity < original.quantity:
        raise ValidationError(
            f"Inventory source {source.source_reference} has already been consumed."
        )
    InventorySource.objects.filter(pk=source.pk).update(
        remaining_quantity=F('remaining_quantity') - original.quantity
    )
    InventorySourceAllocation.objects.create(
        stock_ledger_entry=reversal,
        source=source,
        quantity=original.quantity,
    )


def create_stock_ledger_entry(
    *,
    movement_type: str,
    quantity: Decimal,
    reference_number: str,
    raw_material: Optional[RawMaterial] = None,
    product: Optional[Product] = None,
    unit: Optional[str] = None,
    movement_date: Optional[timezone.datetime] = None,
    description: str = "",
    created_by: Optional[User] = None,
) -> StockLedgerEntry:
    """
    Public service function for recording standard inventory movements (IN or OUT).

    Enforces:
    - movement_type must be IN or OUT. Direct REVERSAL creation is disallowed here.
    - quantity must be a strictly positive Decimal.
    - Atomic database execution.
    - Idempotency via composite reference checks and database constraints.
    """
    if movement_type == StockMovementType.REVERSAL:
        raise ValidationError({
            'movement_type': (
                "Direct creation of REVERSAL movements is prohibited through create_stock_ledger_entry(). "
                "Use create_reversal_ledger_entry() to ensure audit linkage to an original transaction."
            )
        })

    return _insert_ledger_entry(
        movement_type=movement_type,
        quantity=quantity,
        reference_number=reference_number,
        raw_material=raw_material,
        product=product,
        unit=unit,
        movement_date=movement_date,
        description=description,
        created_by=created_by,
    )


@transaction.atomic
def create_inventory_source_record(
    *,
    stock_item: Union[RawMaterial, Product],
    stock_ledger_entry: Optional[StockLedgerEntry] = None,
    source_reference: str,
    quantity: Decimal,
    unit: str,
    unit_cost: Optional[Decimal],
    source_type: str,
    purchase_item=None,
    received_at=None,
) -> InventorySource:
    """Create one immutable-cost source lot for a received or produced stock lot."""
    if not isinstance(quantity, Decimal) or quantity <= Decimal('0.0000'):
        raise ValidationError('Inventory source quantity must be a positive Decimal.')
    if unit_cost is not None and (
        not isinstance(unit_cost, Decimal) or unit_cost < Decimal('0.0000')
    ):
        raise ValidationError('Inventory source unit cost must be a non-negative Decimal.')
    item_fields = _stock_item_filter(stock_item)
    if isinstance(stock_item, RawMaterial):
        if source_type not in (InventorySourceType.RECEIPT, InventorySourceType.LEGACY):
            raise ValidationError('Raw-material sources must be receipt or legacy sources.')
    elif isinstance(stock_item, Product):
        if source_type not in (InventorySourceType.PRODUCTION, InventorySourceType.LEGACY):
            raise ValidationError('Finished-goods sources must be production or legacy sources.')
    else:
        raise ValidationError('Inventory source requires a raw material or finished product.')
    if not source_reference or not source_reference.strip():
        raise ValidationError('Inventory source reference is required.')
    if source_type != InventorySourceType.LEGACY and not stock_ledger_entry:
        raise ValidationError('Traceable sources require a stock IN ledger entry.')
    return InventorySource.objects.create(
        **item_fields,
        stock_ledger_entry=stock_ledger_entry,
        purchase_item=purchase_item,
        source_type=source_type,
        source_reference=source_reference.strip().upper(),
        original_quantity=quantity,
        remaining_quantity=quantity,
        unit=unit,
        unit_cost=unit_cost,
        received_at=received_at or timezone.now(),
    )


@transaction.atomic
def create_reversal_ledger_entry(
    *,
    original_entry: Union[StockLedgerEntry, int],
    reference_number: Optional[str] = None,
    description: str = "",
    created_by: Optional[User] = None,
    movement_date: Optional[timezone.datetime] = None,
) -> StockLedgerEntry:
    """
    Public service function for creating a compensatory REVERSAL ledger entry
    referencing an existing original StockLedgerEntry.

    Enforces:
    - original_entry must exist and be an unreversed IN or OUT transaction.
    - Reversals cannot be reversed (no chaining of reversals).
    - Prevents duplicate reversals for the same original entry.
    - The original entry is strictly unmodified (append-only ledger).
    - Wraps all operations in an atomic transaction.
    """
    # 1. Resolve and validate original_entry
    if original_entry is None:
        raise ValidationError("A valid original stock ledger entry is required for reversal.")

    if isinstance(original_entry, int):
        try:
            original = StockLedgerEntry.objects.select_for_update().get(
                pk=original_entry
            )
        except (StockLedgerEntry.DoesNotExist, ValueError):
            raise ValidationError(f"Original stock ledger entry with ID {original_entry} does not exist.")
    elif isinstance(original_entry, StockLedgerEntry):
        if not original_entry.pk:
            raise ValidationError("Original stock ledger entry does not exist in the database.")
        try:
            original = StockLedgerEntry.objects.select_for_update().get(
                pk=original_entry.pk
            )
        except StockLedgerEntry.DoesNotExist as exc:
            raise ValidationError(
                "Original stock ledger entry does not exist in the database."
            ) from exc
    else:
        raise ValidationError(
            f"original_entry must be a StockLedgerEntry instance or primary key integer, got {type(original_entry).__name__}."
        )

    # 2. Reversal entry cannot reverse another reversal
    if original.movement_type == StockMovementType.REVERSAL:
        raise ValidationError(
            f"Cannot reverse ledger entry #{original.pk}: the original entry is already a reversal transaction."
        )

    # 3. Check for existing reversal of this specific entry
    reversal_marker = f"[REVERSAL-OF-ENTRY-#{original.pk}]"
    existing_reversal = StockLedgerEntry.objects.filter(
        movement_type=StockMovementType.REVERSAL,
        description__contains=reversal_marker
    ).first()
    if existing_reversal:
        raise ValidationError(
            f"A reversal entry already exists for ledger entry #{original.pk} (Reversal Entry ID: #{existing_reversal.pk})."
        )

    # 4. Formulate reversal reference number and description
    if reference_number and str(reference_number).strip():
        ref = str(reference_number).strip().upper()
    else:
        ref = f"REV-{original.reference_number}"

    custom_desc = description.strip() if description else f"Reversal of {original.get_movement_type_display()} entry #{original.pk}"
    full_description = f"{reversal_marker} {custom_desc}"

    # 5. Insert reversal entry atomically
    reversal = _insert_ledger_entry(
        movement_type=StockMovementType.REVERSAL,
        quantity=original.quantity,
        reference_number=ref,
        raw_material=original.raw_material,
        product=original.product,
        unit=original.unit,
        movement_date=movement_date or timezone.now(),
        description=full_description,
        created_by=created_by,
        reversed_entry=original,
    )
    _apply_reversal_source_effects(original, reversal)
    return reversal


def get_inventory_source_cost(entry: StockLedgerEntry) -> Optional[Decimal]:
    """Return cost for a fully allocated movement whose source costs are known."""
    allocations = list(entry.source_allocations.select_related('source'))
    if not allocations or sum(
        (allocation.quantity for allocation in allocations),
        Decimal('0.0000'),
    ) != entry.quantity:
        return None
    if any(allocation.source.unit_cost is None for allocation in allocations):
        return None
    return sum(
        (
            allocation.quantity * allocation.source.unit_cost
            for allocation in allocations
        ),
        Decimal('0.00'),
    )


# ==============================================================================
# STOCK BALANCE AND AVAILABILITY SERVICES
# ==============================================================================

def get_raw_material_stock_balance(raw_material: Union[RawMaterial, int]) -> Decimal:
    """
    Calculates the real-time on-hand stock balance for a raw material directly
    from append-only StockLedgerEntry audit records.

    Accounting Rules:
    - IN adds to stock (+quantity)
    - OUT deducts from stock (-quantity)
    - REVERSAL negates the original movement direction:
      - Reversal of IN: deducts from stock (-quantity)
      - Reversal of OUT: restores stock (+quantity)
    """
    rm_id = raw_material.pk if isinstance(raw_material, RawMaterial) else raw_material
    entries = StockLedgerEntry.objects.filter(raw_material_id=rm_id).select_related('reversed_entry')
    balance = Decimal('0.0000')
    for entry in entries:
        balance += entry.net_quantity
    return balance.quantize(Decimal('0.0001'))


def calculate_weighted_average_rate(
    opening_quantity: Decimal,
    opening_average_rate: Decimal,
    received_quantity: Decimal,
    received_unit_rate: Decimal,
) -> Decimal:
    """
    Calculate the post-receipt weighted average rate for one material unit.

    Quantities and rates must be Decimal values. Rates are rounded to four
    decimal places to preserve precision for subsequent inventory costing.
    """
    values = {
        'opening_quantity': opening_quantity,
        'opening_average_rate': opening_average_rate,
        'received_quantity': received_quantity,
        'received_unit_rate': received_unit_rate,
    }
    for name, value in values.items():
        if not isinstance(value, Decimal):
            raise ValidationError({
                name: f"{name} must be a Decimal instance."
            })

    if opening_quantity < Decimal('0.0000'):
        raise ValidationError({'opening_quantity': "Opening quantity cannot be negative."})
    if opening_average_rate < Decimal('0.0000'):
        raise ValidationError({'opening_average_rate': "Opening average rate cannot be negative."})
    if received_quantity <= Decimal('0.0000'):
        raise ValidationError({'received_quantity': "Received quantity must be strictly greater than zero."})
    if received_unit_rate < Decimal('0.0000'):
        raise ValidationError({'received_unit_rate': "Received unit rate cannot be negative."})

    if opening_quantity == Decimal('0.0000'):
        return received_unit_rate.quantize(Decimal('0.0001'))

    total_quantity = opening_quantity + received_quantity
    total_value = (
        opening_quantity * opening_average_rate
        + received_quantity * received_unit_rate
    )
    return (total_value / total_quantity).quantize(Decimal('0.0001'))


def get_raw_material_weighted_average_rate(
    raw_material: Union[RawMaterial, int],
) -> Decimal:
    """Return the stored WAC rate, falling back to the legacy standard rate."""
    material = (
        raw_material
        if isinstance(raw_material, RawMaterial)
        else RawMaterial.objects.get(pk=raw_material)
    )
    rate = material.weighted_average_rate
    if rate == Decimal('0.0000'):
        rate = material.standard_rate
    return rate.quantize(Decimal('0.0001'))


def get_product_stock_balance(product: Union[Product, int]) -> Decimal:
    """
    Calculates the real-time on-hand stock balance for a finished product directly
    from append-only StockLedgerEntry audit records.
    """
    prod_id = product.pk if isinstance(product, Product) else product
    entries = StockLedgerEntry.objects.filter(product_id=prod_id).select_related('reversed_entry')
    balance = Decimal('0.0000')
    for entry in entries:
        balance += entry.net_quantity
    return balance.quantize(Decimal('0.0001'))


def validate_stock_availability(
    raw_material: Union[RawMaterial, int],
    required_quantity: Decimal
) -> tuple[bool, Decimal]:
    """
    Validates whether sufficient stock is available to satisfy required_quantity.
    Returns (is_available: bool, current_balance: Decimal).
    """
    current_balance = get_raw_material_stock_balance(raw_material)
    is_available = current_balance >= required_quantity
    return is_available, current_balance


def validate_product_availability(
    product: Union[Product, int],
    required_quantity: Decimal
) -> tuple[bool, Decimal]:
    """
    Validates whether sufficient finished goods stock is available to satisfy required_quantity.
    Returns (is_available: bool, current_balance: Decimal).
    """
    current_balance = get_product_stock_balance(product)
    is_available = current_balance >= required_quantity
    return is_available, current_balance


def get_all_raw_material_stock_balances() -> list[dict]:
    """
    Returns inventory summary for all active raw materials.
    Includes current stock balance, standard procurement rate, valuation, and reorder status.
    """
    from django.conf import settings

    materials = RawMaterial.objects.filter(is_active=True).order_by('name')
    summary = []
    for rm in materials:
        balance = get_raw_material_stock_balance(rm)
        weighted_average_rate = get_raw_material_weighted_average_rate(rm)
        stock_value = (balance * weighted_average_rate).quantize(Decimal('0.01'))
        is_low_stock = balance <= Decimal(
            str(settings.LOW_STOCK_RAW_MATERIAL_THRESHOLD)
        )
        summary.append({
            'raw_material': rm,
            'balance': balance,
            'unit': rm.unit,
            'unit_display': rm.get_unit_display(),
            'standard_rate': rm.standard_rate,
            'weighted_average_rate': weighted_average_rate,
            'stock_value': stock_value,
            'is_low_stock': is_low_stock,
        })
    return summary


def get_all_product_stock_balances() -> list[dict]:
    """
    Returns inventory summary for all active finished products.
    Includes current stock in packets, unit selling price, and total valuation.
    """
    products = Product.objects.filter(is_active=True).order_by('name')
    summary = []
    for prod in products:
        balance = get_product_stock_balance(prod)
        stock_value = (balance * prod.unit_price).quantize(Decimal('0.01'))
        summary.append({
            'product': prod,
            'balance': balance,
            'unit': UnitChoices.PACKET,
            'unit_price': prod.unit_price,
            'stock_value': stock_value,
        })
    return summary


def get_low_stock_raw_materials(threshold: Optional[Decimal] = None) -> list[dict]:
    """
    Filters raw materials whose on-hand balance is less than or equal to the reorder threshold.
    """
    all_balances = get_all_raw_material_stock_balances()
    if threshold is None:
        from django.conf import settings

        threshold = Decimal(str(settings.LOW_STOCK_RAW_MATERIAL_THRESHOLD))
    return [item for item in all_balances if item['balance'] <= threshold]
