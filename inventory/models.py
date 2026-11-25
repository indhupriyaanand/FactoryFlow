from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from core.models import TimeStampedModel
from masters.models import RawMaterial, Product, UnitChoices


class StockMovementType(models.TextChoices):
    """
    Standard movement classifications for inventory ledger transactions.
    - IN: Inward stock addition (e.g. Purchase receipt, Production output, Opening balance)
    - OUT: Outward stock deduction (e.g. Production consumption, Sales dispatch, Damaged scrap)
    - REVERSAL: Compensatory reversal transaction correcting a prior movement
    """
    IN = 'IN', 'Stock In'
    OUT = 'OUT', 'Stock Out'
    REVERSAL = 'REVERSAL', 'Stock Reversal'


class StockLedgerQuerySet(models.QuerySet):
    """
    Custom QuerySet enforcing append-only audit trail rules at the ORM QuerySet level.
    Blocks bulk updates and deletions that would otherwise bypass model-level save() and delete() hooks.
    """
    def update(self, **kwargs):
        raise ValidationError(
            "Stock ledger entries are append-only and cannot be modified via QuerySet.update()."
        )

    def delete(self):
        raise ValidationError(
            "Stock ledger entries are permanent audit records and cannot be deleted via QuerySet.delete()."
        )

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError(
            "Stock ledger entries are append-only and cannot be modified via QuerySet.bulk_update()."
        )


class StockLedgerEntry(TimeStampedModel):
    """
    Append-Only Stock Ledger Entry:
    The single source of truth for all inventory movements across FactoryFlow.
    Supports both Raw Materials and Finished Products.
    Existing records cannot be modified or deleted.
    """
    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_ledger_entries',
        help_text="Referenced raw material or packaging component (mutually exclusive with product)."
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_ledger_entries',
        help_text="Referenced finished product (mutually exclusive with raw material)."
    )
    movement_type = models.CharField(
        max_length=10,
        choices=StockMovementType.choices,
        db_index=True,
        help_text="Direction of stock movement (IN, OUT, REVERSAL)."
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
        help_text="Absolute quantity moved. Must be strictly positive."
    )
    unit = models.CharField(
        max_length=20,
        choices=UnitChoices.choices,
        blank=True,
        help_text="Unit of measurement. Must match the referenced stock item's standard unit."
    )
    movement_date = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text="Effective timestamp when the stock movement occurred."
    )
    reference_number = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Source transaction identifier (e.g. Purchase Invoice #, Batch #, Reversal ID)."
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional remarks or operational description."
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_ledger_entries',
        help_text="User who logged or triggered this stock movement."
    )
    reversed_entry = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reversals',
        help_text="Reference to the original stock ledger entry if this is a reversal."
    )

    objects = StockLedgerQuerySet.as_manager()

    class Meta:
        verbose_name = 'Stock Ledger Entry'
        verbose_name_plural = 'Stock Ledger Entries'
        ordering = ['-movement_date', '-created_at', '-id']
        indexes = [
            models.Index(fields=['raw_material', 'movement_date']),
            models.Index(fields=['product', 'movement_date']),
            models.Index(fields=['reference_number']),
            models.Index(fields=['movement_type']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['reference_number', 'raw_material', 'movement_type'],
                condition=models.Q(raw_material__isnull=False),
                name='unique_raw_material_ledger_reference'
            ),
            models.UniqueConstraint(
                fields=['reference_number', 'product', 'movement_type'],
                condition=models.Q(product__isnull=False),
                name='unique_product_ledger_reference'
            ),
        ]

    @property
    def is_raw_material(self):
        return self.raw_material is not None

    @property
    def is_finished_product(self):
        return self.product is not None

    @property
    def stock_item(self):
        return self.raw_material if self.raw_material else self.product

    @property
    def net_quantity(self):
        """
        Calculates signed quantity impact on stock balance:
        +quantity for IN
        -quantity for OUT
        -quantity for REVERSAL of IN (negates inward addition)
        +quantity for REVERSAL of OUT (restores outward deduction)
        """
        if self.movement_type == StockMovementType.IN:
            return self.quantity
        elif self.movement_type == StockMovementType.OUT:
            return -self.quantity
        elif self.movement_type == StockMovementType.REVERSAL:
            orig_type = None
            if self.reversed_entry_id:
                orig_type = self.reversed_entry.movement_type
            elif self.description:
                import re
                match = re.search(r'\[REVERSAL-OF-ENTRY-#(\d+)\]', self.description)
                if match:
                    try:
                        orig = StockLedgerEntry.objects.get(pk=int(match.group(1)))
                        orig_type = orig.movement_type
                    except StockLedgerEntry.DoesNotExist:
                        pass
                if not orig_type:
                    if "OUT" in self.description.upper():
                        orig_type = StockMovementType.OUT
                    else:
                        orig_type = StockMovementType.IN
            if orig_type == StockMovementType.OUT:
                return self.quantity
            return -self.quantity
        return Decimal('0.0000')

    def clean(self):
        super().clean()

        # 1. Quantity must be strictly greater than zero
        if self.quantity is not None and self.quantity <= Decimal('0.0000'):
            raise ValidationError({'quantity': "Quantity must be strictly greater than zero."})

        # 2. Movement type validation
        if self.movement_type not in StockMovementType.values:
            raise ValidationError({'movement_type': f"Invalid movement type '{self.movement_type}'."})

        # 3. Stock item reference: exactly one of raw_material or product must be set
        if not self.raw_material and not self.product:
            raise ValidationError(
                "A stock ledger entry must reference either a raw material or a finished product."
            )
        if self.raw_material and self.product:
            raise ValidationError(
                "A stock ledger entry cannot reference both a raw material and a finished product."
            )

        # 4. Reference number validation
        if not self.reference_number or not self.reference_number.strip():
            raise ValidationError({'reference_number': "Transaction reference number is required."})
        self.reference_number = self.reference_number.strip().upper()

        # 5. Unit validation against referenced stock item
        if self.raw_material:
            if not self.unit:
                self.unit = self.raw_material.unit
            elif self.unit != self.raw_material.unit:
                raise ValidationError({
                    'unit': f"Ledger entry unit '{self.unit}' does not match raw material standard unit '{self.raw_material.unit}' for '{self.raw_material.name}'."
                })
        elif self.product:
            if not self.unit:
                self.unit = UnitChoices.PACKET
            elif self.unit not in [UnitChoices.PACKET, UnitChoices.PIECES]:
                raise ValidationError({
                    'unit': f"Ledger entry unit '{self.unit}' is not a valid unit for finished product packets."
                })

    def save(self, *args, **kwargs):
        # Append-only check: Existing records cannot be modified
        if self.pk:
            raise ValidationError(
                "Stock ledger entries are append-only audit records and cannot be modified once created."
            )

        # Default unit if omitted before full_clean runs
        if self.raw_material and not self.unit:
            self.unit = self.raw_material.unit
        elif self.product and not self.unit:
            self.unit = UnitChoices.PACKET

        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Append-only check: Existing records cannot be deleted
        raise ValidationError(
            "Stock ledger entries are permanent audit records and cannot be deleted."
        )

    def __str__(self):
        item_name = self.stock_item.name if self.stock_item else "Unknown"
        return f"[{self.get_movement_type_display()}] {self.quantity} {self.unit} of {item_name} (Ref: {self.reference_number})"


class InventorySourceType(models.TextChoices):
    RECEIPT = 'RECEIPT', 'Purchase receipt'
    PRODUCTION = 'PRODUCTION', 'Production output'
    LEGACY = 'LEGACY', 'Legacy unallocated stock'


class InventorySource(TimeStampedModel):
    """
    A traceable quantity and immutable unit-cost source for stock.

    Production and purchase sources carry ledger links. Legacy sources are
    aggregate compatibility buckets for stock that predates source tracking.
    """
    purchase_item = models.OneToOneField(
        'purchases.PurchaseItem',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventory_source',
    )
    stock_ledger_entry = models.OneToOneField(
        StockLedgerEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventory_source',
    )
    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventory_sources',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventory_sources',
    )
    source_type = models.CharField(
        max_length=10,
        choices=InventorySourceType.choices,
        default=InventorySourceType.RECEIPT,
    )
    source_reference = models.CharField(max_length=100, db_index=True)
    original_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
    )
    remaining_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0000'))],
    )
    unit = models.CharField(max_length=20, choices=UnitChoices.choices)
    unit_cost = models.DecimalField(
        max_digits=16,
        decimal_places=8,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.0000'))],
    )
    received_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['received_at', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['raw_material', 'source_reference'],
                condition=models.Q(raw_material__isnull=False),
                name='unique_inventory_source_reference',
            ),
            models.UniqueConstraint(
                fields=['product', 'source_reference'],
                condition=models.Q(product__isnull=False),
                name='unique_product_inventory_source_reference',
            ),
            models.CheckConstraint(
                condition=models.Q(remaining_quantity__gte=Decimal('0.0000')),
                name='inventory_source_remaining_nonnegative',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(raw_material__isnull=False, product__isnull=True)
                    | models.Q(raw_material__isnull=True, product__isnull=False)
                ),
                name='inventory_source_exactly_one_item',
            ),
        ]

    def clean(self):
        super().clean()
        if self.original_quantity <= Decimal('0.0000'):
            raise ValidationError({'original_quantity': 'Original quantity must be positive.'})
        if self.remaining_quantity < Decimal('0.0000'):
            raise ValidationError({'remaining_quantity': 'Remaining quantity cannot be negative.'})
        if self.remaining_quantity > self.original_quantity:
            raise ValidationError({'remaining_quantity': 'Remaining quantity cannot exceed original quantity.'})
        if bool(self.raw_material_id) == bool(self.product_id):
            raise ValidationError('An inventory source must reference exactly one stock item.')
        if self.raw_material_id:
            if self.unit != self.raw_material.unit:
                raise ValidationError({'unit': 'Source unit must match the raw material unit.'})
            if self.source_type == InventorySourceType.RECEIPT:
                if not self.purchase_item_id:
                    raise ValidationError('Purchase receipt sources must reference a purchase item.')
            elif self.source_type != InventorySourceType.LEGACY or self.purchase_item_id:
                raise ValidationError('Raw-material sources must be receipts or legacy sources.')
        else:
            if self.unit not in (UnitChoices.PACKET, UnitChoices.PIECES):
                raise ValidationError('Finished-goods sources must use packets or pieces.')
            if self.purchase_item_id or self.source_type not in (
                InventorySourceType.PRODUCTION,
                InventorySourceType.LEGACY,
            ):
                raise ValidationError('Finished-goods sources must be production or legacy sources.')
        if self.source_type != InventorySourceType.LEGACY and self.unit_cost is None:
            raise ValidationError('Traceable inventory sources require a unit cost.')
        if self.source_type == InventorySourceType.LEGACY and self.purchase_item_id:
            raise ValidationError('Legacy sources cannot reference a purchase item.')
        if self.stock_ledger_entry_id:
            entry = self.stock_ledger_entry
            if (
                entry.movement_type != StockMovementType.IN
                or entry.raw_material_id != self.raw_material_id
                or entry.product_id != self.product_id
                or entry.reference_number != self.source_reference
                or entry.quantity != self.original_quantity
            ):
                raise ValidationError(
                    'Inventory source must match its linked stock IN ledger entry.'
                )
        if self.source_type in (
            InventorySourceType.RECEIPT,
            InventorySourceType.PRODUCTION,
        ) and not self.stock_ledger_entry_id:
            raise ValidationError('Traceable sources must link to their stock IN entry.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InventorySourceAllocation(TimeStampedModel):
    """Quantity of a ledger movement allocated to one stock source."""
    stock_ledger_entry = models.ForeignKey(
        StockLedgerEntry,
        on_delete=models.PROTECT,
        related_name='source_allocations',
    )
    source = models.ForeignKey(
        InventorySource,
        on_delete=models.PROTECT,
        related_name='allocations',
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['stock_ledger_entry', 'source'],
                name='unique_source_allocation_per_entry',
            ),
        ]

    def clean(self):
        super().clean()
        if self.stock_ledger_entry.movement_type not in (
            StockMovementType.OUT,
            StockMovementType.REVERSAL,
        ):
            raise ValidationError('Only OUT or REVERSAL movements can have source allocations.')
        if (
            self.stock_ledger_entry.raw_material_id != self.source.raw_material_id
            or self.stock_ledger_entry.product_id != self.source.product_id
        ):
            raise ValidationError('Allocation source must match the ledger stock item.')
        if self.quantity <= Decimal('0.0000'):
            raise ValidationError({'quantity': 'Allocation quantity must be positive.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
