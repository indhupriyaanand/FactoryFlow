import re
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from core.models import TimeStampedModel


class UnitChoices(models.TextChoices):
    """
    Standard Units of Measurement (UOM) for manufacturing operations.
    """
    KG = 'kg', 'Kilogram (kg)'
    LITRE = 'litre', 'Litre (L)'
    PIECES = 'pieces', 'Pieces (pcs)'
    CARTON = 'carton', 'Carton'
    PACKET = 'packet', 'Packet'
    GRAM = 'gram', 'Gram (g)'


class Product(TimeStampedModel):
    """
    Finished Goods Master (e.g. Murukku 200 g).
    Does NOT store overwritten stock balance (stock is ledger-calculated).
    """
    name = models.CharField(
        max_length=150,
        help_text="Full commercial name of the finished snack product."
    )
    code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique Product SKU / Code (e.g. PRD-MUR-200G)."
    )
    packet_size = models.CharField(
        max_length=50,
        default="200 g",
        help_text="Finished packet packaging specification (e.g. 200 g, 500 g)."
    )
    gst_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('5.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Applicable GST rate in percentage (e.g. 5.00 for 5%)."
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('50.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Standard selling price per packet before tax (₹)."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Active status for manufacturing and sales catalog."
    )

    class Meta:
        verbose_name = 'Finished Good / Product'
        verbose_name_plural = 'Finished Goods / Products'
        ordering = ['name']

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().upper()
        if self.gst_rate is not None and self.gst_rate < Decimal('0.00'):
            raise ValidationError({'gst_rate': "GST percentage cannot be negative."})
        if self.unit_price is not None and self.unit_price < Decimal('0.00'):
            raise ValidationError({'unit_price': "Unit selling price cannot be negative."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code}) - {self.packet_size}"


class RawMaterial(TimeStampedModel):
    """
    Raw Material & Packaging Item Master (e.g. Flour, Oil, Pouches, Cartons).
    Strictly NO overwritten inventory balance stored here.
    """
    name = models.CharField(
        max_length=150,
        help_text="Material name (e.g. Flour, Cooking oil, 200g pouches)."
    )
    code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique raw material item code (e.g. RM-FLOUR, PKG-POUCH-200)."
    )
    unit = models.CharField(
        max_length=20,
        choices=UnitChoices.choices,
        default=UnitChoices.KG,
        help_text="Primary unit of measurement."
    )
    standard_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Standard opening procurement rate per unit in ₹."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Active status for purchasing and BOM inclusion."
    )

    class Meta:
        verbose_name = 'Raw Material & Packaging'
        verbose_name_plural = 'Raw Materials & Packaging'
        ordering = ['name']

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().upper()
        if self.standard_rate is not None and self.standard_rate < Decimal('0.00'):
            raise ValidationError({'standard_rate': "Standard rate cannot be negative."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code}) [{self.get_unit_display()}]"


class Customer(TimeStampedModel):
    """
    Customer Master (Retailers & Distributors).
    State is mandatory for GST destination-based tax computation (Intra-State vs Inter-State).
    """
    name = models.CharField(
        max_length=200,
        help_text="Registered business or entity name of the customer."
    )
    contact_number = models.CharField(
        max_length=20,
        help_text="Phone number for orders and dispatch confirmation."
    )
    address = models.TextField(
        help_text="Delivery and billing address."
    )
    state = models.CharField(
        max_length=50,
        default="Tamil Nadu",
        help_text="State name (e.g. Tamil Nadu, Karnataka). Critical for GST determination."
    )
    state_code = models.CharField(
        max_length=2,
        blank=True,
        help_text="2-digit GST state code (e.g. 33 for Tamil Nadu, 29 for Karnataka)."
    )
    gstin = models.CharField(
        max_length=15,
        blank=True,
        help_text="15-character GSTIN (optional for unregistered retailers)."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Active customer status."
    )

    class Meta:
        verbose_name = 'Customer'
        verbose_name_plural = 'Customers'
        ordering = ['name']

    def clean(self):
        super().clean()
        if self.gstin:
            cleaned_gst = self.gstin.strip().upper()
            if len(cleaned_gst) != 15:
                raise ValidationError({'gstin': "GSTIN must be exactly 15 characters long if provided."})
            self.gstin = cleaned_gst
            if not self.state_code and cleaned_gst[:2].isdigit():
                self.state_code = cleaned_gst[:2]

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} - {self.state}"


class Supplier(TimeStampedModel):
    """
    Vendor / Supplier Master for raw materials and packaging materials.
    """
    name = models.CharField(
        max_length=200,
        help_text="Supplier business or trader name."
    )
    contact_number = models.CharField(
        max_length=20,
        help_text="Supplier phone number."
    )
    address = models.TextField(
        help_text="Supplier registered warehouse or office address."
    )
    state = models.CharField(
        max_length=50,
        default="Tamil Nadu",
        help_text="State location of supplier."
    )
    state_code = models.CharField(
        max_length=2,
        blank=True,
        help_text="2-digit GST state code."
    )
    gstin = models.CharField(
        max_length=15,
        blank=True,
        help_text="15-character Supplier GSTIN (optional)."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Active supplier status."
    )

    class Meta:
        verbose_name = 'Supplier'
        verbose_name_plural = 'Suppliers'
        ordering = ['name']

    def clean(self):
        super().clean()
        if self.gstin:
            cleaned_gst = self.gstin.strip().upper()
            if len(cleaned_gst) != 15:
                raise ValidationError({'gstin': "GSTIN must be exactly 15 characters long if provided."})
            self.gstin = cleaned_gst
            if not self.state_code and cleaned_gst[:2].isdigit():
                self.state_code = cleaned_gst[:2]

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.state})"


class BillOfMaterials(TimeStampedModel):
    """
    Standard Bill of Materials (BOM) Recipe per Finished Product.
    Defines standard raw materials and quantities required for a batch.
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='bom_items',
        help_text="The finished product manufactured."
    )
    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.CASCADE,
        related_name='bom_usages',
        help_text="Required raw material or packaging component."
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
        help_text="Standard planned quantity required for standard batch size."
    )
    unit = models.CharField(
        max_length=20,
        choices=UnitChoices.choices,
        help_text="Unit of measurement for the planned quantity."
    )
    batch_size = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('200.00'),
        validators=[MinValueValidator(Decimal('1.00'))],
        help_text="Expected finished output batch size (e.g. 200 packets)."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this BOM item is currently active in the recipe."
    )

    class Meta:
        verbose_name = 'Bill of Materials (BOM) Item'
        verbose_name_plural = 'Bill of Materials (BOM) Items'
        unique_together = ('product', 'raw_material')
        ordering = ['product', 'raw_material']

    def clean(self):
        super().clean()
        if self.quantity is not None and self.quantity <= Decimal('0.0000'):
            raise ValidationError({'quantity': "Planned BOM quantity must be greater than zero."})
        if self.batch_size is not None and self.batch_size <= Decimal('0.00'):
            raise ValidationError({'batch_size': "Batch size must be greater than zero."})

    def save(self, *args, **kwargs):
        # Default unit to raw material unit if not provided
        if not self.unit and self.raw_material:
            self.unit = self.raw_material.unit
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def quantity_per_packet(self):
        """Standard quantity consumed per individual packet produced."""
        if self.batch_size and self.batch_size > Decimal('0'):
            return self.quantity / self.batch_size
        return Decimal('0.0000')

    def __str__(self):
        return f"{self.product.name} <- {self.quantity} {self.get_unit_display()} {self.raw_material.name} (for {self.batch_size} pkts)"
