"""
FactoryFlow ERP - Production Services
Orchestrates batch production workflows, BOM material consumption,
finished goods generation, and atomic inventory ledger tracking.
"""

from decimal import Decimal
from typing import Union, Optional
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.auth.models import User

from masters.models import BillOfMaterials, Product, UnitChoices
from masters.services import calculate_bom_requirements
from inventory.models import (
    InventorySource,
    InventorySourceType,
    StockLedgerEntry,
    StockMovementType,
)
from inventory.services import (
    create_inventory_source_record,
    create_stock_ledger_entry,
    create_reversal_ledger_entry,
    get_inventory_source_cost,
    get_raw_material_stock_balance,
    get_raw_material_weighted_average_rate,
    get_product_stock_balance,
)
from production.models import (
    ProductionApprovalStatus,
    ProductionApprovalHistory,
    ProductionDamageRecord,
    ProductionDamageType,
    ProductionOrder,
    ProductionMaterialUsage,
    ProductionStatus,
)
from accounts.access import PRODUCTION_ROLES, require_role


def generate_production_order_number() -> str:
    """Generates unique production batch number in sequence PRD-2026-XXXX."""
    current_year = timezone.now().year
    prefix = f"PRD-{current_year}-"
    last_order = (
        ProductionOrder.objects
        .filter(order_number__startswith=prefix)
        .order_by('-order_number')
        .first()
    )
    if last_order:
        try:
            last_num = int(last_order.order_number.split('-')[-1])
            new_num = last_num + 1
        except (ValueError, IndexError):
            new_num = 1
    else:
        new_num = 1
    return f"{prefix}{new_num:04d}"


def create_production_order(
    product: Union[Product, int],
    target_quantity: Decimal,
    user: Optional[User] = None,
    start_date=None,
    notes: str = "",
    order_number: Optional[str] = None
) -> ProductionOrder:
    """
    Creates a new production batch order and pre-populates planned material usages from BOM.
    """
    require_role(user, *PRODUCTION_ROLES)
    if isinstance(target_quantity, float):
        raise ValidationError("target_quantity must be a Decimal instance, not float.")
    if not isinstance(target_quantity, Decimal):
        raise ValidationError(f"target_quantity must be a Decimal instance, got {type(target_quantity).__name__}.")
    if target_quantity <= Decimal('0.0000'):
        raise ValidationError("Target quantity must be strictly greater than zero.")

    prod = Product.objects.get(pk=product) if isinstance(product, int) else product
    bom_requirements = calculate_bom_requirements(prod, target_quantity)

    with transaction.atomic():
        batch_num = order_number.strip().upper() if order_number else generate_production_order_number()
        order = ProductionOrder(
            order_number=batch_num,
            product=prod,
            target_quantity=target_quantity,
            status=ProductionStatus.PLANNED,
            approval_status=ProductionApprovalStatus.PENDING,
            start_date=start_date or timezone.now().date(),
            created_by=user,
            notes=notes.strip() if notes else ""
        )
        order.full_clean()
        order.save()

        for req in bom_requirements:
            planned_rate = get_raw_material_weighted_average_rate(
                req['raw_material']
            )
            if (
                planned_rate == Decimal('0.0000')
                and not InventorySource.objects.filter(
                    raw_material=req['raw_material'],
                    source_type=InventorySourceType.RECEIPT,
                ).exists()
            ):
                planned_rate = None
            usage = ProductionMaterialUsage(
                production_order=order,
                raw_material=req['raw_material'],
                planned_quantity=req['required_quantity'],
                planned_unit_rate=planned_rate,
                actual_quantity=Decimal('0.0000'),
                unit=req['unit']
            )
            usage.full_clean()
            usage.save()

        return order


def approve_production_order(order_id: int, user: User) -> ProductionOrder:
    if not user or not (user.is_superuser or getattr(user, 'profile', None) and user.profile.role in PRODUCTION_ROLES):
        raise ValidationError("Only production supervisors or administrators can approve production.")
    with transaction.atomic():
        try:
            order = ProductionOrder.objects.get(pk=order_id)
        except (ProductionOrder.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError(f"Production order with ID '{order_id}' does not exist.") from exc
        if order.status in [ProductionStatus.COMPLETED, ProductionStatus.CANCELLED]:
            raise ValidationError("Finalized production orders cannot be approved.")
        if order.approval_status != ProductionApprovalStatus.PENDING:
            raise ValidationError("Only pending production orders can be approved.")
        order.approval_status = ProductionApprovalStatus.APPROVED
        order.approved_by = user
        order.approved_at = timezone.now()
        order.save()
        ProductionApprovalHistory.objects.create(
            production_order=order,
            approval_status=ProductionApprovalStatus.APPROVED,
            action_by=user,
        )
        return order


def complete_production_order(
    order_id: int,
    produced_quantity: Optional[Decimal] = None,
    user: Optional[User] = None,
    actual_material_quantities=None,
    damaged_pouch_quantity: Decimal = Decimal('0.0000'),
    labour_cost: Decimal = Decimal('0.00'),
    power_cost: Decimal = Decimal('0.00'),
) -> ProductionOrder:
    """
    Finalizes a production batch order:
    1. Verifies raw material stock availability.
    2. Atomically creates OUT ledger entries for raw material consumption.
    3. Atomically creates IN ledger entry for finished product output.
    4. Transitions order status to COMPLETED.
    """
    require_role(user, *PRODUCTION_ROLES)
    with transaction.atomic():
        try:
            order = (
                ProductionOrder.objects
                .select_related('product', 'created_by')
                .get(pk=order_id)
            )
        except (ProductionOrder.DoesNotExist, ValueError):
            raise ValidationError(f"Production order with ID '{order_id}' does not exist.")

        if order.status == ProductionStatus.COMPLETED:
            raise ValidationError(f"Production order '{order.order_number}' is already completed.")
        if order.status == ProductionStatus.CANCELLED:
            raise ValidationError(f"Cancelled production order '{order.order_number}' cannot be completed.")
        if order.approval_status != ProductionApprovalStatus.APPROVED:
            raise ValidationError("Production order must be approved before completion.")

        actual_output = produced_quantity if produced_quantity is not None else order.target_quantity
        if isinstance(actual_output, float):
            raise ValidationError("produced_quantity must be a Decimal instance, not float.")
        if not isinstance(actual_output, Decimal) or actual_output <= Decimal('0.0000'):
            raise ValidationError("Produced output quantity must be strictly positive.")
        for name, value in {
            'damaged_pouch_quantity': damaged_pouch_quantity,
            'labour_cost': labour_cost,
            'power_cost': power_cost,
        }.items():
            if not isinstance(value, Decimal) or value < Decimal('0.00'):
                raise ValidationError(f"{name} must be a non-negative Decimal.")

        usages = list(order.material_usages.select_related('raw_material'))
        if not usages:
            raise ValidationError(f"Production order '{order.order_number}' has no material usages defined.")

        actual_material_quantities = actual_material_quantities or {}
        material_cost = Decimal('0.00')
        resolved_actuals = {}
        for usage in usages:
            actual_quantity = actual_material_quantities.get(
                usage.raw_material_id, usage.planned_quantity
            )
            if not isinstance(actual_quantity, Decimal) or actual_quantity <= Decimal('0.0000'):
                raise ValidationError(
                    f"Actual quantity for '{usage.raw_material.name}' must be a positive Decimal."
                )
            resolved_actuals[usage.pk] = actual_quantity
            current_stock = get_raw_material_stock_balance(usage.raw_material)
            extra_damage = (
                damaged_pouch_quantity
                if usage.raw_material.is_pouch
                else Decimal('0.0000')
            )
            if current_stock < actual_quantity + extra_damage:
                raise ValidationError(
                    f"Insufficient stock for '{usage.raw_material.name}'. "
                    f"Required: {actual_quantity + extra_damage} {usage.unit}, Available: {current_stock} {usage.unit}."
                )

        # 2. Record material consumption (OUT) in stock ledger
        for usage in usages:
            consumption_entry = create_stock_ledger_entry(
                movement_type=StockMovementType.OUT,
                raw_material=usage.raw_material,
                quantity=resolved_actuals[usage.pk],
                unit=usage.unit,
                reference_number=order.order_number,
                description=f"Material consumption for batch {order.order_number}",
                created_by=user
            )
            consumption_cost = get_inventory_source_cost(consumption_entry)
            if consumption_cost is None:
                raise ValidationError(
                    f"Material source allocation is incomplete for "
                    f"'{usage.raw_material.name}'."
                )
            material_cost += consumption_cost
            usage.actual_quantity = resolved_actuals[usage.pk]
            usage.save()

            if damaged_pouch_quantity and usage.raw_material.is_pouch:
                damage_entry = create_stock_ledger_entry(
                    movement_type=StockMovementType.OUT,
                    raw_material=usage.raw_material,
                    quantity=damaged_pouch_quantity,
                    unit=usage.unit,
                    reference_number=f"{order.order_number}-DAMAGE",
                    description=f"Damaged pouches for batch {order.order_number}",
                    created_by=user,
                )
                damage_cost = get_inventory_source_cost(damage_entry)
                if damage_cost is None:
                    raise ValidationError(
                        f"Material source allocation is incomplete for "
                        f"damaged '{usage.raw_material.name}'."
                    )
                material_cost += damage_cost
                ProductionDamageRecord.objects.create(
                    production_order=order,
                    raw_material=usage.raw_material,
                    damage_type=ProductionDamageType.DAMAGED_POUCH,
                    quantity=damaged_pouch_quantity,
                    unit=usage.unit,
                    recorded_by=user,
                    stock_ledger_entry=damage_entry,
                )

        material_cost = material_cost.quantize(Decimal('0.01'))
        total_cost = (material_cost + labour_cost + power_cost).quantize(
            Decimal('0.01')
        )
        source_unit_cost = (total_cost / actual_output).quantize(
            Decimal('0.00000001')
        )
        unit_cost = source_unit_cost.quantize(Decimal('0.0001'))

        # 3. Record finished product generation and its immutable production cost.
        output_entry = create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            product=order.product,
            quantity=actual_output,
            unit=order.product.packet_size if order.product.packet_size in ['packet', 'pieces'] else 'packet',
            reference_number=order.order_number,
            description=f"Finished goods output for batch {order.order_number}",
            created_by=user
        )
        create_inventory_source_record(
            stock_item=order.product,
            stock_ledger_entry=output_entry,
            source_reference=order.order_number,
            quantity=actual_output,
            unit=output_entry.unit,
            unit_cost=source_unit_cost,
            source_type=InventorySourceType.PRODUCTION,
            received_at=output_entry.movement_date,
        )

        # 4. Finalize order
        order.produced_quantity = actual_output
        order.damaged_pouch_quantity = damaged_pouch_quantity
        order.labour_cost = labour_cost
        order.power_cost = power_cost
        order.material_cost = material_cost
        order.total_cost = total_cost
        order.unit_cost = unit_cost
        order.status = ProductionStatus.COMPLETED
        order.completion_date = timezone.now()
        order.save()

        return order


def record_damage(
    production_order_id: int,
    damage_type: str,
    quantity: Decimal,
    user: User,
    *,
    raw_material=None,
    product=None,
    unit: Optional[str] = None,
    reason: str = "",
    recorded_at=None,
) -> ProductionDamageRecord:
    """Record and, where applicable, consume stock for a damage or scrap event."""
    require_role(user, *PRODUCTION_ROLES)
    if not isinstance(quantity, Decimal) or quantity <= Decimal('0.0000'):
        raise ValidationError("Damage quantity must be a positive Decimal.")
    if damage_type not in ProductionDamageType.values:
        raise ValidationError("Invalid damage type.")
    with transaction.atomic():
        try:
            order = ProductionOrder.objects.select_for_update().get(pk=production_order_id)
        except (ProductionOrder.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError("Production order does not exist.") from exc
        if order.status == ProductionStatus.CANCELLED:
            raise ValidationError("Cancelled production orders cannot receive damage records.")
        if (raw_material and product) or (not raw_material and not product):
            raise ValidationError("Select exactly one damaged raw material or product.")
        resolved_unit = unit or (raw_material.unit if raw_material else UnitChoices.PACKET)
        record = ProductionDamageRecord(
            production_order=order,
            raw_material=raw_material,
            product=product,
            damage_type=damage_type,
            quantity=quantity,
            unit=resolved_unit,
            reason=reason.strip(),
            recorded_by=user,
            recorded_at=recorded_at or timezone.now(),
        )
        record.full_clean()
        record.save()
        ledger_entry = create_stock_ledger_entry(
            movement_type=StockMovementType.OUT,
            raw_material=raw_material,
            product=product,
            quantity=quantity,
            unit=resolved_unit,
            reference_number=f"DAMAGE-{record.pk}",
            description=f"{damage_type.title()} for production {order.order_number}",
            created_by=user,
        )
        ProductionDamageRecord.objects.filter(pk=record.pk).update(
            stock_ledger_entry=ledger_entry
        )
        record.stock_ledger_entry = ledger_entry
        return record


def void_damage_record(record_id: int, user: User, reason: str = "") -> ProductionDamageRecord:
    """Void a damage record with a compensating ledger reversal."""
    require_role(user, *PRODUCTION_ROLES)
    with transaction.atomic():
        try:
            record = ProductionDamageRecord.objects.select_for_update().get(pk=record_id)
        except (ProductionDamageRecord.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError("Damage record does not exist.") from exc
        if record.is_voided:
            raise ValidationError("Damage record is already voided.")
        if record.stock_ledger_entry_id:
            create_reversal_ledger_entry(
                original_entry=record.stock_ledger_entry_id,
                description=f"Void damage record #{record.pk}: {reason.strip()}".strip(),
                created_by=user,
            )
        record.is_voided = True
        record.voided_at = timezone.now()
        record.voided_by = user
        record.void_reason = reason.strip()
        record.save()
        return record


def cancel_production_order(
    order_id: int,
    user: Optional[User] = None,
    reason: str = ""
) -> ProductionOrder:
    """
    Cancels a production order with full atomic rollback:
    - If COMPLETED: verifies on-hand finished goods stock, reverses production output,
      and reverses material consumption.
    - If not completed: simply marks status CANCELLED.
    """
    require_role(user, *PRODUCTION_ROLES)
    with transaction.atomic():
        try:
            order = ProductionOrder.objects.get(pk=order_id)
        except (ProductionOrder.DoesNotExist, ValueError):
            raise ValidationError(f"Production order with ID '{order_id}' does not exist.")

        if order.status == ProductionStatus.CANCELLED:
            raise ValidationError(f"Production order '{order.order_number}' is already cancelled.")

        if order.status == ProductionStatus.COMPLETED:
            # Check finished goods stock
            current_prod_stock = get_product_stock_balance(order.product)
            if current_prod_stock < order.produced_quantity:
                raise ValidationError(
                    f"Cannot cancel completed production order '{order.order_number}': "
                    f"Finished stock has already been consumed/dispatched. "
                    f"Required to reverse: {order.produced_quantity} pkts, Available: {current_prod_stock} pkts."
                )

            # Reverse finished product IN entry
            prod_in_entry = StockLedgerEntry.objects.filter(
                reference_number=order.order_number,
                product=order.product,
                movement_type=StockMovementType.IN
            ).first()
            if prod_in_entry:
                create_reversal_ledger_entry(
                    original_entry=prod_in_entry,
                    description=f"Reversal of production output for batch {order.order_number}: {reason}".strip(),
                    created_by=user
                )

            # Reverse material OUT entries
            usages = order.material_usages.select_related('raw_material')
            for usage in usages:
                mat_out_entries = StockLedgerEntry.objects.filter(
                    reference_number__in=[order.order_number, f"{order.order_number}-DAMAGE"],
                    raw_material=usage.raw_material,
                    movement_type=StockMovementType.OUT
                )
                for mat_out_entry in mat_out_entries:
                    create_reversal_ledger_entry(
                        original_entry=mat_out_entry,
                        description=f"Reversal of material consumption for batch {order.order_number}: {reason}".strip(),
                        created_by=user
                    )

        order.notes = f"{order.notes}\nCancellation reason: {reason}".strip()
        if order.status == ProductionStatus.COMPLETED:
            updated = ProductionOrder.objects.filter(
                pk=order.pk, status=ProductionStatus.COMPLETED
            ).update(
                status=ProductionStatus.CANCELLED,
                notes=order.notes,
            )
            if updated != 1:
                raise ValidationError("Production order status changed before cancellation.")
        else:
            order.status = ProductionStatus.CANCELLED
            order.save()
        order.status = ProductionStatus.CANCELLED
        return order
