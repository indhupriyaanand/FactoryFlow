from decimal import Decimal
from django.test import TestCase, RequestFactory
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.contrib.auth.models import User
from django.contrib.admin.sites import AdminSite
from unittest.mock import patch

from masters.models import RawMaterial, Product, UnitChoices
from inventory.models import StockLedgerEntry, StockMovementType
from inventory.admin import StockLedgerEntryAdmin
from inventory.services import create_stock_ledger_entry, create_reversal_ledger_entry


class StockLedgerModelTests(TestCase):
    """
    Test suite verifying data integrity, append-only protection,
    immutability, and uniqueness for StockLedgerEntry.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='inventory_tester',
            password='testpassword123',
            email='inventory@factoryflow.com'
        )
        self.raw_material = RawMaterial.objects.create(
            name='Rice Flour',
            code='RM-FLOUR-01',
            unit=UnitChoices.KG,
            standard_rate=Decimal('42.00')
        )
        self.packaging_material = RawMaterial.objects.create(
            name='Printed Pouches',
            code='RM-POUCH-01',
            unit=UnitChoices.PIECES,
            standard_rate=Decimal('1.50')
        )
        self.product = Product.objects.create(
            name='Murukku 200g',
            code='PRD-MUR-200G',
            packet_size='200 g',
            unit_price=Decimal('45.00')
        )

    def test_01_valid_in_ledger_entry_raw_material(self):
        """Valid IN stock ledger entry for raw material auto-assigns unit and persists."""
        entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('100.0000'),
            reference_number='PO-2026-001',
            description='Purchase receipt for flour',
            created_by=self.user
        )
        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.unit, UnitChoices.KG)
        self.assertEqual(entry.movement_type, StockMovementType.IN)
        self.assertEqual(entry.quantity, Decimal('100.0000'))
        self.assertEqual(entry.reference_number, 'PO-2026-001')
        self.assertTrue(entry.is_raw_material)
        self.assertFalse(entry.is_finished_product)
        self.assertEqual(entry.stock_item, self.raw_material)
        self.assertIn('Stock In', str(entry))
        self.assertIn('100.0000 kg', str(entry))

    def test_02_valid_out_ledger_entry_raw_material(self):
        """Valid OUT stock ledger entry for raw material persists."""
        entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.OUT,
            quantity=Decimal('25.5000'),
            reference_number='BATCH-2026-01',
            description='Consumed in production batch',
            created_by=self.user
        )
        self.assertEqual(entry.movement_type, StockMovementType.OUT)
        self.assertEqual(entry.quantity, Decimal('25.5000'))
        self.assertIn('Stock Out', str(entry))

    def test_03_valid_reversal_ledger_entry(self):
        """Valid REVERSAL stock ledger entry persists for compensatory adjustments."""
        entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.REVERSAL,
            quantity=Decimal('10.0000'),
            reference_number='REV-PO-2026-001',
            description='Cancellation reversal for returned material',
            created_by=self.user
        )
        self.assertEqual(entry.movement_type, StockMovementType.REVERSAL)
        self.assertEqual(entry.quantity, Decimal('10.0000'))
        self.assertIn('Stock Reversal', str(entry))

    def test_04_valid_finished_product_entry(self):
        """Valid entry referencing finished product defaults unit to PACKET."""
        entry = StockLedgerEntry.objects.create(
            product=self.product,
            movement_type=StockMovementType.IN,
            quantity=Decimal('200.0000'),
            reference_number='PROD-2026-001',
            description='Finished goods receipt from packaging'
        )
        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.unit, UnitChoices.PACKET)
        self.assertTrue(entry.is_finished_product)
        self.assertFalse(entry.is_raw_material)
        self.assertEqual(entry.stock_item, self.product)
        self.assertIn('Murukku 200g', str(entry))

    def test_05_zero_quantity_rejection(self):
        """Zero quantity must be rejected with ValidationError."""
        entry = StockLedgerEntry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('0.0000'),
            reference_number='TEST-ZERO'
        )
        with self.assertRaises(ValidationError) as ctx:
            entry.save()
        self.assertIn('quantity', ctx.exception.message_dict)

    def test_06_negative_quantity_rejection(self):
        """Negative quantity must be rejected with ValidationError."""
        entry = StockLedgerEntry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.OUT,
            quantity=Decimal('-15.0000'),
            reference_number='TEST-NEG'
        )
        with self.assertRaises(ValidationError) as ctx:
            entry.save()
        self.assertIn('quantity', ctx.exception.message_dict)

    def test_07_invalid_movement_type_rejection(self):
        """Invalid movement type choice must be rejected."""
        entry = StockLedgerEntry(
            raw_material=self.raw_material,
            movement_type='DISCARD',
            quantity=Decimal('5.0000'),
            reference_number='TEST-TYPE'
        )
        with self.assertRaises(ValidationError) as ctx:
            entry.save()
        self.assertIn('movement_type', ctx.exception.message_dict)

    def test_08_invalid_stock_reference_rejection_both_none(self):
        """Entry with neither raw material nor product must be rejected."""
        entry = StockLedgerEntry(
            raw_material=None,
            product=None,
            movement_type=StockMovementType.IN,
            quantity=Decimal('10.0000'),
            reference_number='TEST-NONE'
        )
        with self.assertRaises(ValidationError) as ctx:
            entry.save()
        self.assertIn(
            "must reference either a raw material or a finished product",
            str(ctx.exception)
        )

    def test_09_stock_reference_conflict_rejection_both_provided(self):
        """Entry with both raw material and product must be rejected."""
        entry = StockLedgerEntry(
            raw_material=self.raw_material,
            product=self.product,
            movement_type=StockMovementType.IN,
            quantity=Decimal('10.0000'),
            reference_number='TEST-BOTH'
        )
        with self.assertRaises(ValidationError) as ctx:
            entry.save()
        self.assertIn(
            "cannot reference both a raw material and a finished product",
            str(ctx.exception)
        )

    def test_10_unit_mismatch_rejection_raw_material(self):
        """Unit mismatch against raw material standard unit must be rejected."""
        entry = StockLedgerEntry(
            raw_material=self.raw_material,  # Unit is KG
            movement_type=StockMovementType.IN,
            quantity=Decimal('10.0000'),
            unit=UnitChoices.LITRE,  # Mismatched unit
            reference_number='TEST-UNIT-MISMATCH'
        )
        with self.assertRaises(ValidationError) as ctx:
            entry.save()
        self.assertIn('unit', ctx.exception.message_dict)
        self.assertIn('does not match raw material standard unit', ctx.exception.message_dict['unit'][0])

    def test_11_unit_mismatch_rejection_product(self):
        """Unit mismatch against finished product must be rejected."""
        entry = StockLedgerEntry(
            product=self.product,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            unit=UnitChoices.KG,  # Products can only be packet or pieces
            reference_number='TEST-PROD-UNIT'
        )
        with self.assertRaises(ValidationError) as ctx:
            entry.save()
        self.assertIn('unit', ctx.exception.message_dict)

    def test_12_existing_ledger_entry_modification_protection(self):
        """Existing stock ledger entries must raise ValidationError if save() is called again."""
        entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            reference_number='IMMUTABLE-01'
        )
        entry.quantity = Decimal('75.0000')
        with self.assertRaises(ValidationError) as ctx:
            entry.save()
        self.assertIn("append-only audit records and cannot be modified", str(ctx.exception))

    def test_13_existing_ledger_entry_deletion_protection(self):
        """Existing stock ledger entries must raise ValidationError if delete() is called."""
        entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            reference_number='NODELETE-01'
        )
        with self.assertRaises(ValidationError) as ctx:
            entry.delete()
        self.assertIn("permanent audit records and cannot be deleted", str(ctx.exception))
        # Ensure record remains intact in DB
        self.assertTrue(StockLedgerEntry.objects.filter(id=entry.id).exists())

    def test_14_duplicate_reference_protection_raw_material(self):
        """Duplicate raw material ledger entries for the same reference and movement_type are blocked."""
        StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            reference_number='INV-DUP-01'
        )
        # Attempting to insert duplicate entry with same reference, material, and movement_type
        duplicate_entry = StockLedgerEntry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            reference_number='INV-DUP-01'
        )
        with self.assertRaises(ValidationError) as ctx:
            duplicate_entry.save()
        self.assertIn("unique_raw_material_ledger_reference", str(ctx.exception))

    def test_15_duplicate_reference_protection_product(self):
        """Duplicate product ledger entries for the same reference and movement_type are blocked."""
        StockLedgerEntry.objects.create(
            product=self.product,
            movement_type=StockMovementType.IN,
            quantity=Decimal('100.0000'),
            reference_number='BATCH-DUP-01'
        )
        duplicate_entry = StockLedgerEntry(
            product=self.product,
            movement_type=StockMovementType.IN,
            quantity=Decimal('100.0000'),
            reference_number='BATCH-DUP-01'
        )
        with self.assertRaises(ValidationError) as ctx:
            duplicate_entry.save()
        self.assertIn("unique_product_ledger_reference", str(ctx.exception))

    def test_16_different_movement_types_same_reference_allowed(self):
        """Same reference number with different movement types (e.g. IN and REVERSAL) is allowed."""
        in_entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            reference_number='INV-REV-PAIR'
        )
        reversal_entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.REVERSAL,
            quantity=Decimal('50.0000'),
            reference_number='INV-REV-PAIR'
        )
        self.assertIsNotNone(in_entry.pk)
        self.assertIsNotNone(reversal_entry.pk)
        self.assertNotEqual(in_entry.pk, reversal_entry.pk)

    def test_17_queryset_update_protection(self):
        """QuerySet.update() must raise ValidationError on StockLedgerEntry."""
        entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            reference_number='QS-UPDATE-01'
        )
        with self.assertRaises(ValidationError) as ctx:
            StockLedgerEntry.objects.filter(id=entry.id).update(quantity=Decimal('100.0000'))
        self.assertIn("cannot be modified via QuerySet.update()", str(ctx.exception))

    def test_18_queryset_delete_protection(self):
        """QuerySet.delete() must raise ValidationError on StockLedgerEntry."""
        entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            reference_number='QS-DELETE-01'
        )
        with self.assertRaises(ValidationError) as ctx:
            StockLedgerEntry.objects.filter(id=entry.id).delete()
        self.assertIn("cannot be deleted via QuerySet.delete()", str(ctx.exception))

    def test_19_queryset_bulk_update_protection(self):
        """QuerySet.bulk_update() must raise ValidationError on StockLedgerEntry."""
        entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            reference_number='QS-BULK-01'
        )
        with self.assertRaises(ValidationError) as ctx:
            StockLedgerEntry.objects.bulk_update([entry], ['quantity'])
        self.assertIn("cannot be modified via QuerySet.bulk_update()", str(ctx.exception))

    def test_20_reference_number_normalization_and_blank_rejection(self):
        """Reference numbers are stripped and converted to uppercase; blank raises ValidationError."""
        entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('10.0000'),
            reference_number='  inv-lower-space  '
        )
        self.assertEqual(entry.reference_number, 'INV-LOWER-SPACE')

        blank_entry = StockLedgerEntry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('10.0000'),
            reference_number='   '
        )
        with self.assertRaises(ValidationError) as ctx:
            blank_entry.save()
        self.assertIn('reference_number', ctx.exception.message_dict)

    def test_21_admin_read_only_permissions(self):
        """Django Admin configuration strictly forbids add, change, and delete permissions."""
        site = AdminSite()
        admin_instance = StockLedgerEntryAdmin(StockLedgerEntry, site)
        factory = RequestFactory()
        request = factory.get('/admin/inventory/stockledgerentry/')
        request.user = self.user

        entry = StockLedgerEntry.objects.create(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('20.0000'),
            reference_number='ADM-001'
        )

        self.assertFalse(admin_instance.has_add_permission(request))
        self.assertFalse(admin_instance.has_change_permission(request, entry))
        self.assertFalse(admin_instance.has_delete_permission(request, entry))
        self.assertIsNone(admin_instance.actions)

        readonly_fields = admin_instance.get_readonly_fields(request, entry)
        expected_fields = [f.name for f in StockLedgerEntry._meta.fields]
        self.assertEqual(set(readonly_fields), set(expected_fields))


class StockLedgerServiceTests(TestCase):
    """
    Test suite verifying the inventory service layer:
    - Controlled creation of IN/OUT entries
    - Decimal quantity handling and validation
    - Idempotency and duplicate prevention
    - Reversal integrity and single-reversal enforcement
    - Reversal audit linkage and preservation of original entry
    - Atomic rollback on failures
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='service_tester',
            password='testpassword123',
            email='srv_tester@factoryflow.com'
        )
        self.raw_material = RawMaterial.objects.create(
            name='Cooking Oil',
            code='RM-OIL-01',
            unit=UnitChoices.LITRE,
            standard_rate=Decimal('135.00')
        )
        self.product = Product.objects.create(
            name='Murukku 200g',
            code='PRD-MUR-200G',
            packet_size='200 g',
            unit_price=Decimal('45.00')
        )

    def test_01_successful_service_in_entry(self):
        """Service creates valid IN ledger entry for raw material and defaults unit."""
        entry = create_stock_ledger_entry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            reference_number='PO-SRV-01',
            description='Purchase receipt via service',
            created_by=self.user
        )
        self.assertIsNotNone(entry.pk)
        self.assertEqual(entry.movement_type, StockMovementType.IN)
        self.assertEqual(entry.quantity, Decimal('50.0000'))
        self.assertEqual(entry.unit, UnitChoices.LITRE)
        self.assertEqual(entry.reference_number, 'PO-SRV-01')
        self.assertEqual(entry.created_by, self.user)

    def test_02_successful_service_out_entry(self):
        """Service creates valid OUT ledger entry for raw material."""
        create_stock_ledger_entry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('20.0000'),
            reference_number='OPENING-SRV-01',
        )
        entry = create_stock_ledger_entry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.OUT,
            quantity=Decimal('15.5000'),
            reference_number='BATCH-SRV-01',
            description='Production consumption via service'
        )
        self.assertEqual(entry.movement_type, StockMovementType.OUT)
        self.assertEqual(entry.quantity, Decimal('15.5000'))

    def test_03_successful_service_product_entry(self):
        """Service creates valid finished product entry defaulting unit to PACKET."""
        entry = create_stock_ledger_entry(
            product=self.product,
            movement_type=StockMovementType.IN,
            quantity=Decimal('200.0000'),
            reference_number='PROD-SRV-01',
            description='Finished goods receipt'
        )
        self.assertEqual(entry.unit, UnitChoices.PACKET)
        self.assertEqual(entry.product, self.product)

    def test_04_invalid_quantity_zero_and_negative(self):
        """Service rejects zero and negative quantities with ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            create_stock_ledger_entry(
                raw_material=self.raw_material,
                movement_type=StockMovementType.IN,
                quantity=Decimal('0.0000'),
                reference_number='TEST-ZERO'
            )
        self.assertIn('quantity', ctx.exception.message_dict)

        with self.assertRaises(ValidationError) as ctx:
            create_stock_ledger_entry(
                raw_material=self.raw_material,
                movement_type=StockMovementType.IN,
                quantity=Decimal('-10.0000'),
                reference_number='TEST-NEG'
            )
        self.assertIn('quantity', ctx.exception.message_dict)

    def test_05_invalid_quantity_float_rejected(self):
        """Service strictly forbids float quantities to prevent rounding errors."""
        with self.assertRaises(ValidationError) as ctx:
            create_stock_ledger_entry(
                raw_material=self.raw_material,
                movement_type=StockMovementType.IN,
                quantity=25.5,  # float
                reference_number='TEST-FLOAT'
            )
        self.assertIn('quantity', ctx.exception.message_dict)
        self.assertIn("Floating-point quantities are forbidden", ctx.exception.message_dict['quantity'][0])

    def test_06_invalid_unit_rejection(self):
        """Service rejects unit mismatch against referenced stock item."""
        with self.assertRaises(ValidationError) as ctx:
            create_stock_ledger_entry(
                raw_material=self.raw_material,  # Unit is LITRE
                movement_type=StockMovementType.IN,
                quantity=Decimal('10.0000'),
                unit=UnitChoices.KG,  # Mismatched unit
                reference_number='TEST-UNIT-FAIL'
            )
        self.assertIn('unit', ctx.exception.message_dict)

    def test_07_duplicate_reference_rejection(self):
        """Service rejects creating a duplicate entry with the same reference, item, and movement type."""
        create_stock_ledger_entry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('50.0000'),
            reference_number='PO-DUP-CHECK'
        )
        with self.assertRaises(ValidationError) as ctx:
            create_stock_ledger_entry(
                raw_material=self.raw_material,
                movement_type=StockMovementType.IN,
                quantity=Decimal('50.0000'),
                reference_number='PO-DUP-CHECK'
            )
        self.assertIn("already exists", str(ctx.exception))

    def test_08_direct_reversal_via_create_stock_ledger_entry_rejected(self):
        """Direct creation of REVERSAL movements via create_stock_ledger_entry is prohibited."""
        with self.assertRaises(ValidationError) as ctx:
            create_stock_ledger_entry(
                raw_material=self.raw_material,
                movement_type=StockMovementType.REVERSAL,
                quantity=Decimal('10.0000'),
                reference_number='PO-DIRECT-REV'
            )
        self.assertIn('movement_type', ctx.exception.message_dict)
        self.assertIn("create_reversal_ledger_entry()", ctx.exception.message_dict['movement_type'][0])

    def test_09_successful_reversal_using_instance(self):
        """create_reversal_ledger_entry creates a linked REVERSAL entry referencing the original."""
        original = create_stock_ledger_entry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('80.0000'),
            reference_number='PO-REV-TARGET',
            created_by=self.user
        )
        reversal = create_reversal_ledger_entry(
            original_entry=original,
            description="Reversing purchase due to damaged barrels",
            created_by=self.user
        )
        self.assertIsNotNone(reversal.pk)
        self.assertEqual(reversal.movement_type, StockMovementType.REVERSAL)
        self.assertEqual(reversal.quantity, original.quantity)
        self.assertEqual(reversal.raw_material, original.raw_material)
        self.assertEqual(reversal.unit, original.unit)
        self.assertEqual(reversal.reference_number, f"REV-{original.reference_number}")
        self.assertIn(f"[REVERSAL-OF-ENTRY-#{original.pk}]", reversal.description)
        self.assertIn("Reversing purchase due to damaged barrels", reversal.description)

    def test_10_successful_reversal_using_id(self):
        """create_reversal_ledger_entry accepts an integer primary key as original_entry."""
        original = create_stock_ledger_entry(
            product=self.product,
            movement_type=StockMovementType.IN,
            quantity=Decimal('100.0000'),
            reference_number='PROD-REV-ID-TARGET'
        )
        reversal = create_reversal_ledger_entry(
            original_entry=original.pk,
            reference_number='CUSTOM-REV-REF-01'
        )
        self.assertEqual(reversal.movement_type, StockMovementType.REVERSAL)
        self.assertEqual(reversal.reference_number, 'CUSTOM-REV-REF-01')
        self.assertEqual(reversal.product, self.product)

    def test_11_duplicate_reversal_rejection(self):
        """Attempting to reverse the same ledger entry a second time raises ValidationError."""
        original = create_stock_ledger_entry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('40.0000'),
            reference_number='PO-DOUBLE-REV'
        )
        create_reversal_ledger_entry(original_entry=original)

        # Attempting second reversal
        with self.assertRaises(ValidationError) as ctx:
            create_reversal_ledger_entry(original_entry=original)
        self.assertIn("already exists", str(ctx.exception))

    def test_reversal_rolls_back_if_source_restoration_fails(self):
        original = create_stock_ledger_entry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('40.0000'),
            reference_number='PO-REV-ATOMIC',
        )
        with patch(
            'inventory.services._apply_reversal_source_effects',
            side_effect=ValidationError('Source restoration failed.'),
        ):
            with self.assertRaises(ValidationError):
                create_reversal_ledger_entry(original_entry=original)

        self.assertFalse(
            StockLedgerEntry.objects.filter(
                reversed_entry=original,
                movement_type=StockMovementType.REVERSAL,
            ).exists()
        )

    def test_12_invalid_reversal_reference(self):
        """Reversing a non-existent entry or None raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            create_reversal_ledger_entry(original_entry=999999)
        self.assertIn("does not exist", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            create_reversal_ledger_entry(original_entry=None)
        self.assertIn("valid original stock ledger entry is required", str(ctx.exception))

    def test_13_invalid_reversal_of_a_reversal(self):
        """Attempting to reverse an existing reversal entry raises ValidationError."""
        original = create_stock_ledger_entry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('20.0000'),
            reference_number='PO-REV-CHAIN'
        )
        reversal = create_reversal_ledger_entry(original_entry=original)

        with self.assertRaises(ValidationError) as ctx:
            create_reversal_ledger_entry(original_entry=reversal)
        self.assertIn("already a reversal transaction", str(ctx.exception))

    def test_14_existing_ledger_records_remain_unchanged_after_reversal(self):
        """Creating a reversal strictly preserves the original entry's fields and timestamps."""
        original = create_stock_ledger_entry(
            raw_material=self.raw_material,
            movement_type=StockMovementType.IN,
            quantity=Decimal('60.0000'),
            reference_number='PO-PRESERVE-TEST'
        )
        orig_id = original.id
        orig_qty = original.quantity
        orig_movement = original.movement_type
        orig_updated_at = original.updated_at

        # Execute reversal
        reversal = create_reversal_ledger_entry(original_entry=original)
        self.assertIsNotNone(reversal.pk)

        # Verify original entry remains unchanged in DB
        original.refresh_from_db()
        self.assertEqual(original.id, orig_id)
        self.assertEqual(original.quantity, orig_qty)
        self.assertEqual(original.movement_type, orig_movement)
        self.assertEqual(original.updated_at, orig_updated_at)

    def test_15_transaction_rollback_when_creation_fails(self):
        """Atomic transaction rolls back and leaves no partial records on failure."""
        initial_count = StockLedgerEntry.objects.count()

        # Simulate a transaction block where entry creation is followed by a crash
        try:
            with transaction.atomic():
                create_stock_ledger_entry(
                    raw_material=self.raw_material,
                    movement_type=StockMovementType.IN,
                    quantity=Decimal('30.0000'),
                    reference_number='PO-CRASH-TEST'
                )
                raise RuntimeError("Simulated mid-transaction failure")
        except RuntimeError:
            pass

        # Verify the record was rolled back
        self.assertEqual(StockLedgerEntry.objects.count(), initial_count)
        self.assertFalse(StockLedgerEntry.objects.filter(reference_number='PO-CRASH-TEST').exists())

    def test_16_stock_reference_conflict_and_missing_rejection(self):
        """Service rejects entries with missing or conflicting stock items."""
        with self.assertRaises(ValidationError) as ctx:
            create_stock_ledger_entry(
                movement_type=StockMovementType.IN,
                quantity=Decimal('10.0000'),
                reference_number='TEST-NO-ITEM'
            )
        self.assertIn("must reference either a raw material or a finished product", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            create_stock_ledger_entry(
                raw_material=self.raw_material,
                product=self.product,
                movement_type=StockMovementType.IN,
                quantity=Decimal('10.0000'),
                reference_number='TEST-BOTH-ITEMS'
            )
        self.assertIn("cannot reference both a raw material and a finished product", str(ctx.exception))
