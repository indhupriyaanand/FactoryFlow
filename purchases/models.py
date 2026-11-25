from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from core.models import TimeStampedModel
from masters.models import RawMaterial, Supplier, UnitChoices


class PurchaseStatus(models.TextChoices):
    """
    Lifecycle stages of a purchase invoice.
    Stock ledger entries are created ONLY upon reaching RECEIVED status.
    """
    DRAFT = 'DRAFT', 'Draft'
    RECEIVED = 'RECEIVED', 'Received'
    CANCELLED = 'CANCELLED', 'Cancelled'


class PurchaseInvoice(TimeStampedModel):
    """
    Purchase Invoice Master:
    Records procurement orders from vendors with supplier details, invoice reference,
    and aggregate financial amounts.
    """
    invoice_number = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique purchase invoice / bill reference (e.g. PUR-2026-001 or vendor invoice number)."
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name='purchase_invoices',
        help_text="Registered vendor providing the raw materials."
    )
    purchase_date = models.DateField(
        default=timezone.now,
        help_text="Date when materials were procured / invoiced."
    )
    status = models.CharField(
        max_length=20,
        choices=PurchaseStatus.choices,
        default=PurchaseStatus.DRAFT,
        db_index=True,
        help_text="Invoice lifecycle status. Only 'RECEIVED' status generates stock ledger entries."
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Aggregated total invoice amount in ₹ (sum of all line items)."
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_purchases',
        help_text="User who initiated this purchase entry."
    )
    notes = models.TextField(
        blank=True,
        help_text="Optional remarks, payment terms, or delivery notes."
    )

    class Meta:
        verbose_name = 'Purchase Invoice'
        verbose_name_plural = 'Purchase Invoices'
        ordering = ['-purchase_date', '-created_at']

    def clean(self):
        super().clean()
        if self.invoice_number:
            self.invoice_number = self.invoice_number.strip().upper()

        if self.pk:
            original = PurchaseInvoice.objects.get(pk=self.pk)

            # 1. State transition restrictions
            # DRAFT -> RECEIVED is blocked via model save()
            if original.status == PurchaseStatus.DRAFT and self.status == PurchaseStatus.RECEIVED:
                raise ValidationError({
                    'status': "Direct transition from DRAFT to RECEIVED is not allowed via model save. "
                              "Invoices must be received through the authorized Inventory Receiving Service."
                })

            # RECEIVED -> CANCELLED is blocked via model save()
            if original.status == PurchaseStatus.RECEIVED and self.status == PurchaseStatus.CANCELLED:
                raise ValidationError({
                    'status': "Direct transition from RECEIVED to CANCELLED is not allowed via model save. "
                              "Invoices must be cancelled through the authorized Inventory Cancellation Service."
                })

            # RECEIVED -> DRAFT is always blocked
            if original.status == PurchaseStatus.RECEIVED and self.status == PurchaseStatus.DRAFT:
                raise ValidationError({
                    'status': "Received purchase invoices cannot be reverted to draft."
                })

            # CANCELLED -> any other status is blocked
            if original.status == PurchaseStatus.CANCELLED and self.status != PurchaseStatus.CANCELLED:
                raise ValidationError({
                    'status': "Cancelled purchase invoices are final and cannot be reopened."
                })

            # 2. Prevent core field modifications once RECEIVED or CANCELLED
            if original.status in [PurchaseStatus.RECEIVED, PurchaseStatus.CANCELLED]:
                if self.supplier_id != original.supplier_id:
                    raise ValidationError({
                        'supplier': f"Cannot modify supplier on an invoice that is already {original.get_status_display().lower()}."
                    })
                if self.invoice_number != original.invoice_number:
                    raise ValidationError({
                        'invoice_number': f"Cannot modify invoice number on an invoice that is already {original.get_status_display().lower()}."
                    })
                if self.purchase_date != original.purchase_date:
                    raise ValidationError({
                        'purchase_date': f"Cannot modify purchase date on an invoice that is already {original.get_status_display().lower()}."
                    })
        else:
            # On creation, disallow creating directly as RECEIVED
            if self.status == PurchaseStatus.RECEIVED:
                raise ValidationError({
                    'status': "New purchase invoices must be created in DRAFT status. "
                              "They cannot be created directly as RECEIVED."
                })

    def save(self, *args, **kwargs):
        if self.invoice_number:
            self.invoice_number = self.invoice_number.strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)

    def recalculate_total(self):
        """
        Calculates and updates total_amount using Decimal precision.
        """
        line_sum = sum(
            (item.total_price for item in self.items.all()),
            Decimal('0.00')
        )
        self.total_amount = line_sum.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        PurchaseInvoice.objects.filter(pk=self.pk).update(total_amount=self.total_amount)
        return self.total_amount

    def __str__(self):
        return f"{self.invoice_number} - {self.supplier.name} (₹{self.total_amount}) [{self.get_status_display()}]"


class PurchaseItem(TimeStampedModel):
    """
    Individual Line Item within a Purchase Invoice:
    Specifies raw material, quantity, unit, and unit rate.
    """
    purchase = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.CASCADE,
        related_name='items',
        help_text="Parent purchase invoice."
    )
    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        related_name='purchase_items',
        help_text="Procured raw material or packaging component."
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
        help_text="Procured quantity (must be strictly positive)."
    )
    unit = models.CharField(
        max_length=20,
        choices=UnitChoices.choices,
        blank=True,
        help_text="Unit of measurement. Defaults to the raw material's defined unit."
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Procurement cost per unit before tax (₹)."
    )
    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Calculated line item total: quantity * unit_price (₹)."
    )

    class Meta:
        verbose_name = 'Purchase Item'
        verbose_name_plural = 'Purchase Items'
        unique_together = ('purchase', 'raw_material')
        ordering = ['purchase', 'raw_material']

    def clean(self):
        super().clean()
        # 1. Validate quantity > 0
        if self.quantity is not None and self.quantity <= Decimal('0.0000'):
            raise ValidationError({'quantity': "Purchase quantity must be strictly greater than zero."})

        # 2. Validate unit price >= 0
        if self.unit_price is not None and self.unit_price < Decimal('0.00'):
            raise ValidationError({'unit_price': "Unit price cannot be negative."})

        # 3. Unit validation against RawMaterial
        if self.raw_material:
            if not self.unit:
                self.unit = self.raw_material.unit
            elif self.unit != self.raw_material.unit:
                raise ValidationError({
                    'unit': f"Purchase unit '{self.unit}' does not match Raw Material standard unit '{self.raw_material.unit}' for '{self.raw_material.name}'."
                })

        # 4. Prevent purchase item reassignment (FIX 2)
        if self.pk:
            orig = PurchaseItem.objects.get(pk=self.pk)
            if self.purchase_id != orig.purchase_id:
                raise ValidationError({
                    'purchase': "Reassigning a purchase item to a different invoice is strictly prohibited."
                })

        # 5. Status protection: Disallow modifying line items of received/cancelled invoices
        if self.purchase_id and hasattr(self, 'purchase'):
            if self.purchase.status in [PurchaseStatus.RECEIVED, PurchaseStatus.CANCELLED]:
                # Allow initial save if pk is None and status is DRAFT, but not if purchase is already locked
                if not self.pk or self.purchase.status != PurchaseStatus.DRAFT:
                    raise ValidationError(
                        f"Cannot add or modify items on a purchase invoice that is already {self.purchase.get_status_display().lower()}."
                    )

        # 5. Exact Decimal calculation for line total
        if self.quantity is not None and self.unit_price is not None:
            self.total_price = (Decimal(str(self.quantity)) * Decimal(str(self.unit_price))).quantize(
                Decimal('0.01'),
                rounding=ROUND_HALF_UP
            )

    def save(self, *args, **kwargs):
        # Auto-default unit to raw material unit before full_clean runs
        if not self.unit and self.raw_material_id:
            self.unit = self.raw_material.unit
        self.full_clean()
        super().save(*args, **kwargs)
        # Keep parent invoice total up to date
        if self.purchase_id:
            self.purchase.recalculate_total()

    def delete(self, *args, **kwargs):
        if self.purchase and self.purchase.status in [PurchaseStatus.RECEIVED, PurchaseStatus.CANCELLED]:
            raise ValidationError(
                f"Cannot delete items from a purchase invoice that is already {self.purchase.get_status_display().lower()}."
            )
        purchase = self.purchase
        super().delete(*args, **kwargs)
        if purchase:
            purchase.recalculate_total()

    def __str__(self):
        return f"{self.raw_material.name}: {self.quantity} {self.unit} @ ₹{self.unit_price}/unit = ₹{self.total_price}"
