from decimal import Decimal
from django.test import TestCase, Client
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.contrib.auth.models import User
from django.urls import reverse
from accounts.models import Role
from masters.models import RawMaterial, Supplier, UnitChoices
from purchases.models import PurchaseInvoice, PurchaseItem, PurchaseStatus
from purchases.services import cancel_purchase_invoice, receive_purchase_invoice
from inventory.models import (
    InventorySource,
    InventorySourceAllocation,
    InventorySourceType,
    StockLedgerEntry,
    StockMovementType,
)
from inventory.services import calculate_weighted_average_rate, create_stock_ledger_entry
from inventory.services import get_raw_material_stock_balance
from unittest.mock import patch


class PurchaseModelTests(TestCase):
    """
    Automated tests for Purchase Management:
    - PurchaseInvoice and PurchaseItem creation
    - Multi-item calculation with Decimal arithmetic
    - Strict unit matching with RawMaterial
    - Non-negative and positive validations
    - Invoice lifecycle and item modification protections
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='purchaser',
            password='Password123!',
            email='purchaser@factoryflow.com'
        )
        self.user.profile.role = 'ACCOUNTANT'
        self.user.profile.save()

        self.supplier = Supplier.objects.create(
            name="Sri Meenakshi Agro Traders",
            contact_number="9876543210",
            address="Grain Market, Madurai",
            state="Tamil Nadu",
            state_code="33",
            gstin="33AAAAA0000A1Z5"
        )

        self.flour = RawMaterial.objects.create(
            name="Flour",
            code="RM-FLOUR",
            unit=UnitChoices.KG,
            standard_rate=Decimal('42.00')
        )

        self.oil = RawMaterial.objects.create(
            name="Cooking Oil",
            code="RM-OIL",
            unit=UnitChoices.LITRE,
            standard_rate=Decimal('140.00')
        )

        self.pouch = RawMaterial.objects.create(
            name="Pouches",
            code="RM-POUCH",
            unit=UnitChoices.PIECES,
            standard_rate=Decimal('2.50')
        )

    def test_single_item_purchase_calculation(self):
        """
        Verify benchmark purchase: 60 kg Flour at ₹45/kg = ₹2,700.00.
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-BNCH-001",
            supplier=self.supplier,
            created_by=self.user
        )
        self.assertEqual(invoice.status, PurchaseStatus.DRAFT)

        item = PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('60.0000'),
            unit_price=Decimal('45.00')
        )

        # 1. Check unit auto-defaults to RawMaterial unit ('kg')
        self.assertEqual(item.unit, UnitChoices.KG)

        # 2. Check line total: 60 * 45 = 2700.00
        self.assertEqual(item.total_price, Decimal('2700.00'))

        # 3. Check invoice total amount updated
        invoice.refresh_from_db()
        self.assertEqual(invoice.total_amount, Decimal('2700.00'))
        self.assertIn("PUR-BNCH-001", str(invoice))

    def test_multi_item_purchase_calculation(self):
        """
        Verify multi-item purchase total amount calculation using Decimal precision:
        - 60 kg Flour @ ₹45.00 = ₹2,700.00
        - 20 L Oil @ ₹142.50 = ₹2,850.00
        - 500 Pouches @ ₹2.50 = ₹1,250.00
        Total = ₹6,800.00
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-MULTI-002",
            supplier=self.supplier,
            created_by=self.user
        )

        PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('60.0000'),
            unit_price=Decimal('45.00')
        )
        PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.oil,
            quantity=Decimal('20.0000'),
            unit_price=Decimal('142.50')
        )
        PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.pouch,
            quantity=Decimal('500.0000'),
            unit_price=Decimal('2.50')
        )

        invoice.refresh_from_db()
        self.assertEqual(invoice.items.count(), 3)
        self.assertEqual(invoice.total_amount, Decimal('6800.00'))

    def test_unit_mismatch_with_raw_material_rejected(self):
        """
        Requirement 7: Validate that material units in PurchaseItem
        match the corresponding RawMaterial units (e.g. Flour must be kg, not litre).
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-ERR-003",
            supplier=self.supplier
        )

        item = PurchaseItem(
            purchase=invoice,
            raw_material=self.flour,  # unit is kg
            quantity=Decimal('10.0000'),
            unit=UnitChoices.LITRE,   # Invalid unit for flour
            unit_price=Decimal('45.00')
        )
        with self.assertRaises(ValidationError) as ctx:
            item.full_clean()
        self.assertIn('unit', ctx.exception.message_dict)

    def test_zero_or_negative_quantity_rejected(self):
        """
        Validate that purchase quantity must be strictly greater than zero.
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-ERR-004",
            supplier=self.supplier
        )

        # Zero quantity
        item_zero = PurchaseItem(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('0.0000'),
            unit_price=Decimal('45.00')
        )
        with self.assertRaises(ValidationError):
            item_zero.full_clean()

        # Negative quantity
        item_neg = PurchaseItem(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('-10.0000'),
            unit_price=Decimal('45.00')
        )
        with self.assertRaises(ValidationError):
            item_neg.full_clean()

    def test_negative_unit_price_rejected(self):
        """
        Validate that unit price cannot be negative.
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-ERR-005",
            supplier=self.supplier
        )
        item = PurchaseItem(
            purchase=invoice,
            raw_material=self.oil,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('-50.00')
        )
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_duplicate_material_in_same_invoice_prevented(self):
        """
        Verify unique constraint preventing duplicate raw material lines in one invoice.
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-DUP-006",
            supplier=self.supplier
        )
        PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('50.0000'),
            unit_price=Decimal('45.00')
        )
        with self.assertRaises(ValidationError):
            PurchaseItem.objects.create(
                purchase=invoice,
                raw_material=self.flour,
                quantity=Decimal('10.0000'),
                unit_price=Decimal('45.00')
            )

    def test_invoice_number_uniqueness(self):
        """
        Verify duplicate invoice numbers are rejected.
        """
        PurchaseInvoice.objects.create(
            invoice_number="PUR-UNIQUE-007",
            supplier=self.supplier
        )
        with self.assertRaises(ValidationError):
            PurchaseInvoice.objects.create(
                invoice_number="PUR-UNIQUE-007",
                supplier=self.supplier
            )

    def test_item_modification_blocked_on_locked_invoice(self):
        """
        Verify that line items cannot be added or deleted on a RECEIVED invoice.
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-LOCK-008",
            supplier=self.supplier
        )
        item = PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('50.0000'),
            unit_price=Decimal('45.00')
        )

        # Transition invoice to RECEIVED using authorized service transition pattern
        PurchaseInvoice.objects.filter(pk=invoice.pk).update(status=PurchaseStatus.RECEIVED)
        invoice.refresh_from_db()

        # Attempting to add a new item should raise ValidationError
        new_item = PurchaseItem(
            purchase=invoice,
            raw_material=self.oil,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('140.00')
        )
        with self.assertRaises(ValidationError):
            new_item.full_clean()

        # Attempting to delete existing item should raise ValidationError
        with self.assertRaises(ValidationError):
            item.delete()

    def test_item_deletion_recalculates_invoice_total(self):
        """
        Deleting a line item from a DRAFT invoice must automatically update invoice total_amount.
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-DEL-009",
            supplier=self.supplier
        )
        item1 = PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('45.00')  # 450
        )
        item2 = PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.oil,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('140.00') # 1400
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.total_amount, Decimal('1850.00'))

        # Delete item1
        item1.delete()
        invoice.refresh_from_db()
        self.assertEqual(invoice.total_amount, Decimal('1400.00'))
        self.assertEqual(invoice.items.count(), 1)

    # -------------------------------------------------------------
    # Hardening Tests (Fix 1, Fix 2, Fix 3)
    # -------------------------------------------------------------
    def test_direct_save_validation_and_normalization(self):
        """
        Fix 1: PurchaseInvoice.save() must normalize invoice_number and run full_clean().
        """
        invoice = PurchaseInvoice(
            invoice_number="  pur-lower-010  ",
            supplier=self.supplier
        )
        invoice.save()
        self.assertEqual(invoice.invoice_number, "PUR-LOWER-010")

    def test_prevent_cancelled_invoice_reopening(self):
        """
        Fix 1 & 2: Reopening a CANCELLED invoice must be blocked at model save().
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-CAN-011",
            supplier=self.supplier
        )
        invoice.status = PurchaseStatus.CANCELLED
        invoice.save()

        # Try to reopen as DRAFT
        invoice.status = PurchaseStatus.DRAFT
        with self.assertRaises(ValidationError) as ctx:
            invoice.save()
        self.assertIn('status', ctx.exception.message_dict)

    def test_prevent_modifying_received_invoice_core_fields(self):
        """
        Fix 2: Once RECEIVED, core fields (supplier, invoice_number, purchase_date)
        are strictly immutable.
        """
        import datetime
        another_supplier = Supplier.objects.create(
            name="Alternative Supplier",
            contact_number="9000000000",
            address="Chennai",
            state="Tamil Nadu"
        )
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-RCV-012",
            supplier=self.supplier,
            purchase_date=datetime.date(2026, 1, 15)
        )
        PurchaseInvoice.objects.filter(pk=invoice.pk).update(status=PurchaseStatus.RECEIVED)
        invoice.refresh_from_db()

        # 1. Attempt changing supplier
        invoice.supplier = another_supplier
        with self.assertRaises(ValidationError) as ctx:
            invoice.save()
        self.assertIn('supplier', ctx.exception.message_dict)
        invoice.supplier = self.supplier  # Revert for next test

        # 2. Attempt changing invoice number
        invoice.invoice_number = "PUR-MUTATED-012"
        with self.assertRaises(ValidationError) as ctx:
            invoice.save()
        self.assertIn('invoice_number', ctx.exception.message_dict)
        invoice.invoice_number = "PUR-RCV-012"  # Revert for next test

        # 3. Attempt changing purchase date
        invoice.purchase_date = datetime.date(2026, 2, 20)
        with self.assertRaises(ValidationError) as ctx:
            invoice.save()
        self.assertIn('purchase_date', ctx.exception.message_dict)

    def test_prevent_modifying_cancelled_invoice_core_fields(self):
        """
        Fix 2: Once CANCELLED, core fields (supplier, invoice_number, purchase_date)
        are also strictly immutable.
        """
        another_supplier = Supplier.objects.create(
            name="Third Supplier",
            contact_number="9111111111",
            address="Coimbatore",
            state="Tamil Nadu"
        )
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-CAN-013",
            supplier=self.supplier
        )
        invoice.status = PurchaseStatus.CANCELLED
        invoice.save()

        invoice.supplier = another_supplier
        with self.assertRaises(ValidationError) as ctx:
            invoice.save()
        self.assertIn('supplier', ctx.exception.message_dict)

    def test_admin_status_and_lock_protection(self):
        """
        Fix 3: Verify Django Admin protections:
        - Status is read-only for draft invoices (cannot manually choose RECEIVED).
        - All core fields are read-only for received/cancelled invoices.
        - Delete permission is disabled for received/cancelled invoices.
        """
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from purchases.admin import PurchaseInvoiceAdmin

        factory = RequestFactory()
        request = factory.get('/')
        request.user = User.objects.create_superuser(
            username='admin_lock_test',
            password='Password123!',
            email='admin_lock@factoryflow.com'
        )

        site = AdminSite()
        admin_obj = PurchaseInvoiceAdmin(PurchaseInvoice, site)

        # 1. Draft invoice
        draft_inv = PurchaseInvoice.objects.create(
            invoice_number="PUR-ADM-014",
            supplier=self.supplier
        )
        draft_readonly = admin_obj.get_readonly_fields(request, draft_inv)
        self.assertIn('status', draft_readonly)
        self.assertIn('total_amount_display', draft_readonly)
        self.assertNotIn('supplier', draft_readonly)
        self.assertTrue(admin_obj.has_delete_permission(request, draft_inv))

        # 2. Received invoice
        rcv_inv = PurchaseInvoice.objects.create(
            invoice_number="PUR-ADM-015",
            supplier=self.supplier
        )
        PurchaseInvoice.objects.filter(pk=rcv_inv.pk).update(status=PurchaseStatus.RECEIVED)
        rcv_inv.refresh_from_db()

        rcv_readonly = admin_obj.get_readonly_fields(request, rcv_inv)
        self.assertIn('status', rcv_readonly)
        self.assertIn('supplier', rcv_readonly)
        self.assertIn('invoice_number', rcv_readonly)
        self.assertIn('purchase_date', rcv_readonly)
        self.assertIn('total_amount_display', rcv_readonly)
        # Delete must be blocked
        self.assertFalse(admin_obj.has_delete_permission(request, rcv_inv))

    # -------------------------------------------------------------
    # Strict Transition & Item Reassignment Security Tests
    # -------------------------------------------------------------
    def test_direct_draft_to_received_save_rejected(self):
        """
        FIX 1: Direct DRAFT -> RECEIVED transition via model save() must raise ValidationError.
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-TRANS-001",
            supplier=self.supplier
        )
        invoice.status = PurchaseStatus.RECEIVED
        with self.assertRaises(ValidationError) as ctx:
            invoice.save()
        self.assertIn('status', ctx.exception.message_dict)

    def test_direct_received_to_cancelled_save_rejected(self):
        """
        FIX 1: Direct RECEIVED -> CANCELLED transition via model save() must raise ValidationError.
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-TRANS-002",
            supplier=self.supplier
        )
        PurchaseInvoice.objects.filter(pk=invoice.pk).update(status=PurchaseStatus.RECEIVED)
        invoice.refresh_from_db()

        invoice.status = PurchaseStatus.CANCELLED
        with self.assertRaises(ValidationError) as ctx:
            invoice.save()
        self.assertIn('status', ctx.exception.message_dict)

    def test_direct_received_to_draft_save_rejected(self):
        """
        FIX 1: Direct RECEIVED -> DRAFT transition via model save() must always be rejected.
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-TRANS-003",
            supplier=self.supplier
        )
        PurchaseInvoice.objects.filter(pk=invoice.pk).update(status=PurchaseStatus.RECEIVED)
        invoice.refresh_from_db()

        invoice.status = PurchaseStatus.DRAFT
        with self.assertRaises(ValidationError) as ctx:
            invoice.save()
        self.assertIn('status', ctx.exception.message_dict)

    def test_draft_to_cancelled_save_allowed(self):
        """
        FIX 1: Direct DRAFT -> CANCELLED transition is allowed (cancelling an unreceived draft).
        """
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PUR-TRANS-004",
            supplier=self.supplier
        )
        invoice.status = PurchaseStatus.CANCELLED
        invoice.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.CANCELLED)

    def test_create_directly_as_received_rejected(self):
        """
        FIX 1: New invoices cannot be created directly as RECEIVED.
        """
        new_inv = PurchaseInvoice(
            invoice_number="PUR-NEW-RCV-005",
            supplier=self.supplier,
            status=PurchaseStatus.RECEIVED
        )
        with self.assertRaises(ValidationError) as ctx:
            new_inv.save()
        self.assertIn('status', ctx.exception.message_dict)

    def test_moving_item_from_received_to_draft_invoice_rejected(self):
        """
        FIX 2: Moving a PurchaseItem from a RECEIVED invoice to a DRAFT invoice must be rejected.
        """
        rcv_inv = PurchaseInvoice.objects.create(
            invoice_number="PUR-RCV-MOV-006",
            supplier=self.supplier
        )
        item = PurchaseItem.objects.create(
            purchase=rcv_inv,
            raw_material=self.flour,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('45.00')
        )
        PurchaseInvoice.objects.filter(pk=rcv_inv.pk).update(status=PurchaseStatus.RECEIVED)
        rcv_inv.refresh_from_db()

        draft_inv = PurchaseInvoice.objects.create(
            invoice_number="PUR-DFT-MOV-006",
            supplier=self.supplier
        )

        item.purchase = draft_inv
        with self.assertRaises(ValidationError) as ctx:
            item.save()
        self.assertIn('purchase', ctx.exception.message_dict)

    def test_moving_item_between_two_draft_invoices_rejected(self):
        """
        FIX 2: Moving a PurchaseItem between two DRAFT invoices is also strictly rejected.
        """
        draft1 = PurchaseInvoice.objects.create(
            invoice_number="PUR-DFT-A-007",
            supplier=self.supplier
        )
        item = PurchaseItem.objects.create(
            purchase=draft1,
            raw_material=self.flour,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('45.00')
        )

        draft2 = PurchaseInvoice.objects.create(
            invoice_number="PUR-DFT-B-007",
            supplier=self.supplier
        )

        item.purchase = draft2
        with self.assertRaises(ValidationError) as ctx:
            item.save()
        self.assertIn('purchase', ctx.exception.message_dict)

    def test_normal_item_editing_on_draft_invoice_allowed(self):
        """
        FIX 2: Editing quantity and unit_price on existing items belonging to a DRAFT invoice works normally.
        """
        draft_inv = PurchaseInvoice.objects.create(
            invoice_number="PUR-DFT-EDIT-008",
            supplier=self.supplier
        )
        item = PurchaseItem.objects.create(
            purchase=draft_inv,
            raw_material=self.flour,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('45.00')
        )
        draft_inv.refresh_from_db()
        self.assertEqual(draft_inv.total_amount, Decimal('450.00'))

        # Update quantity
        item.quantity = Decimal('20.0000')
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal('20.0000'))
        draft_inv.refresh_from_db()
        self.assertEqual(draft_inv.total_amount, Decimal('900.00'))


class PurchaseReceivingServiceTests(TestCase):
    """Focused tests for atomic purchase receiving."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='receiving_tester',
            password='Password123!'
        )
        self.user.profile.role = 'ACCOUNTANT'
        self.user.profile.save()
        self.supplier = Supplier.objects.create(
            name='Receiving Supplier',
            contact_number='9876501234',
            address='Madurai',
            state='Tamil Nadu'
        )
        self.flour = RawMaterial.objects.create(
            name='Rice Flour',
            code='RM-RCV-FLOUR',
            unit=UnitChoices.KG,
            standard_rate=Decimal('42.00')
        )
        self.oil = RawMaterial.objects.create(
            name='Cooking Oil',
            code='RM-RCV-OIL',
            unit=UnitChoices.LITRE,
            standard_rate=Decimal('140.00')
        )

    def create_draft_invoice(self, number='PUR-RCV-100'):
        return PurchaseInvoice.objects.create(
            invoice_number=number,
            supplier=self.supplier,
            created_by=self.user
        )

    def create_receipt_source(self, number, quantity, unit_price):
        invoice = self.create_draft_invoice(number)
        item = PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.flour,
            quantity=quantity,
            unit_price=unit_price,
        )
        receive_purchase_invoice(invoice.pk, created_by=self.user)
        return invoice, item, InventorySource.objects.get(purchase_item=item)

    def test_inventory_source_created_during_receiving(self):
        invoice, item, source = self.create_receipt_source(
            'PUR-SOURCE-CREATE', Decimal('10.0000'), Decimal('45.00')
        )
        self.assertEqual(source.original_quantity, Decimal('10.0000'))
        self.assertEqual(source.remaining_quantity, Decimal('10.0000'))
        self.assertEqual(source.raw_material, self.flour)
        self.assertEqual(source.purchase_item, item)
        self.assertEqual(source.source_reference, invoice.invoice_number)
        self.assertEqual(source.unit_cost, Decimal('45.0000'))

    def test_fifo_consumption_across_sources(self):
        _, first_item, first = self.create_receipt_source(
            'PUR-SOURCE-FIFO-1', Decimal('5.0000'), Decimal('40.00')
        )
        _, second_item, second = self.create_receipt_source(
            'PUR-SOURCE-FIFO-2', Decimal('7.0000'), Decimal('50.00')
        )
        entry = create_stock_ledger_entry(
            movement_type=StockMovementType.OUT,
            raw_material=self.flour,
            quantity=Decimal('8.0000'),
            reference_number='FIFO-CONSUME',
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.remaining_quantity, Decimal('0.0000'))
        self.assertEqual(second.remaining_quantity, Decimal('4.0000'))
        allocations = entry.source_allocations.order_by('source_id')
        self.assertEqual(
            list(allocations.values_list('quantity', flat=True)),
            [Decimal('5.0000'), Decimal('3.0000')],
        )

    def test_partial_and_full_source_consumption(self):
        _, _, source = self.create_receipt_source(
            'PUR-SOURCE-PARTIAL', Decimal('10.0000'), Decimal('45.00')
        )
        create_stock_ledger_entry(
            movement_type=StockMovementType.OUT,
            raw_material=self.flour,
            quantity=Decimal('3.0000'),
            reference_number='PARTIAL-CONSUME',
        )
        source.refresh_from_db()
        self.assertEqual(source.remaining_quantity, Decimal('7.0000'))
        create_stock_ledger_entry(
            movement_type=StockMovementType.OUT,
            raw_material=self.flour,
            quantity=Decimal('7.0000'),
            reference_number='FULL-CONSUME',
        )
        source.refresh_from_db()
        self.assertEqual(source.remaining_quantity, Decimal('0.0000'))

    def test_source_allocation_prevents_negative_stock_and_rolls_back(self):
        _, _, source = self.create_receipt_source(
            'PUR-SOURCE-ROLLBACK', Decimal('5.0000'), Decimal('45.00')
        )
        with self.assertRaises(ValidationError):
            create_stock_ledger_entry(
                movement_type=StockMovementType.OUT,
                raw_material=self.flour,
                quantity=Decimal('6.0000'),
                reference_number='FAILED-ALLOCATION',
            )
        source.refresh_from_db()
        self.assertEqual(source.remaining_quantity, Decimal('5.0000'))
        self.assertFalse(
            StockLedgerEntry.objects.filter(reference_number='FAILED-ALLOCATION').exists()
        )

    def test_duplicate_consumption_reference_does_not_duplicate_allocation(self):
        _, _, source = self.create_receipt_source(
            'PUR-SOURCE-DUP', Decimal('5.0000'), Decimal('45.00')
        )
        create_stock_ledger_entry(
            movement_type=StockMovementType.OUT,
            raw_material=self.flour,
            quantity=Decimal('2.0000'),
            reference_number='DUP-CONSUME',
        )
        with self.assertRaises(ValidationError):
            create_stock_ledger_entry(
                movement_type=StockMovementType.OUT,
                raw_material=self.flour,
                quantity=Decimal('2.0000'),
                reference_number='DUP-CONSUME',
            )
        source.refresh_from_db()
        self.assertEqual(source.remaining_quantity, Decimal('3.0000'))
        self.assertEqual(
            InventorySourceAllocation.objects.filter(
                stock_ledger_entry__reference_number='DUP-CONSUME'
            ).count(),
            1,
        )

    def test_duplicate_source_allocation_is_rejected(self):
        _, _, source = self.create_receipt_source(
            'PUR-SOURCE-ALLOC-DUP', Decimal('5.0000'), Decimal('45.00')
        )
        entry = create_stock_ledger_entry(
            movement_type=StockMovementType.OUT,
            raw_material=self.flour,
            quantity=Decimal('2.0000'),
            reference_number='ALLOC-DUP-CONSUME',
        )
        allocation = entry.source_allocations.get(source=source)
        with self.assertRaises(ValidationError):
            InventorySourceAllocation.objects.create(
                stock_ledger_entry=entry,
                source=source,
                quantity=allocation.quantity,
            )

    def test_unused_receipt_can_be_cancelled_and_source_is_reversed(self):
        invoice, _, source = self.create_receipt_source(
            'PUR-SOURCE-CANCEL', Decimal('5.0000'), Decimal('45.00')
        )
        cancel_purchase_invoice(invoice.pk, cancelled_by=self.user)
        invoice.refresh_from_db()
        source.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.CANCELLED)
        self.assertEqual(source.remaining_quantity, Decimal('0.0000'))

    def test_consumed_receipt_cannot_be_cancelled(self):
        invoice, _, source = self.create_receipt_source(
            'PUR-SOURCE-CANCEL-CONSUMED', Decimal('5.0000'), Decimal('45.00')
        )
        create_stock_ledger_entry(
            movement_type=StockMovementType.OUT,
            raw_material=self.flour,
            quantity=Decimal('1.0000'),
            reference_number='SOURCE-CANCEL-CONSUME',
        )
        with self.assertRaises(ValidationError):
            cancel_purchase_invoice(invoice.pk, cancelled_by=self.user)
        invoice.refresh_from_db()
        source.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.RECEIVED)
        self.assertEqual(source.remaining_quantity, Decimal('4.0000'))

    def test_cancelling_one_receipt_does_not_block_unrelated_receipt(self):
        first, _, first_source = self.create_receipt_source(
            'PUR-SOURCE-UNRELATED-1', Decimal('5.0000'), Decimal('45.00')
        )
        second, _, second_source = self.create_receipt_source(
            'PUR-SOURCE-UNRELATED-2', Decimal('5.0000'), Decimal('50.00')
        )
        create_stock_ledger_entry(
            movement_type=StockMovementType.OUT,
            raw_material=self.flour,
            quantity=Decimal('5.0000'),
            reference_number='UNRELATED-CONSUME',
        )
        with self.assertRaises(ValidationError):
            cancel_purchase_invoice(first.pk, cancelled_by=self.user)
        cancel_purchase_invoice(second.pk, cancelled_by=self.user)
        first_source.refresh_from_db()
        second_source.refresh_from_db()
        self.assertEqual(first_source.remaining_quantity, Decimal('0.0000'))
        self.assertEqual(second_source.remaining_quantity, Decimal('0.0000'))

    def test_legacy_stock_is_explicitly_bucketed_without_purchase_lot(self):
        create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            raw_material=self.flour,
            quantity=Decimal('4.0000'),
            reference_number='LEGACY-OPENING',
        )
        entry = create_stock_ledger_entry(
            movement_type=StockMovementType.OUT,
            raw_material=self.flour,
            quantity=Decimal('2.0000'),
            reference_number='LEGACY-CONSUME',
        )
        source = InventorySource.objects.get(
            raw_material=self.flour,
            source_type=InventorySourceType.LEGACY,
        )
        self.assertIsNone(source.purchase_item_id)
        self.assertEqual(source.original_quantity, Decimal('4.0000'))
        self.assertEqual(source.remaining_quantity, Decimal('2.0000'))
        self.assertEqual(entry.source_allocations.get(source=source).quantity, Decimal('2.0000'))

    def test_receiving_requires_purchase_role(self):
        invoice = self.create_draft_invoice('PUR-RCV-AUTH')
        unauthorized = User.objects.create_user(username='salesman')
        with self.assertRaises(Exception):
            receive_purchase_invoice(invoice.pk, created_by=unauthorized)

    def test_cancellation_rejects_source_with_post_receipt_consumption(self):
        invoice = self.create_draft_invoice('PUR-RCV-SOURCE')
        PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('5.0000'),
            unit_price=Decimal('45.00'),
        )
        receive_purchase_invoice(invoice.pk, created_by=self.user)
        create_stock_ledger_entry(
            movement_type=StockMovementType.OUT,
            raw_material=self.flour,
            quantity=Decimal('5.0000'),
            reference_number='CONSUME-SOURCE',
        )
        with self.assertRaises(ValidationError):
            cancel_purchase_invoice(invoice.pk, cancelled_by=self.user)

    def test_01_successful_single_item_receiving(self):
        invoice = self.create_draft_invoice()
        PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('60.0000'),
            unit_price=Decimal('45.00')
        )

        received = receive_purchase_invoice(invoice.pk, created_by=self.user)

        received.refresh_from_db()
        self.assertEqual(received.status, PurchaseStatus.RECEIVED)
        entries = StockLedgerEntry.objects.filter(reference_number='PUR-RCV-100')
        self.assertEqual(entries.count(), 1)
        entry = entries.get()
        self.assertEqual(entry.movement_type, StockMovementType.IN)
        self.assertEqual(entry.quantity, Decimal('60.0000'))
        self.assertEqual(entry.unit, UnitChoices.KG)
        self.assertEqual(entry.raw_material, self.flour)
        self.assertEqual(entry.created_by, self.user)

    def test_02_successful_multiple_item_receiving(self):
        invoice = self.create_draft_invoice('PUR-RCV-101')
        PurchaseItem.objects.create(
            purchase=invoice, raw_material=self.flour,
            quantity=Decimal('60.0000'), unit_price=Decimal('45.00')
        )
        PurchaseItem.objects.create(
            purchase=invoice, raw_material=self.oil,
            quantity=Decimal('20.0000'), unit_price=Decimal('142.50')
        )

        receive_purchase_invoice(invoice.pk, created_by=self.user)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.RECEIVED)
        entries = StockLedgerEntry.objects.filter(
            reference_number='PUR-RCV-101',
            movement_type=StockMovementType.IN
        )
        self.assertEqual(entries.count(), 2)
        quantities = {
            entries.get(raw_material=self.flour).quantity,
            entries.get(raw_material=self.oil).quantity,
        }
        self.assertEqual(
            quantities,
            {Decimal('60.0000'), Decimal('20.0000')}
        )

    def test_wac_first_purchase_sets_weighted_average_rate(self):
        invoice = self.create_draft_invoice('PUR-WAC-001')
        PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('45.00')
        )

        receive_purchase_invoice(invoice.pk, created_by=self.user)

        self.flour.refresh_from_db()
        self.assertEqual(self.flour.weighted_average_rate, Decimal('45.0000'))

    def test_wac_multiple_receipts_use_weighted_average_rate(self):
        first = self.create_draft_invoice('PUR-WAC-002')
        PurchaseItem.objects.create(
            purchase=first,
            raw_material=self.flour,
            quantity=Decimal('10.0000'),
            unit_price=Decimal('40.00')
        )
        receive_purchase_invoice(first.pk, created_by=self.user)

        second = self.create_draft_invoice('PUR-WAC-003')
        PurchaseItem.objects.create(
            purchase=second,
            raw_material=self.flour,
            quantity=Decimal('20.0000'),
            unit_price=Decimal('50.00')
        )
        receive_purchase_invoice(second.pk, created_by=self.user)

        self.flour.refresh_from_db()
        self.assertEqual(self.flour.weighted_average_rate, Decimal('46.6667'))

    def test_wac_zero_opening_stock_ignores_legacy_standard_rate(self):
        self.flour.standard_rate = Decimal('99.00')
        self.flour.save()
        invoice = self.create_draft_invoice('PUR-WAC-004')
        PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.flour,
            quantity=Decimal('2.0000'),
            unit_price=Decimal('41.25')
        )

        receive_purchase_invoice(invoice.pk, created_by=self.user)

        self.flour.refresh_from_db()
        self.assertEqual(self.flour.weighted_average_rate, Decimal('41.2500'))

    def test_wac_decimal_precision_and_invalid_quantities(self):
        self.assertEqual(
            calculate_weighted_average_rate(
                Decimal('3.0000'),
                Decimal('10.0000'),
                Decimal('2.0000'),
                Decimal('11.0000'),
            ),
            Decimal('10.4000'),
        )
        with self.assertRaises(ValidationError):
            calculate_weighted_average_rate(
                Decimal('0.0000'),
                Decimal('10.0000'),
                Decimal('0.0000'),
                Decimal('11.0000'),
            )
        with self.assertRaises(ValidationError):
            calculate_weighted_average_rate(
                Decimal('0.0000'),
                Decimal('10.0000'),
                Decimal('-1.0000'),
                Decimal('11.0000'),
            )
        with self.assertRaises(ValidationError):
            calculate_weighted_average_rate(
                Decimal('0.0000'),
                Decimal('10.0000'),
                Decimal('1.0000'),
                11.0,
            )

    def test_03_already_received_invoice_rejected(self):
        invoice = self.create_draft_invoice('PUR-RCV-102')
        PurchaseItem.objects.create(
            purchase=invoice, raw_material=self.flour,
            quantity=Decimal('10.0000'), unit_price=Decimal('45.00')
        )
        receive_purchase_invoice(invoice.pk, created_by=self.user)

        with self.assertRaises(ValidationError) as ctx:
            receive_purchase_invoice(invoice.pk, created_by=self.user)
        self.assertIn("already been received", str(ctx.exception))
        self.assertEqual(
            StockLedgerEntry.objects.filter(reference_number='PUR-RCV-102').count(),
            1
        )

    def test_04_cancelled_invoice_rejected(self):
        invoice = self.create_draft_invoice('PUR-RCV-103')
        PurchaseItem.objects.create(
            purchase=invoice, raw_material=self.flour,
            quantity=Decimal('10.0000'), unit_price=Decimal('45.00')
        )
        invoice.status = PurchaseStatus.CANCELLED
        invoice.save()

        with self.assertRaises(ValidationError) as ctx:
            receive_purchase_invoice(invoice.pk, created_by=self.user)
        self.assertIn("cancelled", str(ctx.exception).lower())
        self.assertFalse(
            StockLedgerEntry.objects.filter(reference_number='PUR-RCV-103').exists()
        )

    def test_05_nonexistent_invoice_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            receive_purchase_invoice(999999, created_by=self.user)
        self.assertIn("does not exist", str(ctx.exception))

    def test_06_empty_invoice_rejected(self):
        invoice = self.create_draft_invoice('PUR-RCV-104')

        with self.assertRaises(ValidationError) as ctx:
            receive_purchase_invoice(invoice.pk, created_by=self.user)

        self.assertIn("without purchase items", str(ctx.exception))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.DRAFT)
        self.assertFalse(
            StockLedgerEntry.objects.filter(reference_number='PUR-RCV-104').exists()
        )

    def test_07_unit_mismatch_rejected(self):
        invoice = self.create_draft_invoice('PUR-RCV-105')
        item = PurchaseItem.objects.create(
            purchase=invoice, raw_material=self.flour,
            quantity=Decimal('10.0000'), unit_price=Decimal('45.00')
        )
        PurchaseItem.objects.filter(pk=item.pk).update(unit=UnitChoices.LITRE)

        with self.assertRaises(ValidationError) as ctx:
            receive_purchase_invoice(invoice.pk, created_by=self.user)

        self.assertIn("does not match", str(ctx.exception))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.DRAFT)
        self.assertFalse(
            StockLedgerEntry.objects.filter(reference_number='PUR-RCV-105').exists()
        )

    def test_08_invalid_quantity_rejected(self):
        invoice = self.create_draft_invoice('PUR-RCV-106')
        item = PurchaseItem.objects.create(
            purchase=invoice, raw_material=self.flour,
            quantity=Decimal('10.0000'), unit_price=Decimal('45.00')
        )
        PurchaseItem.objects.filter(pk=item.pk).update(quantity=Decimal('0.0000'))

        with self.assertRaises(ValidationError) as ctx:
            receive_purchase_invoice(invoice.pk, created_by=self.user)

        self.assertIn('quantity', ctx.exception.message_dict)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.DRAFT)

    def test_09_duplicate_receiving_prevention(self):
        invoice = self.create_draft_invoice('PUR-RCV-107')
        PurchaseItem.objects.create(
            purchase=invoice, raw_material=self.flour,
            quantity=Decimal('10.0000'), unit_price=Decimal('45.00')
        )

        receive_purchase_invoice(invoice.pk, created_by=self.user)
        with self.assertRaises(ValidationError):
            receive_purchase_invoice(invoice.pk, created_by=self.user)

        self.assertEqual(
            StockLedgerEntry.objects.filter(
                reference_number='PUR-RCV-107',
                raw_material=self.flour,
                movement_type=StockMovementType.IN
            ).count(),
            1
        )

    def test_10_second_item_failure_rolls_back_first_entry(self):
        invoice = self.create_draft_invoice('PUR-RCV-108')
        PurchaseItem.objects.create(
            purchase=invoice, raw_material=self.flour,
            quantity=Decimal('10.0000'), unit_price=Decimal('45.00')
        )
        PurchaseItem.objects.create(
            purchase=invoice, raw_material=self.oil,
            quantity=Decimal('5.0000'), unit_price=Decimal('140.00')
        )

        call_count = {'value': 0}

        def fail_on_second(**kwargs):
            call_count['value'] += 1
            if call_count['value'] == 2:
                raise ValidationError("Second item failed.")
            return create_stock_ledger_entry(**kwargs)

        with patch(
            'purchases.services.create_stock_ledger_entry',
            side_effect=fail_on_second
        ):
            with self.assertRaises(ValidationError):
                receive_purchase_invoice(invoice.pk, created_by=self.user)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.DRAFT)
        self.assertFalse(
            StockLedgerEntry.objects.filter(reference_number='PUR-RCV-108').exists()
        )

    def test_11_ledger_failure_rolls_back_invoice_status(self):
        invoice = self.create_draft_invoice('PUR-RCV-109')
        PurchaseItem.objects.create(
            purchase=invoice, raw_material=self.flour,
            quantity=Decimal('10.0000'), unit_price=Decimal('45.00')
        )

        with patch(
            'purchases.services.create_stock_ledger_entry',
            side_effect=IntegrityError("forced ledger failure")
        ):
            with self.assertRaises(ValidationError):
                receive_purchase_invoice(invoice.pk, created_by=self.user)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.DRAFT)
        self.assertFalse(
            StockLedgerEntry.objects.filter(reference_number='PUR-RCV-109').exists()
        )


class PurchaseRequestWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='purchase-request-user',
            password='Password123!',
        )
        self.user.profile.role = Role.ACCOUNTANT
        self.user.profile.save()
        self.client = Client()
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(
            name='Request Supplier',
            contact_number='9876543210',
            address='Supplier Road',
        )
        self.materials = [
            RawMaterial.objects.create(
                name=f'Request Material {index}',
                code=f'REQ-MAT-{index}',
                unit=unit,
                standard_rate=Decimal('10.00'),
            )
            for index, unit in enumerate(
                (UnitChoices.KG, UnitChoices.LITRE, UnitChoices.PIECES),
                start=1,
            )
        ]

    def purchase_payload(self, invoice_number, items, existing=()):
        data = {
            'invoice_number': invoice_number,
            'supplier': str(self.supplier.pk),
            'purchase_date': '2026-11-25',
            'notes': '',
            'items-TOTAL_FORMS': str(len(items)),
            'items-INITIAL_FORMS': str(len(existing)),
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
        }
        for index, item in enumerate(items):
            if index < len(existing):
                data[f'items-{index}-id'] = str(existing[index].pk)
            data.update({
                f'items-{index}-raw_material': str(item['material'].pk),
                f'items-{index}-quantity': str(item.get('quantity', '1.0000')),
                f'items-{index}-unit': item['material'].unit,
                f'items-{index}-unit_price': str(item.get('unit_price', '10.00')),
            })
        return data

    def test_create_edit_receive_and_view_multi_item_purchase(self):
        create_url = reverse('purchases:create')
        list_url = reverse('purchases:list')
        response = self.client.post(
            create_url,
            self.purchase_payload('REQ-PUR-1', [
                {'material': self.materials[0], 'quantity': '2', 'unit_price': '10'},
                {'material': self.materials[1], 'quantity': '3', 'unit_price': '20'},
            ]),
        )
        self.assertRedirects(response, list_url)
        invoice = PurchaseInvoice.objects.get(invoice_number='REQ-PUR-1')
        self.assertEqual(invoice.items.count(), 2)
        self.assertEqual(invoice.total_amount, Decimal('80.00'))

        edit_url = reverse('purchases:update', args=[invoice.pk])
        existing_items = list(invoice.items.order_by('pk'))
        response = self.client.post(
            edit_url,
            self.purchase_payload('REQ-PUR-1', [
                {'material': self.materials[0], 'quantity': '4', 'unit_price': '11'},
                {'material': self.materials[1], 'quantity': '3', 'unit_price': '20'},
                {'material': self.materials[2], 'quantity': '5', 'unit_price': '2'},
            ], existing_items),
        )
        detail_url = reverse('purchases:detail', args=[invoice.pk])
        self.assertRedirects(response, detail_url)
        invoice.refresh_from_db()
        self.assertEqual(invoice.items.count(), 3)
        self.assertEqual(invoice.total_amount, Decimal('114.00'))
        self.assertContains(self.client.get(detail_url), 'Request Material 3')

        receive_url = reverse('purchases:receive', args=[invoice.pk])
        self.assertRedirects(self.client.post(receive_url), list_url)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.RECEIVED)
        self.assertEqual(get_raw_material_stock_balance(self.materials[0]), Decimal('4.0000'))
        self.assertEqual(get_raw_material_stock_balance(self.materials[1]), Decimal('3.0000'))
        self.assertEqual(get_raw_material_stock_balance(self.materials[2]), Decimal('5.0000'))

        response = self.client.post(
            edit_url,
            self.purchase_payload('REQ-PUR-1', [
                {'material': self.materials[0], 'quantity': '9'},
            ], existing_items[:1]),
        )
        self.assertRedirects(response, detail_url)
        invoice.refresh_from_db()
        self.assertEqual(invoice.items.count(), 3)
        self.assertEqual(get_raw_material_stock_balance(self.materials[0]), Decimal('4.0000'))

    def test_create_rejects_duplicate_material_and_empty_formset(self):
        url = reverse('purchases:create')
        duplicate = self.client.post(
            url,
            self.purchase_payload('REQ-PUR-DUP', [
                {'material': self.materials[0]},
                {'material': self.materials[0]},
            ]),
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(PurchaseInvoice.objects.filter(invoice_number='REQ-PUR-DUP').exists())

        empty_payload = self.purchase_payload(
            'REQ-PUR-EMPTY',
            [{'material': self.materials[0], 'quantity': '', 'unit_price': ''}],
        )
        for field in ('raw_material', 'unit'):
            empty_payload[f'items-0-{field}'] = ''
        empty = self.client.post(url, empty_payload)
        self.assertEqual(empty.status_code, 200)
        self.assertFalse(PurchaseInvoice.objects.filter(invoice_number='REQ-PUR-EMPTY').exists())
        self.assertContains(empty, 'must contain at least one item')

    def test_cancel_requires_confirmation_and_preserves_audit_state(self):
        invoice = PurchaseInvoice.objects.create(
            invoice_number='REQ-PUR-CANCEL',
            supplier=self.supplier,
            created_by=self.user,
        )
        PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.materials[0],
            quantity=Decimal('2.0000'),
            unit_price=Decimal('10.00'),
        )
        cancel_url = reverse('purchases:cancel', args=[invoice.pk])
        self.assertEqual(self.client.get(cancel_url).status_code, 200)
        self.assertContains(self.client.get(cancel_url), 'Confirm purchase cancellation')
        response = self.client.post(cancel_url, {'reason': 'Duplicate draft'})
        self.assertRedirects(response, reverse('purchases:detail', args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseStatus.CANCELLED)
        self.assertEqual(StockLedgerEntry.objects.count(), 0)

    def test_read_only_role_cannot_create_or_edit_purchases(self):
        viewer = User.objects.create_user(
            username='purchase-viewer',
            password='Password123!',
        )
        viewer.profile.role = Role.SALESMAN
        viewer.profile.save()
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('purchases:list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('purchases:create')).status_code, 403)

