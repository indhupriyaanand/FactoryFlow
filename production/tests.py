import csv
import io
from django.test import TestCase

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import InventorySource, StockLedgerEntry, StockMovementType
from inventory.services import create_stock_ledger_entry
from masters.models import (
    BillOfMaterials,
    Product,
    RawMaterial,
    Supplier,
    UnitChoices,
)
from accounts.models import Role, UserProfile
from core.reports import production_material_variance_csv_response
from purchases.models import PurchaseInvoice, PurchaseItem
from purchases.services import receive_purchase_invoice
from production.services import (
    approve_production_order,
    cancel_production_order,
    complete_production_order,
    create_production_order,
)
from production.models import (
    ProductionApprovalHistory,
    ProductionDamageRecord,
    ProductionDamageType,
)


class ProductionWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="supervisor")
        self.user.profile.role = Role.PRODUCTION_SUPERVISOR
        self.user.profile.save()
        self.product = Product.objects.create(name="Murukku", code="MUR-200")
        self.material = RawMaterial.objects.create(
            name="Flour", code="RM-FLOUR", unit=UnitChoices.KG,
            standard_rate=Decimal("10.00"),
        )
        BillOfMaterials.objects.create(
            product=self.product,
            raw_material=self.material,
            quantity=Decimal("2.0000"),
            unit=UnitChoices.KG,
            batch_size=Decimal("10.00"),
        )

    def test_actual_consumption_and_cost_are_recorded(self):
        create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            raw_material=self.material,
            quantity=Decimal("3.0000"),
            reference_number="OPENING-PROD",
        )
        order = create_production_order(self.product, Decimal("10.0000"), user=self.user)
        with self.assertRaises(ValidationError):
            complete_production_order(order.pk, user=self.user)
        approve_production_order(order.pk, self.user)
        completed = complete_production_order(
            order.pk,
            user=self.user,
            actual_material_quantities={self.material.pk: Decimal("2.4000")},
            labour_cost=Decimal("5.00"),
            power_cost=Decimal("3.00"),
        )
        self.assertEqual(completed.material_cost, Decimal("24.00"))
        self.assertEqual(completed.unit_cost, Decimal("3.2000"))
        output_source = InventorySource.objects.get(
            product=self.product,
            source_reference=completed.order_number,
        )
        self.assertEqual(output_source.original_quantity, Decimal('10.0000'))
        self.assertEqual(output_source.unit_cost, Decimal('3.2000'))
        self.assertEqual(
            output_source.stock_ledger_entry.reference_number,
            completed.order_number,
        )

    def test_planned_unit_cost_is_snapshotted_and_variance_uses_fifo_cost(self):
        order = create_production_order(
            self.product,
            Decimal('10.0000'),
            user=self.user,
            order_number='COST-SNAPSHOT-1',
        )
        usage = order.material_usages.get(raw_material=self.material)
        self.assertEqual(usage.planned_unit_rate, Decimal('10.0000'))
        self.assertEqual(usage.planned_cost, Decimal('20.00'))

        purchaser = User.objects.create_user(username='cost-snapshot-purchaser')
        purchaser.profile.role = Role.ACCOUNTANT
        purchaser.profile.save()
        supplier = Supplier.objects.create(
            name='Cost Snapshot Supplier',
            contact_number='9876543210',
            address='Factory Road',
        )
        invoice = PurchaseInvoice.objects.create(
            invoice_number='COST-SNAPSHOT-PUR-1',
            supplier=supplier,
            created_by=purchaser,
        )
        PurchaseItem.objects.create(
            purchase=invoice,
            raw_material=self.material,
            quantity=Decimal('3.0000'),
            unit=UnitChoices.KG,
            unit_price=Decimal('20.00'),
        )
        receive_purchase_invoice(invoice.pk, purchaser)
        usage.refresh_from_db()
        self.material.refresh_from_db()
        self.assertEqual(self.material.weighted_average_rate, Decimal('20.0000'))
        self.assertEqual(usage.planned_unit_rate, Decimal('10.0000'))

        approve_production_order(order.pk, self.user)
        completed = complete_production_order(
            order.pk,
            user=self.user,
            actual_material_quantities={
                self.material.pk: Decimal('2.0000'),
            },
        )
        variance_csv = production_material_variance_csv_response(
            [completed]
        ).content.decode()
        row = next(csv.DictReader(io.StringIO(variance_csv)))
        self.assertEqual(row['Planned Unit Cost Snapshot'], '10.0000')
        self.assertEqual(row['Planned Cost'], '20.00')
        self.assertEqual(row['Actual FIFO Unit Cost'], '20.0000')
        self.assertEqual(Decimal(row['Actual FIFO Cost']), Decimal('40.00'))
        self.assertEqual(row['Cost Variance'], '20.00')
        self.assertEqual(row['Source Cost Basis'], 'FIFO receipt sources')

    def test_completed_order_can_be_cancelled_with_ledger_reversals(self):
        create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            raw_material=self.material,
            quantity=Decimal("3.0000"),
            reference_number="OPENING-PROD-CANCEL",
        )
        order = create_production_order(self.product, Decimal("10.0000"), user=self.user)
        approve_production_order(order.pk, self.user)
        complete_production_order(order.pk, user=self.user)
        output_source = InventorySource.objects.get(
            product=self.product,
            source_reference=order.order_number,
        )
        cancelled = cancel_production_order(order.pk, self.user, "Test cancellation")
        self.assertEqual(cancelled.status, "CANCELLED")
        output_source.refresh_from_db()
        self.assertEqual(output_source.remaining_quantity, Decimal('0.0000'))
        self.assertTrue(
            output_source.allocations.filter(
                stock_ledger_entry__movement_type=StockMovementType.REVERSAL
            ).exists()
        )

    def test_production_order_details_cancel_and_approval_audit_are_safe(self):
        order = create_production_order(self.product, Decimal("10.0000"), user=self.user)
        approve_production_order(order.pk, self.user)
        history_count = ProductionApprovalHistory.objects.filter(
            production_order=order
        ).count()
        with self.assertRaises(ValidationError):
            approve_production_order(order.pk, self.user)
        self.assertEqual(
            ProductionApprovalHistory.objects.filter(production_order=order).count(),
            history_count,
        )

        client = Client()
        client.force_login(self.user)
        detail_url = reverse("production:detail", args=[order.pk])
        self.assertEqual(client.get(detail_url).status_code, 200)
        cancel_url = reverse("production:cancel", args=[order.pk])
        self.assertEqual(client.get(cancel_url).status_code, 200)
        response = client.post(cancel_url, {"reason": "Plan changed"})
        self.assertRedirects(response, reverse("production:list"))
        order.refresh_from_db()
        self.assertEqual(order.status, "CANCELLED")

    def test_unknown_damage_type_returns_404(self):
        client = Client()
        client.force_login(self.user)
        response = client.get(
            reverse("production:damage_create", args=["NOT_A_DAMAGE_TYPE"])
        )
        self.assertEqual(response.status_code, 404)

    def test_completion_formset_records_actual_material_and_fifo_allocation(self):
        create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            raw_material=self.material,
            quantity=Decimal("3.0000"),
            reference_number="OPENING-FORMSET",
        )
        order = create_production_order(self.product, Decimal("10.0000"), user=self.user)
        approve_production_order(order.pk, self.user)
        client = Client()
        client.force_login(self.user)
        response = client.post(
            reverse("production:complete", args=[order.pk]),
            {
                "produced_quantity": "10.0000",
                "damaged_pouch_quantity": "0",
                "labour_cost": "5.00",
                "power_cost": "3.00",
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "1",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-id": str(order.material_usages.get().pk),
                "form-0-actual_quantity": "2.4000",
            },
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        usage = order.material_usages.get()
        self.assertEqual(usage.actual_quantity, Decimal("2.4000"))
        self.assertEqual(
            InventorySource.objects.get(
                source_type="LEGACY", raw_material=self.material
            ).remaining_quantity,
            Decimal("0.6000"),
        )
        duplicate = client.post(
            reverse("production:complete", args=[order.pk]),
            {
                "produced_quantity": "10.0000",
                "damaged_pouch_quantity": "0",
                "labour_cost": "5.00",
                "power_cost": "3.00",
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "1",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-id": str(usage.pk),
                "form-0-actual_quantity": "2.4000",
            },
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(
            StockLedgerEntry.objects.filter(reference_number=order.order_number).count(),
            2,
        )

    def test_completion_formset_supports_multiple_materials(self):
        second_material = RawMaterial.objects.create(
            name="Spice", code="RM-SPICE", unit=UnitChoices.KG,
            standard_rate=Decimal("20.00"),
        )
        BillOfMaterials.objects.create(
            product=self.product,
            raw_material=second_material,
            quantity=Decimal("1.0000"),
            unit=UnitChoices.KG,
            batch_size=Decimal("10.00"),
        )
        for material, reference in (
            (self.material, "OPENING-MULTI-FLOUR"),
            (second_material, "OPENING-MULTI-SPICE"),
        ):
            create_stock_ledger_entry(
                movement_type=StockMovementType.IN,
                raw_material=material,
                quantity=Decimal("3.0000"),
                reference_number=reference,
            )
        order = create_production_order(self.product, Decimal("10.0000"), user=self.user)
        approve_production_order(order.pk, self.user)
        usages = list(order.material_usages.order_by('pk'))
        response = Client()
        response.force_login(self.user)
        result = response.post(
            reverse("production:complete", args=[order.pk]),
            {
                "produced_quantity": "10.0000",
                "damaged_pouch_quantity": "0",
                "labour_cost": "0",
                "power_cost": "0",
                "form-TOTAL_FORMS": "2",
                "form-INITIAL_FORMS": "2",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-id": str(usages[0].pk),
                "form-0-actual_quantity": "2.1000",
                "form-1-id": str(usages[1].pk),
                "form-1-actual_quantity": "1.2000",
            },
        )
        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            order.material_usages.get(raw_material=self.material).actual_quantity,
            Decimal("2.1000"),
        )
        self.assertEqual(
            order.material_usages.get(raw_material=second_material).actual_quantity,
            Decimal("1.2000"),
        )

    def test_completion_formset_rejects_insufficient_stock_without_deduction(self):
        order = create_production_order(self.product, Decimal("10.0000"), user=self.user)
        approve_production_order(order.pk, self.user)
        client = Client()
        client.force_login(self.user)
        response = client.post(
            reverse("production:complete", args=[order.pk]),
            {
                "produced_quantity": "10.0000",
                "damaged_pouch_quantity": "0",
                "labour_cost": "0",
                "power_cost": "0",
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "1",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-id": str(order.material_usages.get().pk),
                "form-0-actual_quantity": "2.0000",
            },
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "PLANNED")
        self.assertFalse(
            StockLedgerEntry.objects.filter(reference_number=order.order_number).exists()
        )

    def test_completion_endpoint_requires_production_role(self):
        order = create_production_order(self.product, Decimal("10.0000"), user=self.user)
        unauthorized = User.objects.create_user(username="sales-user")
        unauthorized.profile.role = Role.SALESMAN
        unauthorized.profile.save()
        client = Client()
        client.force_login(unauthorized)
        self.assertEqual(
            client.get(reverse("production:complete", args=[order.pk])).status_code,
            403,
        )

    def test_damage_create_edit_list_and_void_reverses_ledger(self):
        pouch = RawMaterial.objects.create(
            name="Pouch", code="RM-POUCH", unit=UnitChoices.PIECES,
            standard_rate=Decimal("1.00"), is_pouch=True,
        )
        create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            raw_material=pouch,
            quantity=Decimal("10.0000"),
            reference_number="OPENING-DAMAGE",
        )
        order = create_production_order(self.product, Decimal("10.0000"), user=self.user)
        client = Client()
        client.force_login(self.user)
        response = client.post(
            reverse("production:damage_create", args=[ProductionDamageType.DAMAGED_POUCH]),
            {
                "production_order": order.pk,
                "raw_material": pouch.pk,
                "product": "",
                "quantity": "2.0000",
                "unit": UnitChoices.PIECES,
                "reason": "Seal failure",
                "recorded_at": "2026-11-24T10:00",
            },
        )
        self.assertEqual(response.status_code, 302)
        record = ProductionDamageRecord.objects.get()
        self.assertEqual(record.stock_ledger_entry.quantity, Decimal("2.0000"))
        self.assertEqual(
            StockLedgerEntry.objects.filter(
                reference_number=f"DAMAGE-{record.pk}"
            ).count(),
            1,
        )
        self.assertEqual(
            client.get(reverse("production:damage_list")).status_code,
            200,
        )
        self.assertEqual(
            client.post(
                reverse("production:damage_edit", args=[record.pk]),
                {"reason": "Updated reason", "recorded_at": "2026-11-24T11:00"},
            ).status_code,
            302,
        )
        response = client.post(
            reverse("production:damage_void", args=[record.pk]),
            {"reason": "Correction"},
        )
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertTrue(record.is_voided)
        self.assertEqual(
            StockLedgerEntry.objects.filter(
                movement_type=StockMovementType.REVERSAL,
                reversed_entry=record.stock_ledger_entry,
            ).count(),
            1,
        )

    def test_scrap_requires_material_or_product_and_reports_export_csv(self):
        order = create_production_order(self.product, Decimal("10.0000"), user=self.user)
        client = Client()
        client.force_login(self.user)
        invalid = client.post(
            reverse("production:damage_create", args=[ProductionDamageType.SCRAP]),
            {
                "production_order": order.pk,
                "quantity": "1.0000",
                "unit": UnitChoices.PACKET,
                "reason": "Invalid",
                "recorded_at": "2026-11-24T10:00",
            },
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(ProductionDamageRecord.objects.count(), 0)
