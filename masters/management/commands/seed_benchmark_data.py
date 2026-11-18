from decimal import Decimal
from django.core.management.base import BaseCommand
from masters.models import Product, RawMaterial, Customer, Supplier, BillOfMaterials, UnitChoices

class Command(BaseCommand):
    help = "Idempotently seeds benchmark master data for the Murukku 200g manufacturing case."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== Seeding FactoryFlow Benchmark Master Data (Phase 3) ==="))

        # 1. Product Master: Murukku 200g
        product, p_created = Product.objects.update_or_create(
            code="PRD-MUR-200G",
            defaults={
                'name': "Murukku 200g",
                'packet_size': "200 g",
                'gst_rate': Decimal('5.00'),
                'unit_price': Decimal('50.00'),
                'is_active': True,
            }
        )
        p_status = "Created" if p_created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f" [Product] {p_status}: {product.name} ({product.code}) - ₹{product.unit_price} (GST {product.gst_rate}%)"
        ))

        # 2. Raw Material Masters
        raw_materials_data = [
            {
                'code': "RM-FLOUR",
                'name': "Flour",
                'unit': UnitChoices.KG,
                'standard_rate': Decimal('42.00'),
            },
            {
                'code': "RM-OIL",
                'name': "Cooking Oil",
                'unit': UnitChoices.LITRE,
                'standard_rate': Decimal('140.00'),
            },
            {
                'code': "RM-SPICE",
                'name': "Spice Mix",
                'unit': UnitChoices.KG,
                'standard_rate': Decimal('200.00'),
            },
            {
                'code': "RM-POUCH",
                'name': "Pouches",
                'unit': UnitChoices.PIECES,
                'standard_rate': Decimal('2.50'),
            },
            {
                'code': "RM-CARTON",
                'name': "Cartons",
                'unit': UnitChoices.PIECES,
                'standard_rate': Decimal('15.00'),
            },
        ]

        rm_lookup = {}
        for rm in raw_materials_data:
            mat, m_created = RawMaterial.objects.update_or_create(
                code=rm['code'],
                defaults={
                    'name': rm['name'],
                    'unit': rm['unit'],
                    'standard_rate': rm['standard_rate'],
                    'is_active': True,
                }
            )
            rm_lookup[rm['code']] = mat
            m_status = "Created" if m_created else "Updated"
            self.stdout.write(self.style.SUCCESS(
                f" [Material] {m_status}: {mat.name} ({mat.code}) - ₹{mat.standard_rate} / {mat.unit}"
            ))

        # 3. Customer Masters
        customers_data = [
            {
                'name': "Retailer A",
                'contact_number': "9876543210",
                'address': "Shop No. 12, Gandhi Market, Madurai",
                'state': "Tamil Nadu",
                'state_code': "33",
                'gstin': "33AAAAA0000A1Z5",
            },
            {
                'name': "Distributor B",
                'contact_number': "9123456789",
                'address': "Plot 45, Peenya Industrial Area, Bengaluru",
                'state': "Karnataka",
                'state_code': "29",
                'gstin': "29BBBBB0000B1Z6",
            },
        ]

        for cust_info in customers_data:
            cust, c_created = Customer.objects.update_or_create(
                name=cust_info['name'],
                defaults={
                    'contact_number': cust_info['contact_number'],
                    'address': cust_info['address'],
                    'state': cust_info['state'],
                    'state_code': cust_info['state_code'],
                    'gstin': cust_info['gstin'],
                    'is_active': True,
                }
            )
            c_status = "Created" if c_created else "Updated"
            self.stdout.write(self.style.SUCCESS(
                f" [Customer] {c_status}: {cust.name} - State: {cust.state} (Code: {cust.state_code})"
            ))

        # 4. Supplier Master
        supplier, s_created = Supplier.objects.update_or_create(
            name="Sample Raw Material Supplier",
            defaults={
                'contact_number': "9443322110",
                'address': "Agro Wholesale Complex, NH-45, Tiruchirappalli",
                'state': "Tamil Nadu",
                'state_code': "33",
                'gstin': "33CCCCC0000C1Z7",
                'is_active': True,
            }
        )
        s_status = "Created" if s_created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f" [Supplier] {s_status}: {supplier.name} - State: {supplier.state} (Code: {supplier.state_code})"
        ))

        # 5. Bill of Materials (BOM) Recipe for Murukku 200g (Batch size: 200 packets)
        bom_data = [
            {
                'material_code': "RM-FLOUR",
                'quantity': Decimal('40.0000'),
                'unit': UnitChoices.KG,
            },
            {
                'material_code': "RM-OIL",
                'quantity': Decimal('10.0000'),
                'unit': UnitChoices.LITRE,
            },
            {
                'material_code': "RM-SPICE",
                'quantity': Decimal('1.2000'),
                'unit': UnitChoices.KG,
            },
            {
                'material_code': "RM-POUCH",
                'quantity': Decimal('200.0000'),
                'unit': UnitChoices.PIECES,
            },
            {
                'material_code': "RM-CARTON",
                'quantity': Decimal('10.0000'),
                'unit': UnitChoices.PIECES,
            },
        ]

        batch_size = Decimal('200.00')
        self.stdout.write(f"\n--- BOM Recipe for '{product.name}' (Standard Batch: {batch_size} packets) ---")
        for item in bom_data:
            mat = rm_lookup[item['material_code']]
            bom_entry, b_created = BillOfMaterials.objects.update_or_create(
                product=product,
                raw_material=mat,
                defaults={
                    'quantity': item['quantity'],
                    'unit': item['unit'],
                    'batch_size': batch_size,
                    'is_active': True,
                }
            )
            b_status = "Created" if b_created else "Updated"
            per_packet = bom_entry.quantity_per_packet
            self.stdout.write(self.style.SUCCESS(
                f"  [{b_status}] {mat.name:<14} : {bom_entry.quantity:>8} {bom_entry.unit:<6} "
                f"-> ({per_packet:.4f} {bom_entry.unit}/packet)"
            ))

        self.stdout.write(self.style.SUCCESS("\nAll Phase 3 benchmark master data seeded successfully."))
