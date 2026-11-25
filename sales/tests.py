import csv
import io
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.test import Client, TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from inventory.models import (
    InventorySourceAllocation,
    InventorySourceType,
    StockLedgerEntry,
    StockMovementType,
)
from inventory.services import (
    create_inventory_source_record,
    create_stock_ledger_entry,
    get_inventory_source_cost,
    get_product_stock_balance,
)
from masters.models import Customer, Product
from sales.billing import calculate_gst_breakdown, create_sales_invoice, record_payment
from sales.models import (
    DispatchRecord,
    PaymentStatus,
    SalesApprovalHistory,
    SalesApprovalStatus,
    SalesInvoiceLine,
    SalesShortage,
    SalesOrder,
    SalesStatus,
)
from accounts.models import Role
from core.reports import product_profitability_csv_response
from sales.services import (
    cancel_sales_order,
    approve_sales_order,
    confirm_sales_order,
    create_sales_order,
    dispatch_sales_order,
    reject_sales_order,
)


class SalesOrderRequestTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="order-admin", password="test-pass")
        self.admin.profile.role = Role.ADMIN
        self.admin.profile.save()
        self.salesman = User.objects.create_user(username="order-salesman", password="test-pass")
        self.salesman.profile.role = Role.SALESMAN
        self.salesman.profile.save()
        self.other_role_user = User.objects.create_user(
            username="order-production", password="test-pass"
        )
        self.other_role_user.profile.role = Role.PRODUCTION_SUPERVISOR
        self.other_role_user.profile.save()
        self.customer = Customer.objects.create(
            name="Request Customer",
            contact_number="9999999999",
            address="Address",
        )
        self.products = [
            Product.objects.create(
                name=f"Request Product {index}",
                code=f"REQ-{index}",
                unit_price=Decimal("10.00") * index,
            )
            for index in (1, 2)
        ]
        self.client = Client()
        self.client.force_login(self.admin)

    def create_payload(self, order_number, lines):
        payload = {
            "order_number": order_number,
            "customer": self.customer.pk,
            "order_date": "2026-11-25",
            "notes": "",
            "items-TOTAL_FORMS": str(len(lines)),
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
        }
        for index, line in enumerate(lines):
            payload.update({
                f"items-{index}-product": line.get("product", self.products[index % 2]).pk,
                f"items-{index}-quantity": str(line.get("quantity", "1")),
                f"items-{index}-unit_price": str(line.get("unit_price", "10.00")),
                f"items-{index}-discount_amount": str(line.get("discount_amount", "0.00")),
                f"items-{index}-gst_rate": str(line.get("gst_rate", "5.00")),
            })
        return payload

    def post_create(self, order_number, lines):
        return self.client.post(
            reverse("sales:create_order"),
            self.create_payload(order_number, lines),
        )

    def test_create_request_saves_multiple_items_and_calculates_totals(self):
        response = self.post_create("SO-REQUEST-1", [
            {
                "product": self.products[0],
                "quantity": "2",
                "unit_price": "10.00",
                "discount_amount": "2.00",
            },
            {
                "product": self.products[1],
                "quantity": "1",
                "unit_price": "20.00",
            },
        ])

        self.assertRedirects(response, reverse("sales:order_list"))
        order = SalesOrder.objects.get(order_number="SO-REQUEST-1")
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(order.total_amount, Decimal("38.00"))
        self.assertEqual(order.tax_amount, Decimal("1.90"))
        self.assertEqual(order.grand_total, Decimal("39.90"))

    def test_edit_request_updates_multiple_items_and_recalculates_totals(self):
        response = self.post_create("SO-REQUEST-2", [{
            "product": self.products[0],
            "quantity": "1",
            "unit_price": "10.00",
        }])
        self.assertEqual(response.status_code, 302)
        order = SalesOrder.objects.get(order_number="SO-REQUEST-2")
        original_item_id = order.items.get().pk
        payload = {
            "customer": self.customer.pk,
            "order_date": "2026-11-25",
            "notes": "Updated order",
            "items-TOTAL_FORMS": "2",
            "items-INITIAL_FORMS": "1",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-id": str(original_item_id),
            "items-0-product": str(self.products[0].pk),
            "items-0-quantity": "2",
            "items-0-unit_price": "10.00",
            "items-0-discount_amount": "0.00",
            "items-0-gst_rate": "5.00",
            "items-1-product": str(self.products[1].pk),
            "items-1-quantity": "1",
            "items-1-unit_price": "20.00",
            "items-1-discount_amount": "1.00",
            "items-1-gst_rate": "5.00",
        }

        response = self.client.post(
            reverse("sales:edit_order", args=[order.pk]),
            payload,
        )

        self.assertRedirects(response, reverse("sales:detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.notes, "Updated order")
        self.assertEqual(order.items.count(), 2)
        self.assertFalse(order.items.filter(pk=original_item_id).exists())
        self.assertEqual(order.total_amount, Decimal("39.00"))
        self.assertEqual(order.tax_amount, Decimal("1.95"))
        self.assertEqual(order.grand_total, Decimal("40.95"))

    def test_create_request_rejects_nonpositive_quantity(self):
        response = self.post_create("SO-REQUEST-3", [{
            "product": self.products[0],
            "quantity": "0",
        }])

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SalesOrder.objects.filter(order_number="SO-REQUEST-3").exists())
        self.assertContains(response, "Ensure this value is greater than or equal to 0.0001")

    def test_create_request_rejects_duplicate_product_rows(self):
        response = self.post_create("SO-REQUEST-4", [
            {"product": self.products[0]},
            {"product": self.products[0]},
        ])

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SalesOrder.objects.filter(order_number="SO-REQUEST-4").exists())
        self.assertContains(response, "Each product may appear only once")

    def test_create_request_rejects_invalid_product_selection(self):
        payload = self.create_payload(
            "SO-REQUEST-INVALID-PRODUCT",
            [{"product": self.products[0]}],
        )
        payload["items-0-product"] = "999999"

        response = self.client.post(reverse("sales:create_order"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            SalesOrder.objects.filter(order_number="SO-REQUEST-INVALID-PRODUCT").exists()
        )

    def test_create_request_rejects_invalid_rate_and_discount(self):
        negative_rate = self.post_create("SO-REQUEST-5", [{
            "product": self.products[0],
            "unit_price": "-1.00",
        }])
        self.assertEqual(negative_rate.status_code, 200)
        self.assertFalse(SalesOrder.objects.filter(order_number="SO-REQUEST-5").exists())

        negative_gst = self.post_create("SO-REQUEST-NEGATIVE-GST", [{
            "product": self.products[0],
            "gst_rate": "-1.00",
        }])
        self.assertEqual(negative_gst.status_code, 200)
        self.assertFalse(
            SalesOrder.objects.filter(order_number="SO-REQUEST-NEGATIVE-GST").exists()
        )

        excessive_discount = self.post_create("SO-REQUEST-6", [{
            "product": self.products[0],
            "quantity": "1",
            "unit_price": "10.00",
            "discount_amount": "11.00",
        }])
        self.assertEqual(excessive_discount.status_code, 200)
        self.assertFalse(SalesOrder.objects.filter(order_number="SO-REQUEST-6").exists())
        self.assertContains(excessive_discount, "Discount cannot exceed the line subtotal")

    def test_sales_order_mutations_keep_sales_role_permissions(self):
        self.client.force_login(self.admin)
        created = self.post_create("SO-REQUEST-7", [{
            "product": self.products[0],
        }])
        self.assertEqual(created.status_code, 302)
        order = SalesOrder.objects.get(order_number="SO-REQUEST-7")

        self.client.force_login(self.other_role_user)

        response = self.client.post(
            reverse("sales:create_order"),
            self.create_payload("SO-REQUEST-7", [{"product": self.products[0]}]),
        )

        self.assertEqual(response.status_code, 403)
        edit_response = self.client.post(
            reverse("sales:edit_order", args=[order.pk]),
            {},
        )
        self.assertEqual(edit_response.status_code, 403)

    def test_approval_and_confirmation_requests_unlock_dispatch_in_order(self):
        response = self.post_create("SO-APPROVAL-REQUEST", [{
            "product": self.products[0],
        }])
        self.assertEqual(response.status_code, 302)
        order = SalesOrder.objects.get(order_number="SO-APPROVAL-REQUEST")

        detail = self.client.get(reverse("sales:detail", args=[order.pk]))
        self.assertContains(detail, "Approve order")
        self.assertNotContains(detail, "Confirm for dispatch")
        self.assertEqual(
            self.client.get(reverse("sales:dispatch", args=[order.pk])).status_code,
            302,
        )

        response = self.client.post(reverse("sales:approve_order", args=[order.pk]))
        self.assertRedirects(response, reverse("sales:detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.approval_status, SalesApprovalStatus.APPROVED)
        self.assertEqual(order.status, SalesStatus.DRAFT)
        self.assertTrue(SalesApprovalHistory.objects.filter(
            sales_order=order,
            approval_status=SalesApprovalStatus.APPROVED,
            action_by=self.admin,
        ).exists())
        detail = self.client.get(reverse("sales:detail", args=[order.pk]))
        self.assertContains(detail, "Confirm for dispatch")
        self.assertNotContains(detail, ">Edit</a>")

        self.client.post(reverse("sales:confirm_order", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, SalesStatus.CONFIRMED)
        dispatch_page = self.client.get(reverse("sales:dispatch", args=[order.pk]))
        self.assertEqual(dispatch_page.status_code, 200)

    def test_management_approval_queue_lists_pending_orders_and_blocks_sales_role(self):
        self.post_create("SO-APPROVAL-QUEUE", [{"product": self.products[0]}])
        queue_url = reverse("sales:approval_queue")

        response = self.client.get(queue_url)
        self.assertContains(response, "SO-APPROVAL-QUEUE")
        self.assertContains(response, reverse("sales:approve_order", args=[
            SalesOrder.objects.get(order_number="SO-APPROVAL-QUEUE").pk,
        ]))

        self.client.force_login(self.salesman)
        self.assertEqual(self.client.get(queue_url).status_code, 403)

    def test_rejection_request_requires_reason_and_records_approval_history(self):
        self.post_create("SO-REJECTION-REQUEST", [{"product": self.products[0]}])
        order = SalesOrder.objects.get(order_number="SO-REJECTION-REQUEST")
        reject_url = reverse("sales:reject_order", args=[order.pk])

        missing_reason = self.client.post(reject_url, {"reason": "  "})
        self.assertRedirects(missing_reason, reverse("sales:detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.approval_status, SalesApprovalStatus.PENDING)

        response = self.client.post(reject_url, {"reason": "Incorrect quantity"})
        self.assertRedirects(response, reverse("sales:detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.approval_status, SalesApprovalStatus.REJECTED)
        self.assertTrue(SalesApprovalHistory.objects.filter(
            sales_order=order,
            approval_status=SalesApprovalStatus.REJECTED,
            action_by=self.admin,
            notes="Incorrect quantity",
        ).exists())

    def test_sales_role_cannot_approve_or_reject_order(self):
        self.post_create("SO-UNAUTHORIZED-APPROVAL", [{"product": self.products[0]}])
        order = SalesOrder.objects.get(order_number="SO-UNAUTHORIZED-APPROVAL")
        self.client.force_login(self.salesman)

        approve_response = self.client.post(
            reverse("sales:approve_order", args=[order.pk])
        )
        reject_response = self.client.post(
            reverse("sales:reject_order", args=[order.pk]),
            {"reason": "Unauthorized"},
        )

        self.assertEqual(approve_response.status_code, 403)
        self.assertEqual(reject_response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, SalesApprovalStatus.PENDING)

    def test_approved_but_unconfirmed_order_cannot_open_dispatch_form(self):
        self.post_create("SO-UNCONFIRMED-DISPATCH", [{"product": self.products[0]}])
        order = SalesOrder.objects.get(order_number="SO-UNCONFIRMED-DISPATCH")
        approve_sales_order(order.pk, self.admin)

        response = self.client.get(reverse("sales:dispatch", args=[order.pk]))

        self.assertRedirects(response, reverse("sales:detail", args=[order.pk]))

    def test_multi_product_dispatch_and_invoice_workflow_preserves_quantities(self):
        response = self.post_create("SO-REQUEST-8", [
            {"product": self.products[0], "quantity": "2", "unit_price": "10.00"},
            {"product": self.products[1], "quantity": "3", "unit_price": "20.00"},
        ])
        self.assertEqual(response.status_code, 302)
        order = SalesOrder.objects.get(order_number="SO-REQUEST-8")
        for index, product in enumerate(self.products, start=1):
            create_stock_ledger_entry(
                movement_type=StockMovementType.IN,
                product=product,
                quantity=Decimal("10"),
                reference_number=f"REQUEST-OPENING-{index}",
            )
        approve_sales_order(order.pk, self.admin)
        confirm_sales_order(order.pk, self.admin)
        dispatch_sales_order(order.pk, self.admin)
        invoice = create_sales_invoice(order.pk, self.admin)

        self.assertEqual(invoice.lines.count(), 2)
        self.assertEqual(
            set(order.items.values_list("dispatched_quantity", flat=True)),
            {Decimal("2.0000"), Decimal("3.0000")},
        )
        before = list(order.items.values_list("product_id", "quantity", "dispatched_quantity"))
        payload = {
            "customer": self.customer.pk,
            "order_date": "2026-11-25",
            "notes": "Should not be saved",
            "items-TOTAL_FORMS": "3",
            "items-INITIAL_FORMS": "2",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
        }
        for index, item in enumerate(order.items.order_by("pk")):
            payload.update({
                f"items-{index}-id": str(item.pk),
                f"items-{index}-product": str(item.product_id),
                f"items-{index}-quantity": str(item.quantity + Decimal("1")),
                f"items-{index}-unit_price": str(item.unit_price),
                f"items-{index}-discount_amount": str(item.discount_amount),
                f"items-{index}-gst_rate": str(item.gst_rate),
            })

        edit_response = self.client.post(
            reverse("sales:edit_order", args=[order.pk]),
            payload,
        )

        self.assertEqual(edit_response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.notes, "")
        self.assertEqual(
            list(order.items.values_list("product_id", "quantity", "dispatched_quantity")),
            before,
        )
        self.assertEqual(order.invoice.lines.count(), 2)


class SalesWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="sales-admin")
        self.admin.profile.role = Role.ADMIN
        self.admin.profile.save()
        self.customer = Customer.objects.create(
            name="Customer", contact_number="9999999999", address="Address"
        )
        self.product = Product.objects.create(
            name="Product", code="PRD-1", unit_price=Decimal("10.00")
        )

    def _create_finished_goods_lot(self, reference, quantity, unit_cost, days_ago):
        received_at = timezone.now() - timedelta(days=days_ago)
        entry = create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            product=self.product,
            quantity=quantity,
            reference_number=reference,
            movement_date=received_at,
        )
        return create_inventory_source_record(
            stock_item=self.product,
            stock_ledger_entry=entry,
            source_reference=reference,
            quantity=quantity,
            unit=entry.unit,
            unit_cost=unit_cost,
            source_type=InventorySourceType.PRODUCTION,
            received_at=received_at,
        )

    def test_dispatch_requires_stock_and_updates_ledger(self):
        order = create_sales_order(
            self.customer, [{"product": self.product, "quantity": Decimal("2")}], user=self.admin
        )
        approve_sales_order(order.pk, self.admin)
        confirm_sales_order(order.pk, self.admin)
        with self.assertRaises(PermissionDenied):
            dispatch_sales_order(order.pk)
        create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            product=self.product,
            quantity=Decimal("3"),
            reference_number="OPENING-1",
        )
        dispatched = dispatch_sales_order(order.pk, self.admin)
        self.assertEqual(dispatched.status, SalesStatus.DISPATCHED)
        self.assertEqual(get_product_stock_balance(self.product), Decimal("1.0000"))
        self.assertTrue(DispatchRecord.objects.filter(sales_order=order).exists())

    def test_cancel_dispatch_reverses_out_entry(self):
        create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            product=self.product,
            quantity=Decimal("3"),
            reference_number="OPENING-2",
        )
        order = create_sales_order(
            self.customer, [{"product": self.product, "quantity": Decimal("2")}], user=self.admin
        )
        approve_sales_order(order.pk, self.admin)
        confirm_sales_order(order.pk, self.admin)
        dispatch_sales_order(order.pk, self.admin)
        cancelled = cancel_sales_order(order.pk, self.admin, reason="Customer request")
        self.assertEqual(cancelled.status, SalesStatus.CANCELLED)
        self.assertEqual(get_product_stock_balance(self.product), Decimal("3.0000"))
        self.assertEqual(
            StockLedgerEntry.objects.filter(
                movement_type=StockMovementType.REVERSAL,
                description__contains=order.order_number,
            ).count(),
            1,
        )

    def test_partial_dispatch_allocates_finished_goods_fifo_and_reversal_restores_lots(self):
        first_lot = self._create_finished_goods_lot(
            'FG-LOT-OLD',
            Decimal('2.0000'),
            Decimal('5.0000'),
            days_ago=2,
        )
        second_lot = self._create_finished_goods_lot(
            'FG-LOT-NEW',
            Decimal('4.0000'),
            Decimal('7.0000'),
            days_ago=1,
        )
        order = create_sales_order(
            self.customer,
            [{'product': self.product, 'quantity': Decimal('5.0000')}],
            user=self.admin,
        )
        approve_sales_order(order.pk, self.admin)
        confirm_sales_order(order.pk, self.admin)
        item = order.items.get()

        dispatch_sales_order(
            order.pk,
            self.admin,
            dispatch_quantities={item.pk: Decimal('2.0000')},
        )
        first_dispatch_item = order.dispatch_records.order_by('pk').first().items.get()
        self.assertEqual(
            first_dispatch_item.stock_ledger_entry.source_allocations.get().source,
            first_lot,
        )
        self.assertEqual(
            get_inventory_source_cost(first_dispatch_item.stock_ledger_entry),
            Decimal('10.00000000'),
        )
        report = product_profitability_csv_response(
            Product.objects.filter(pk=self.product.pk)
        )
        row = next(csv.DictReader(io.StringIO(report.content.decode())))
        self.assertEqual(Decimal(row['Line-item Revenue (ex GST)']), Decimal('20.00'))
        self.assertEqual(Decimal(row['Dispatch COGS']), Decimal('10.00000000'))
        self.assertEqual(Decimal(row['Gross Contribution']), Decimal('10.00'))
        first_lot.refresh_from_db()
        self.assertEqual(first_lot.remaining_quantity, Decimal('0.0000'))
        self.assertEqual(order.items.get().dispatched_quantity, Decimal('2.0000'))

        dispatch_sales_order(
            order.pk,
            self.admin,
            dispatch_quantities={item.pk: Decimal('3.0000')},
        )
        second_dispatch_item = order.dispatch_records.order_by('pk').last().items.get()
        second_allocation = second_dispatch_item.stock_ledger_entry.source_allocations.get()
        self.assertEqual(second_allocation.source, second_lot)
        self.assertEqual(second_allocation.quantity, Decimal('3.0000'))
        self.assertEqual(
            get_inventory_source_cost(second_dispatch_item.stock_ledger_entry),
            Decimal('21.00000000'),
        )
        self.assertEqual(order.items.get().dispatched_quantity, Decimal('5.0000'))
        second_lot.refresh_from_db()
        self.assertEqual(second_lot.remaining_quantity, Decimal('1.0000'))

        cancel_sales_order(order.pk, self.admin, reason='Reverse dispatched goods')
        first_lot.refresh_from_db()
        second_lot.refresh_from_db()
        self.assertEqual(first_lot.remaining_quantity, Decimal('2.0000'))
        self.assertEqual(second_lot.remaining_quantity, Decimal('4.0000'))
        self.assertEqual(get_product_stock_balance(self.product), Decimal('6.0000'))
        self.assertEqual(
            InventorySourceAllocation.objects.filter(
                source=first_lot,
                stock_ledger_entry__movement_type=StockMovementType.REVERSAL,
            ).aggregate(total=Sum('quantity'))['total'],
            Decimal('2.0000'),
        )
        self.assertEqual(
            get_inventory_source_cost(
                StockLedgerEntry.objects.get(
                    reversed_entry=first_dispatch_item.stock_ledger_entry
                )
            ),
            Decimal('10.00000000'),
        )

    def test_duplicate_order_item_is_rejected_by_model(self):
        order = create_sales_order(
            self.customer, [{"product": self.product, "quantity": Decimal("1")}], user=self.admin
        )
        with self.assertRaises(ValidationError):
            create_sales_order(
                self.customer,
                [
                    {"product": self.product, "quantity": Decimal("1")},
                    {"product": self.product, "quantity": Decimal("1")},
                ], user=self.admin,
            )
        self.assertEqual(order.items.count(), 1)

    def test_confirmation_requires_approval_and_role(self):
        order = create_sales_order(
            self.customer,
            [{"product": self.product, "quantity": Decimal("1")}],
            user=self.admin,
        )
        with self.assertRaises(ValidationError):
            confirm_sales_order(order.pk, self.admin)
        salesman = User.objects.create_user(username="salesman")
        with self.assertRaises(PermissionDenied):
            approve_sales_order(order.pk, salesman)

    def test_repeated_sales_approval_does_not_duplicate_audit_events(self):
        order = create_sales_order(
            self.customer,
            [{"product": self.product, "quantity": Decimal("1")}],
            user=self.admin,
        )
        approve_sales_order(order.pk, self.admin)
        history_count = SalesApprovalHistory.objects.filter(sales_order=order).count()
        with self.assertRaises(ValidationError):
            approve_sales_order(order.pk, self.admin)
        self.assertEqual(
            SalesApprovalHistory.objects.filter(sales_order=order).count(),
            history_count,
        )

    def test_shortage_is_recorded_when_dispatch_cannot_fulfil_order(self):
        order = create_sales_order(
            self.customer,
            [{"product": self.product, "quantity": Decimal("2")}],
            user=self.admin,
        )
        approve_sales_order(order.pk, self.admin)
        confirm_sales_order(order.pk, self.admin)
        with self.assertRaises(ValidationError):
            dispatch_sales_order(order.pk, self.admin)
        shortage = SalesShortage.objects.get(sales_order=order, product=self.product)
        self.assertEqual(shortage.shortage_quantity, Decimal("2.0000"))

    def test_intra_state_invoice_splits_cgst_and_sgst(self):
        create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            product=self.product,
            quantity=Decimal("3"),
            reference_number="OPENING-GST-1",
        )
        order = create_sales_order(
            self.customer, [{"product": self.product, "quantity": Decimal("2")}], user=self.admin
        )
        approve_sales_order(order.pk, self.admin)
        confirm_sales_order(order.pk, self.admin)
        dispatch_sales_order(order.pk, self.admin)
        invoice = create_sales_invoice(order.pk, self.admin)
        self.assertEqual(invoice.cgst_amount, Decimal("0.50"))
        self.assertEqual(invoice.sgst_amount, Decimal("0.50"))
        self.assertEqual(invoice.igst_amount, Decimal("0.00"))

    def test_inter_state_invoice_uses_igst_and_payment_status(self):
        self.customer.state = "Karnataka"
        self.customer.save()
        create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            product=self.product,
            quantity=Decimal("3"),
            reference_number="OPENING-GST-2",
        )
        order = create_sales_order(
            self.customer, [{"product": self.product, "quantity": Decimal("2")}], user=self.admin
        )
        approve_sales_order(order.pk, self.admin)
        confirm_sales_order(order.pk, self.admin)
        dispatch_sales_order(order.pk, self.admin)
        invoice = create_sales_invoice(order.pk, self.admin)
        self.assertEqual(invoice.igst_amount, Decimal("1.00"))
        self.assertEqual(invoice.lines.count(), 1)
        record_payment(invoice.pk, Decimal("10.00"), user=self.admin)
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, PaymentStatus.PARTIAL)
        record_payment(invoice.pk, Decimal("11.00"), user=self.admin)
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, PaymentStatus.PAID)
