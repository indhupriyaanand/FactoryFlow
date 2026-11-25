from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from core.models import TimeStampedModel
from masters.models import Customer, Product, UnitChoices


class SalesStatus(models.TextChoices):
    """
    Lifecycle status of a wholesale or retail sales order / dispatch invoice.
    """
    DRAFT = 'DRAFT', 'Draft'
    CONFIRMED = 'CONFIRMED', 'Confirmed'
    PARTIALLY_DISPATCHED = 'PARTIALLY_DISPATCHED', 'Partially Dispatched'
    DISPATCHED = 'DISPATCHED', 'Dispatched'
    CANCELLED = 'CANCELLED', 'Cancelled'


class SalesApprovalStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending Approval'
    APPROVED = 'APPROVED', 'Approved'
    REJECTED = 'REJECTED', 'Rejected'


class PaymentStatus(models.TextChoices):
    UNPAID = 'UNPAID', 'Unpaid'
    PARTIAL = 'PARTIAL', 'Partially Paid'
    PAID = 'PAID', 'Paid'


class ShortageStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    RESOLVED = 'RESOLVED', 'Resolved'


class SalesOrder(TimeStampedModel):
    """
    Commercial Sales Order / Wholesale Dispatch Master:
    Tracks sales to distributors and retailers (e.g. Distributor B, Retailer A),
    tax computation (GST), and inventory deduction on dispatch.
    """
    order_number = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique sales order / dispatch note reference (e.g. SO-2026-0001)."
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name='sales_orders',
        help_text="Purchasing customer / distributor."
    )
    order_date = models.DateField(
        default=timezone.now,
        help_text="Date order was placed."
    )
    dispatch_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when order was physically dispatched and inventory deducted."
    )
    status = models.CharField(
        max_length=20,
        choices=SalesStatus.choices,
        default=SalesStatus.DRAFT,
        db_index=True,
        help_text="Sales order processing status."
    )
    approval_status = models.CharField(
        max_length=10,
        choices=SalesApprovalStatus.choices,
        default=SalesApprovalStatus.PENDING,
        db_index=True,
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Subtotal amount before GST in ₹."
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Total calculated GST amount in ₹."
    )
    grand_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Final payable invoice amount including tax in ₹."
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales_orders',
        help_text="Salesman or user who booked this order."
    )
    notes = models.TextField(
        blank=True,
        help_text="Delivery address, payment terms, or transport details."
    )

    class Meta:
        verbose_name = 'Sales Order'
        verbose_name_plural = 'Sales Orders'
        ordering = ['-order_date', '-created_at']

    def clean(self):
        super().clean()
        if self.order_number:
            self.order_number = self.order_number.strip().upper()

        if self.pk:
            orig = SalesOrder.objects.get(pk=self.pk)
            # Immutability once dispatched or cancelled
            if orig.status == SalesStatus.DISPATCHED and self.status != SalesStatus.DISPATCHED:
                raise ValidationError({'status': "Dispatched sales orders cannot change status directly."})
            if orig.status == SalesStatus.CANCELLED and self.status != SalesStatus.CANCELLED:
                raise ValidationError({'status': "Cancelled sales orders cannot be reopened."})
            if orig.status in [SalesStatus.DISPATCHED, SalesStatus.CANCELLED]:
                if self.customer_id != orig.customer_id:
                    raise ValidationError({'customer': "Cannot alter customer on finalized sales orders."})

    def save(self, *args, **kwargs):
        if self.order_number:
            self.order_number = self.order_number.strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)

    def recalculate_totals(self):
        """Recomputes subtotal, tax amount, and grand total from line items using Decimal."""
        items = self.items.all()
        subtotal = sum((item.subtotal for item in items), Decimal('0.00'))
        tax = sum((item.tax_amount for item in items), Decimal('0.00'))
        self.total_amount = subtotal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        self.tax_amount = tax.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        self.grand_total = (self.total_amount + self.tax_amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        SalesOrder.objects.filter(pk=self.pk).update(
            total_amount=self.total_amount,
            tax_amount=self.tax_amount,
            grand_total=self.grand_total
        )
        return self.grand_total

    def __str__(self):
        return f"{self.order_number} - {self.customer.name} (₹{self.grand_total}) [{self.get_status_display()}]"


class SalesOrderItem(TimeStampedModel):
    """
    Individual line item in a sales order.
    """
    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.CASCADE,
        related_name='items',
        help_text="Parent sales order."
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='sales_order_items',
        help_text="Finished product being sold."
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
        help_text="Ordered quantity in packets."
    )
    dispatched_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal('0.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Selling price per packet before tax in ₹."
    )
    gst_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('5.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Applicable GST rate in percentage (e.g. 5.00 for 5%)."
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Line item subtotal before tax: quantity * unit_price."
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Line item GST amount: subtotal * (gst_rate / 100)."
    )
    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Line item total including tax: subtotal + tax_amount."
    )

    class Meta:
        verbose_name = 'Sales Order Item'
        verbose_name_plural = 'Sales Order Items'
        unique_together = ('sales_order', 'product')
        ordering = ['sales_order', 'product']

    def clean(self):
        super().clean()
        if self.quantity is not None and self.quantity <= Decimal('0.0000'):
            raise ValidationError({'quantity': "Quantity must be strictly greater than zero."})
        if self.unit_price is not None and self.unit_price < Decimal('0.00'):
            raise ValidationError({'unit_price': "Unit price cannot be negative."})
        if (
            self.dispatched_quantity is not None
            and self.quantity is not None
            and self.dispatched_quantity > self.quantity
        ):
            raise ValidationError({'dispatched_quantity': "Dispatched quantity cannot exceed ordered quantity."})
        if (
            self.discount_amount is not None
            and self.quantity is not None
            and self.discount_amount > (
                Decimal(str(self.quantity)) * Decimal(str(self.unit_price or 0))
            )
        ):
            raise ValidationError({'discount_amount': "Discount cannot exceed the line subtotal."})

        # Calculate exact amounts
        if self.quantity is not None and self.unit_price is not None:
            gross_subtotal = (Decimal(str(self.quantity)) * Decimal(str(self.unit_price))).quantize(
                Decimal('0.01'),
                rounding=ROUND_HALF_UP
            )
            self.subtotal = (gross_subtotal - self.discount_amount).quantize(
                Decimal('0.01'),
                rounding=ROUND_HALF_UP
            )
            rate = self.gst_rate if self.gst_rate is not None else Decimal('5.00')
            self.tax_amount = (self.subtotal * (rate / Decimal('100.00'))).quantize(
                Decimal('0.01'),
                rounding=ROUND_HALF_UP
            )
            self.total_price = (self.subtotal + self.tax_amount).quantize(
                Decimal('0.01'),
                rounding=ROUND_HALF_UP
            )

    def save(self, *args, **kwargs):
        # Auto-default unit_price from product if omitted
        if (self.unit_price is None or self.unit_price == Decimal('0.00')) and self.product_id:
            self.unit_price = self.product.unit_price
        if (self.gst_rate is None) and self.product_id:
            self.gst_rate = self.product.gst_rate
        self.full_clean()
        super().save(*args, **kwargs)
        if self.sales_order_id:
            self.sales_order.recalculate_totals()

    def delete(self, *args, **kwargs):
        order = self.sales_order
        super().delete(*args, **kwargs)
        if order:
            order.recalculate_totals()

    def __str__(self):
        return f"{self.product.name}: {self.quantity} pkts @ ₹{self.unit_price} = ₹{self.total_price}"


class SalesApprovalHistory(TimeStampedModel):
    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.PROTECT,
        related_name='approval_history',
    )
    approval_status = models.CharField(
        max_length=10,
        choices=SalesApprovalStatus.choices,
    )
    action_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    notes = models.TextField(blank=True)


class SalesInvoice(TimeStampedModel):
    sales_order = models.OneToOneField(
        SalesOrder,
        on_delete=models.PROTECT,
        related_name='invoice',
    )
    invoice_number = models.CharField(max_length=50, unique=True, db_index=True)
    issue_date = models.DateField(default=timezone.now)
    place_of_supply = models.CharField(max_length=50)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    cgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    sgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    igst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    payment_status = models.CharField(
        max_length=10,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )

    class Meta:
        ordering = ['-issue_date', '-created_at']

    @property
    def outstanding_amount(self):
        return (self.grand_total - self.amount_paid).quantize(Decimal('0.01'))


class SalesInvoiceLine(TimeStampedModel):
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.PROTECT, related_name='lines')
    product_name = models.CharField(max_length=150)
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)


class DispatchRecord(TimeStampedModel):
    sales_order = models.ForeignKey(
        SalesOrder, on_delete=models.PROTECT, related_name='dispatch_records'
    )
    dispatch_number = models.CharField(
        max_length=50, unique=True, db_index=True, null=True, blank=True
    )
    dispatched_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    dispatch_date = models.DateTimeField(default=timezone.now)
    vehicle_reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)


class DispatchItem(TimeStampedModel):
    dispatch = models.ForeignKey(
        DispatchRecord, on_delete=models.PROTECT, related_name='items'
    )
    sales_order_item = models.ForeignKey(
        SalesOrderItem, on_delete=models.PROTECT, related_name='dispatch_items'
    )
    stock_ledger_entry = models.OneToOneField(
        'inventory.StockLedgerEntry',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='dispatch_item',
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['dispatch', 'sales_order_item'],
                name='unique_dispatch_item_line',
            ),
        ]

    def clean(self):
        super().clean()
        if self.stock_ledger_entry_id:
            entry = self.stock_ledger_entry
            if (
                entry.movement_type != 'OUT'
                or entry.product_id != self.sales_order_item.product_id
                or entry.quantity != self.quantity
            ):
                raise ValidationError(
                    'Dispatch item must match its linked finished-goods OUT entry.'
                )


class SalesShortage(TimeStampedModel):
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='shortages')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    requested_quantity = models.DecimalField(max_digits=12, decimal_places=4)
    available_quantity = models.DecimalField(max_digits=12, decimal_places=4)
    shortage_quantity = models.DecimalField(max_digits=12, decimal_places=4)
    status = models.CharField(max_length=10, choices=ShortageStatus.choices, default=ShortageStatus.OPEN)


class Payment(TimeStampedModel):
    invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.PROTECT,
        related_name='payments',
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    payment_date = models.DateField(default=timezone.now)
    method = models.CharField(max_length=30, default='Cash')
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= Decimal('0.00'):
            raise ValidationError({'amount': 'Payment amount must be greater than zero.'})


class PaymentReversal(TimeStampedModel):
    payment = models.OneToOneField(
        Payment, on_delete=models.PROTECT, related_name='reversal'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=255)
    reversed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    reversed_at = models.DateTimeField(default=timezone.now)
