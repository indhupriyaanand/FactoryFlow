from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from masters.models import Product, RawMaterial, Customer, Supplier, BillOfMaterials, UnitChoices

class MasterModelsTests(TestCase):
    """
    Automated verification tests for Phase 2 Master Data Models:
    Product, RawMaterial, Customer, Supplier, BillOfMaterials.
    """

    def setUp(self):
        # Create benchmark sample product: Murukku 200 g
        self.product = Product.objects.create(
            name="Murukku 200 g",
            code="PRD-MUR-200G",
            packet_size="200 g",
            gst_rate=Decimal('5.00'),
            unit_price=Decimal('50.00'),
        )

        # Create raw materials
        self.flour = RawMaterial.objects.create(
            name="Flour",
            code="RM-FLOUR",
            unit=UnitChoices.KG,
            standard_rate=Decimal('42.00')
        )
        self.oil = RawMaterial.objects.create(
            name="Cooking oil",
            code="RM-OIL",
            unit=UnitChoices.LITRE,
            standard_rate=Decimal('140.00')
        )

    # 1. Product Tests
    def test_product_creation_and_attributes(self):
        """Verify product fields, uppercase code, and string representation."""
        self.assertEqual(self.product.code, "PRD-MUR-200G")
        self.assertEqual(self.product.gst_rate, Decimal('5.00'))
        self.assertEqual(self.product.unit_price, Decimal('50.00'))
        self.assertTrue(self.product.is_active)
        self.assertIn("Murukku 200 g", str(self.product))

    def test_product_code_uniqueness(self):
        """Verify that duplicate product SKU/code is rejected."""
        with self.assertRaises(Exception):
            Product.objects.create(
                name="Duplicate Murukku",
                code="PRD-MUR-200G",  # Duplicate code
                packet_size="200 g",
            )

    def test_product_negative_gst_validation(self):
        """Verify that negative GST rate raises ValidationError."""
        product = Product(
            name="Invalid Product",
            code="PRD-INV",
            gst_rate=Decimal('-5.00')
        )
        with self.assertRaises(ValidationError):
            product.full_clean()

    # 2. Raw Material Tests
    def test_raw_material_creation(self):
        """Verify raw material attributes and string representation."""
        self.assertEqual(self.flour.code, "RM-FLOUR")
        self.assertEqual(self.flour.unit, UnitChoices.KG)
        self.assertEqual(self.flour.standard_rate, Decimal('42.00'))
        self.assertIn("Flour", str(self.flour))

    def test_raw_material_code_uniqueness(self):
        """Verify raw material code uniqueness."""
        with self.assertRaises(Exception):
            RawMaterial.objects.create(
                name="Another Flour",
                code="RM-FLOUR",
                unit=UnitChoices.KG
            )

    def test_raw_material_negative_rate_validation(self):
        """Verify negative standard rate is rejected."""
        rm = RawMaterial(
            name="Negative Rate Material",
            code="RM-NEG",
            standard_rate=Decimal('-10.00')
        )
        with self.assertRaises(ValidationError):
            rm.full_clean()

    # 3. Customer Tests
    def test_customer_creation_and_state(self):
        """Test customer creation for Retailer A (Tamil Nadu) and Distributor B (Karnataka)."""
        retailer_a = Customer.objects.create(
            name="Retailer A",
            contact_number="9876543210",
            address="12 Main Bazaar, Madurai",
            state="Tamil Nadu",
            state_code="33",
            gstin="33ABCDE1234F1Z5"
        )
        distributor_b = Customer.objects.create(
            name="Distributor B",
            contact_number="9123456780",
            address="45 Industrial Area, Bengaluru",
            state="Karnataka",
            state_code="29",
            gstin="29XYZAB5678C1Z2"
        )
        self.assertEqual(retailer_a.state, "Tamil Nadu")
        self.assertEqual(distributor_b.state, "Karnataka")
        self.assertTrue(retailer_a.is_active)
        self.assertIn("Retailer A", str(retailer_a))

    def test_customer_invalid_gstin_length(self):
        """Verify that GSTIN with invalid character count raises ValidationError."""
        cust = Customer(
            name="Bad GST Customer",
            contact_number="1234567890",
            address="Somewhere",
            state="Tamil Nadu",
            gstin="SHORTGST"  # Not 15 chars
        )
        with self.assertRaises(ValidationError):
            cust.full_clean()

    # 4. Supplier Tests
    def test_supplier_creation(self):
        """Test supplier master creation and string representation."""
        supplier = Supplier.objects.create(
            name="Sri Krishna Flour Mills",
            contact_number="9988776655",
            address="10 Grain Market, Trichy",
            state="Tamil Nadu",
            state_code="33",
            gstin="33KLMNO9988P1Z3"
        )
        self.assertEqual(supplier.name, "Sri Krishna Flour Mills")
        self.assertTrue(supplier.is_active)
        self.assertIn("Sri Krishna Flour Mills", str(supplier))

    # 5. Bill of Materials (BOM) Tests
    def test_bom_creation_and_calculation(self):
        """Test standard BOM creation: 40kg Flour for 200 packets Murukku."""
        bom_flour = BillOfMaterials.objects.create(
            product=self.product,
            raw_material=self.flour,
            quantity=Decimal('40.0000'),
            unit=UnitChoices.KG,
            batch_size=Decimal('200.00')
        )
        self.assertEqual(bom_flour.quantity, Decimal('40.0000'))
        self.assertEqual(bom_flour.batch_size, Decimal('200.00'))
        # 40 kg / 200 packets = 0.20 kg per packet
        self.assertEqual(bom_flour.quantity_per_packet, Decimal('0.2000'))
        self.assertIn("Murukku 200 g", str(bom_flour))

    def test_bom_unique_product_material_constraint(self):
        """Verify that the same material cannot be duplicated in a product's BOM."""
        BillOfMaterials.objects.create(
            product=self.product,
            raw_material=self.flour,
            quantity=Decimal('40.0000'),
            unit=UnitChoices.KG,
            batch_size=Decimal('200.00')
        )
        # Attempt to add Flour again to the same Murukku product
        with self.assertRaises(Exception):
            BillOfMaterials.objects.create(
                product=self.product,
                raw_material=self.flour,
                quantity=Decimal('10.0000'),
                unit=UnitChoices.KG,
                batch_size=Decimal('200.00')
            )

    def test_bom_zero_or_negative_quantity_validation(self):
        """Verify that zero or negative BOM quantity is rejected."""
        bom = BillOfMaterials(
            product=self.product,
            raw_material=self.oil,
            quantity=Decimal('0.0000'),
            unit=UnitChoices.LITRE,
            batch_size=Decimal('200.00')
        )
        with self.assertRaises(ValidationError):
            bom.full_clean()
