from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from core.models import TimeStampedModel
from masters.models import Product, RawMaterial, UnitChoices


class ProductionStatus(models.TextChoices):
    """
    Lifecycle status of a manufacturing production order / batch run.
    """
    DRAFT = 'DRAFT', 'Draft'
    PLANNED = 'PLANNED', 'Planned'
    IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
    COMPLETED = 'COMPLETED', 'Completed'
    CANCELLED = 'CANCELLED', 'Cancelled'


class ProductionApprovalStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending Approval'
    APPROVED = 'APPROVED', 'Approved'
    REJECTED = 'REJECTED', 'Rejected'


class ProductionDamageType(models.TextChoices):
    DAMAGED_POUCH = 'DAMAGED_POUCH', 'Damaged pouch'
    SCRAP = 'SCRAP', 'Scrap'


class ProductionOrder(TimeStampedModel):
    """
    Manufacturing Production Batch Order:
    Tracks batch production runs of finished goods (e.g. 200 packets of Murukku 200g),
    material consumption from BOM, and output generation.
    """
    order_number = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique production order / batch identifier (e.g. PRD-BATCH-2026-001)."
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='production_orders',
        help_text="Finished product being manufactured."
    )
    target_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
        help_text="Target output quantity in packets (e.g. 200.0000)."
    )
    produced_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal('0.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
        help_text="Actual finished output quantity recorded upon batch completion."
    )
    status = models.CharField(
        max_length=20,
        choices=ProductionStatus.choices,
        default=ProductionStatus.DRAFT,
        db_index=True,
        help_text="Lifecycle status of this production run."
    )
    start_date = models.DateField(
        default=timezone.now,
        help_text="Date production was scheduled or initiated."
    )
    completion_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when production batch was completed and stock credited."
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='production_orders',
        help_text="User / Supervisor who initiated this production order."
    )
    notes = models.TextField(
        blank=True,
        help_text="Operational remarks, shift notes, or batch observations."
    )
    approval_status = models.CharField(
        max_length=20,
        choices=ProductionApprovalStatus.choices,
        default=ProductionApprovalStatus.PENDING,
        db_index=True,
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_production_orders',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    damaged_pouch_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal('0.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
    )
    labour_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    power_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    material_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal('0.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
    )

    class Meta:
        verbose_name = 'Production Order'
        verbose_name_plural = 'Production Orders'
        ordering = ['-start_date', '-created_at']

    def clean(self):
        super().clean()
        if self.order_number:
            self.order_number = self.order_number.strip().upper()

        if self.target_quantity is not None and self.target_quantity <= Decimal('0.0000'):
            raise ValidationError({'target_quantity': "Target quantity must be strictly greater than zero."})

        if self.pk:
            orig = ProductionOrder.objects.get(pk=self.pk)
            # Immutability once completed or cancelled
            if orig.status == ProductionStatus.COMPLETED and self.status != ProductionStatus.COMPLETED:
                raise ValidationError({'status': "Completed production orders are finalized and cannot change status."})
            if orig.status == ProductionStatus.CANCELLED and self.status != ProductionStatus.CANCELLED:
                raise ValidationError({'status': "Cancelled production orders cannot be reopened."})
            if orig.status in [ProductionStatus.COMPLETED, ProductionStatus.CANCELLED]:
                if self.product_id != orig.product_id:
                    raise ValidationError({'product': "Cannot change product of a finalized production order."})
                if self.target_quantity != orig.target_quantity:
                    raise ValidationError({'target_quantity': "Cannot change target quantity of a finalized production order."})
                if self.order_number != orig.order_number:
                    raise ValidationError({'order_number': "Cannot change the number of a finalized production order."})

    def save(self, *args, **kwargs):
        if self.order_number:
            self.order_number = self.order_number.strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order_number} - {self.product.name} ({self.target_quantity} pkts) [{self.get_status_display()}]"

    @property
    def yield_variance_quantity(self):
        return self.produced_quantity - self.target_quantity

    @property
    def yield_variance_percent(self):
        if not self.target_quantity:
            return Decimal('0.0000')
        return ((self.yield_variance_quantity / self.target_quantity) * Decimal('100')).quantize(
            Decimal('0.0001')
        )

    @property
    def material_variance_total(self):
        return sum(
            (usage.variance_quantity for usage in self.material_usages.all()),
            Decimal('0.0000'),
        ).quantize(Decimal('0.0001'))

    @property
    def oil_variance_quantity(self):
        return sum(
            (
                usage.variance_quantity
                for usage in self.material_usages.all()
                if usage.raw_material.is_oil
            ),
            Decimal('0.0000'),
        ).quantize(Decimal('0.0001'))


class ProductionMaterialUsage(TimeStampedModel):
    """
    Line item specifying planned and actual material consumption for a production order.
    """
    production_order = models.ForeignKey(
        ProductionOrder,
        on_delete=models.CASCADE,
        related_name='material_usages',
        help_text="Parent production order."
    )
    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        related_name='production_usages',
        help_text="Required raw material or packaging component."
    )
    planned_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
        help_text="Calculated planned quantity based on BOM recipe."
    )
    planned_unit_rate = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.0000'))],
        help_text="Unit-cost snapshot captured when the production plan is created.",
    )
    actual_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal('0.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
        help_text="Actual quantity consumed upon batch completion."
    )
    unit = models.CharField(
        max_length=20,
        choices=UnitChoices.choices,
        help_text="Unit of measurement."
    )

    class Meta:
        verbose_name = 'Production Material Usage'
        verbose_name_plural = 'Production Material Usages'
        unique_together = ('production_order', 'raw_material')
        ordering = ['production_order', 'raw_material']

    def clean(self):
        super().clean()
        if self.raw_material and self.unit:
            if self.unit != self.raw_material.unit:
                raise ValidationError({'unit': f"Usage unit '{self.unit}' does not match standard unit '{self.raw_material.unit}'."})

    def save(self, *args, **kwargs):
        if not self.unit and self.raw_material:
            self.unit = self.raw_material.unit
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.production_order.order_number}: {self.raw_material.name} ({self.planned_quantity} {self.unit})"

    @property
    def variance_quantity(self):
        return (
            self.actual_consumed_quantity - self.planned_quantity
        ).quantize(Decimal('0.0001'))

    @property
    def actual_consumed_quantity(self):
        damaged_quantity = sum(
            self.production_order.damage_records.filter(
                raw_material_id=self.raw_material_id,
                damage_type=ProductionDamageType.DAMAGED_POUCH,
                is_voided=False,
                stock_ledger_entry__reference_number=(
                    f'{self.production_order.order_number}-DAMAGE'
                ),
            ).values_list('quantity', flat=True),
            Decimal('0.0000'),
        )
        return (self.actual_quantity + damaged_quantity).quantize(
            Decimal('0.0001')
        )

    @property
    def planned_cost(self):
        if self.planned_unit_rate is None:
            return None
        return (self.planned_quantity * self.planned_unit_rate).quantize(
            Decimal('0.01'),
            rounding=ROUND_HALF_UP,
        )


class ProductionApprovalHistory(TimeStampedModel):
    production_order = models.ForeignKey(
        ProductionOrder, on_delete=models.CASCADE, related_name='approval_history'
    )
    approval_status = models.CharField(max_length=20, choices=ProductionApprovalStatus.choices)
    action_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    notes = models.TextField(blank=True)


class ProductionDamageRecord(TimeStampedModel):
    production_order = models.ForeignKey(
        ProductionOrder, on_delete=models.CASCADE, related_name='damage_records'
    )
    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='production_damage_records',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='production_damage_records',
    )
    damage_type = models.CharField(max_length=20, choices=ProductionDamageType.choices)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
    )
    unit = models.CharField(max_length=20, choices=UnitChoices.choices)
    reason = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    recorded_at = models.DateTimeField(default=timezone.now)
    stock_ledger_entry = models.OneToOneField(
        'inventory.StockLedgerEntry',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='damage_record',
    )
    is_voided = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='voided_damage_records',
    )
    void_reason = models.CharField(max_length=255, blank=True)

    def clean(self):
        super().clean()
        if self.quantity <= Decimal('0.0000'):
            raise ValidationError({'quantity': 'Damage quantity must be greater than zero.'})
        legacy_unlinked = (
            self.pk
            and not self.raw_material_id
            and not self.product_id
            and not self.stock_ledger_entry_id
        )
        if not legacy_unlinked and bool(self.raw_material_id) == bool(self.product_id):
            raise ValidationError('A damage record must reference exactly one raw material or product.')
        if self.raw_material_id and self.unit != self.raw_material.unit:
            raise ValidationError({'unit': 'Damage unit must match the raw material unit.'})
        if self.product_id and self.unit not in (UnitChoices.PACKET, UnitChoices.PIECES):
            raise ValidationError({'unit': 'Product damage must use packets or pieces.'})
        if self.damage_type == ProductionDamageType.DAMAGED_POUCH and not legacy_unlinked:
            if not self.raw_material_id or not self.raw_material.is_pouch:
                raise ValidationError('Damaged pouch records must reference a pouch raw material.')
        if self.is_voided and not self.voided_at:
            raise ValidationError('Voided damage records require a void timestamp.')
