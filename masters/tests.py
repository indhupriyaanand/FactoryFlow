import io
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from accounts.models import Role
from accounts.roles import setup_role_groups
from masters.models import Product, RawMaterial, Customer, Supplier, BillOfMaterials, UnitChoices

class MasterModelsAndBenchmarkSeedTests(TestCase):
    """
    Automated verification tests for Phase 3:
    - Master models validation and constraints
    - BOM calculations per packet
    - Idempotency of seed_benchmark_data command
    - Role-based permissions on masters
    """

    def setUp(self):
        setup_role_groups()

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

    # 1. Product Master Tests
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
                code="PRD-MUR-200G",
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

    # 2. Raw Material Master Tests
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

    # 3. Customer & Supplier Tests
    def test_customer_creation_and_state(self):
        """Test customer creation for Retailer A (Tamil Nadu) and Distributor B (Karnataka)."""
        retailer_a = Customer.objects.create(
            name="Retailer A",
            contact_number="9876543210",
            address="12 Main Bazaar, Madurai",
            state="Tamil Nadu",
            state_code="33",
            gstin="33AAAAA0000A1Z5"
        )
        distributor_b = Customer.objects.create(
            name="Distributor B",
            contact_number="9123456780",
            address="45 Industrial Area, Bengaluru",
            state="Karnataka",
            state_code="29",
            gstin="29BBBBB0000B1Z6"
        )
        self.assertEqual(retailer_a.state, "Tamil Nadu")
        self.assertEqual(retailer_a.state_code, "33")
        self.assertEqual(distributor_b.state, "Karnataka")
        self.assertEqual(distributor_b.state_code, "29")
        self.assertTrue(retailer_a.is_active)
        self.assertIn("Retailer A", str(retailer_a))

    def test_supplier_creation(self):
        """Test supplier master creation and string representation."""
        supplier = Supplier.objects.create(
            name="Sample Raw Material Supplier",
            contact_number="9443322110",
            address="Agro Trading Complex, NH-45, Trichy",
            state="Tamil Nadu",
            state_code="33",
            gstin="33CCCCC0000C1Z7"
        )
        self.assertEqual(supplier.name, "Sample Raw Material Supplier")
        self.assertEqual(supplier.state_code, "33")
        self.assertTrue(supplier.is_active)

    # 4. Bill of Materials (BOM) Calculation Tests
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

    def test_bom_unique_product_material_constraint(self):
        """Verify that the same material cannot be duplicated in a product's BOM."""
        BillOfMaterials.objects.create(
            product=self.product,
            raw_material=self.flour,
            quantity=Decimal('40.0000'),
            unit=UnitChoices.KG,
            batch_size=Decimal('200.00')
        )
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

    # 5. Seed Command Idempotency Tests
    def test_seed_benchmark_data_command_idempotency(self):
        """
        Verify that running seed_benchmark_data populates exact records,
        and running it a second time does NOT create any duplicate records.
        """
        out = io.StringIO()
        call_command('seed_benchmark_data', stdout=out)

        # Verify initial counts
        product = Product.objects.get(code="PRD-MUR-200G")
        self.assertEqual(product.name, "Murukku 200g")
        self.assertEqual(product.unit_price, Decimal('50.00'))

        self.assertEqual(RawMaterial.objects.filter(code__in=[
            "RM-FLOUR", "RM-OIL", "RM-SPICE", "RM-POUCH", "RM-CARTON"
        ]).count(), 5)

        self.assertEqual(Customer.objects.filter(name__in=["Retailer A", "Distributor B"]).count(), 2)
        self.assertTrue(Supplier.objects.filter(name="Sample Raw Material Supplier").exists())

        # Check BOM items count
        bom_items = BillOfMaterials.objects.filter(product=product)
        self.assertEqual(bom_items.count(), 5)

        # Check BOM per-packet exact formulas:
        # Flour: 40 / 200 = 0.20 kg
        flour_bom = bom_items.get(raw_material__code="RM-FLOUR")
        self.assertEqual(flour_bom.quantity, Decimal('40.0000'))
        self.assertEqual(flour_bom.quantity_per_packet, Decimal('0.2000'))

        # Oil: 10 / 200 = 0.05 litre
        oil_bom = bom_items.get(raw_material__code="RM-OIL")
        self.assertEqual(oil_bom.quantity, Decimal('10.0000'))
        self.assertEqual(oil_bom.quantity_per_packet, Decimal('0.0500'))

        # Spice: 1.2 / 200 = 0.006 kg
        spice_bom = bom_items.get(raw_material__code="RM-SPICE")
        self.assertEqual(spice_bom.quantity, Decimal('1.2000'))
        self.assertEqual(spice_bom.quantity_per_packet, Decimal('0.0060'))

        # Pouches: 200 / 200 = 1.0 piece
        pouch_bom = bom_items.get(raw_material__code="RM-POUCH")
        self.assertEqual(pouch_bom.quantity, Decimal('200.0000'))
        self.assertEqual(pouch_bom.quantity_per_packet, Decimal('1.0000'))

        # Cartons: 10 / 200 = 0.05 piece
        carton_bom = bom_items.get(raw_material__code="RM-CARTON")
        self.assertEqual(carton_bom.quantity, Decimal('10.0000'))
        self.assertEqual(carton_bom.quantity_per_packet, Decimal('0.0500'))

        # Record counts before second run
        prod_count_1 = Product.objects.count()
        rm_count_1 = RawMaterial.objects.count()
        cust_count_1 = Customer.objects.count()
        supp_count_1 = Supplier.objects.count()
        bom_count_1 = BillOfMaterials.objects.count()

        # RUN SEED COMMAND A SECOND TIME (Verify Idempotency)
        call_command('seed_benchmark_data', stdout=out)

        self.assertEqual(Product.objects.count(), prod_count_1)
        self.assertEqual(RawMaterial.objects.count(), rm_count_1)
        self.assertEqual(Customer.objects.count(), cust_count_1)
        self.assertEqual(Supplier.objects.count(), supp_count_1)
        self.assertEqual(BillOfMaterials.objects.count(), bom_count_1)

    # 6. Role Permissions on Masters
    def test_role_permissions_on_masters(self):
        """Verify supervisor can manage BOM, accountant is read-only, salesman can't change BOM."""
        # Supervisor
        sup = User.objects.create_user(username='bom_sup', password='Password123!')
        sup.profile.role = Role.PRODUCTION_SUPERVISOR
        sup.profile.save()
        sup = User.objects.get(pk=sup.pk)
        self.assertTrue(sup.has_perm('masters.add_billofmaterials'))
        self.assertTrue(sup.has_perm('masters.change_billofmaterials'))

        # Accountant
        acct = User.objects.create_user(username='bom_acct', password='Password123!')
        acct.profile.role = Role.ACCOUNTANT
        acct.profile.save()
        acct = User.objects.get(pk=acct.pk)
        self.assertTrue(acct.has_perm('masters.view_billofmaterials'))
        self.assertFalse(acct.has_perm('masters.add_billofmaterials'))

        # Salesman
        sales = User.objects.create_user(username='bom_sales', password='Password123!')
        sales.profile.role = Role.SALESMAN
        sales.profile.save()
        sales = User.objects.get(pk=sales.pk)
        self.assertFalse(sales.has_perm('masters.add_billofmaterials'))

    # 7. Admin Display and Changelist View Tests
    def test_bom_admin_per_packet_display_formatting(self):
        """
        Verify that BillOfMaterialsAdmin.per_packet_display formats properly
        without raising ValueError: Unknown format code 'f' for object of type 'SafeString'.
        """
        from django.contrib.admin.sites import AdminSite
        from masters.admin import BillOfMaterialsAdmin, BillOfMaterialsInline

        bom = BillOfMaterials.objects.create(
            product=self.product,
            raw_material=self.flour,
            quantity=Decimal('40.0000'),
            unit=UnitChoices.KG,
            batch_size=Decimal('200.00')
        )

        site = AdminSite()
        bom_admin = BillOfMaterialsAdmin(BillOfMaterials, site)
        result = bom_admin.per_packet_display(bom)

        self.assertIn("0.2000", str(result))
        self.assertIn("kg / pkt", str(result))
        self.assertIn("<strong>", str(result))

        # Test inline display method as well
        bom_inline = BillOfMaterialsInline(Product, site)
        inline_result = bom_inline.quantity_per_packet_display(bom)
        self.assertIn("0.2000", str(inline_result))

    def test_admin_bom_changelist_view_renders_successfully(self):
        """
        Verify that the Django Admin BOM changelist page (/admin/masters/billofmaterials/)
        loads cleanly with HTTP 200 and renders the per_packet_display column without error.
        """
        from django.urls import reverse

        BillOfMaterials.objects.create(
            product=self.product,
            raw_material=self.flour,
            quantity=Decimal('40.0000'),
            unit=UnitChoices.KG,
            batch_size=Decimal('200.00')
        )

        admin_user = User.objects.create_superuser(
            username='admin_test_bom',
            password='Password123!',
            email='admin_test_bom@factoryflow.com'
        )
        self.client.force_login(admin_user)

        url = reverse('admin:masters_billofmaterials_changelist')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "0.2000")
        self.assertContains(response, "kg / pkt")


class MasterCrudRequestTests(TestCase):
    models_by_resource = {
        "products": Product,
        "raw-materials": RawMaterial,
        "customers": Customer,
        "suppliers": Supplier,
    }

    def setUp(self):
        setup_role_groups()
        self.admin = User.objects.create_user(
            username="masters-admin",
            password="Password123!",
        )
        self.admin.profile.role = Role.ADMIN
        self.admin.profile.save()
        self.client.force_login(self.admin)

    def payload(self, resource, suffix):
        active = {"is_active": "on"}
        if resource == "products":
            return {
                **active,
                "name": f"Test Product {suffix}",
                "code": f"TEST-PRODUCT-{suffix}",
                "packet_size": "250 g",
                "gst_rate": "5.00",
                "unit_price": "75.00",
            }
        if resource == "raw-materials":
            return {
                **active,
                "name": f"Test Material {suffix}",
                "code": f"TEST-MATERIAL-{suffix}",
                "unit": UnitChoices.KG,
                "standard_rate": "42.00",
                "weighted_average_rate": "40.0000",
            }
        common = {
            **active,
            "name": f"Test {resource.title()} {suffix}",
            "contact_number": "9876543210",
            "address": "1 Factory Road",
            "state": "Tamil Nadu",
            "state_code": "33",
            "gstin": "",
        }
        return common

    def test_summary_cards_link_to_each_management_list(self):
        response = self.client.get(reverse("masters:summary"))
        self.assertEqual(response.status_code, 200)
        for resource in self.models_by_resource:
            with self.subTest(resource=resource):
                self.assertContains(
                    response,
                    reverse("masters:list", kwargs={"resource": resource}),
                )

    def test_each_master_supports_list_add_details_edit_and_confirmed_delete(self):
        for index, (resource, model) in enumerate(self.models_by_resource.items()):
            suffix = str(index)
            payload = self.payload(resource, suffix)
            with self.subTest(resource=resource):
                list_url = reverse("masters:list", kwargs={"resource": resource})
                create_url = reverse("masters:create", kwargs={"resource": resource})
                self.assertEqual(self.client.get(list_url).status_code, 200)
                self.assertEqual(self.client.get(create_url).status_code, 200)

                response = self.client.post(create_url, payload)
                self.assertRedirects(response, list_url)
                obj = model.objects.get(code=payload["code"]) if resource in {
                    "products", "raw-materials"
                } else model.objects.get(name=payload["name"])

                detail_url = reverse(
                    "masters:detail",
                    kwargs={"resource": resource, "pk": obj.pk},
                )
                edit_url = reverse(
                    "masters:update",
                    kwargs={"resource": resource, "pk": obj.pk},
                )
                delete_url = reverse(
                    "masters:delete",
                    kwargs={"resource": resource, "pk": obj.pk},
                )

                response = self.client.get(list_url)
                self.assertContains(response, detail_url)
                self.assertContains(response, edit_url)
                self.assertContains(response, delete_url)
                self.assertEqual(self.client.get(detail_url).status_code, 200)
                self.assertContains(self.client.get(edit_url), "Save")

                edited_payload = self.payload(resource, suffix)
                edited_payload["name"] += " Edited"
                response = self.client.post(edit_url, edited_payload)
                self.assertRedirects(response, list_url)
                obj.refresh_from_db()
                self.assertTrue(obj.name.endswith(" Edited"))
                self.assertContains(self.client.get(detail_url), "Edited")

                response = self.client.get(delete_url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Confirm deactivation")
                response = self.client.post(delete_url)
                self.assertRedirects(response, list_url)
                obj.refresh_from_db()
                self.assertFalse(obj.is_active)
                self.assertFalse(model.objects.filter(pk=obj.pk, is_active=True).exists())

    def test_read_only_role_can_view_but_cannot_manage_masters(self):
        product = Product.objects.create(
            name="Read-only Product",
            code="READONLY-PRODUCT",
        )
        accountant = User.objects.create_user(
            username="masters-accountant",
            password="Password123!",
        )
        accountant.profile.role = Role.ACCOUNTANT
        accountant.profile.save()
        self.client.force_login(accountant)

        list_url = reverse("masters:list", kwargs={"resource": "products"})
        detail_url = reverse(
            "masters:detail",
            kwargs={"resource": "products", "pk": product.pk},
        )
        self.assertEqual(self.client.get(list_url).status_code, 200)
        self.assertEqual(self.client.get(detail_url).status_code, 200)
        self.assertNotContains(self.client.get(list_url), "Add</a>")
        self.assertNotContains(self.client.get(detail_url), ">Edit</a>")
        self.assertEqual(
            self.client.get(reverse("masters:create", kwargs={"resource": "products"})).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("masters:update", kwargs={"resource": "products", "pk": product.pk})
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("masters:delete", kwargs={"resource": "products", "pk": product.pk})
            ).status_code,
            403,
        )

    def test_unknown_master_resource_returns_404(self):
        self.assertEqual(
            self.client.get(reverse("masters:list", kwargs={"resource": "unknown"})).status_code,
            404,
        )

