import csv
import io
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse
from django.db import connection
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db.models import Sum
from accounts.models import Role
from inventory.models import InventorySourceAllocation, StockMovementType
from inventory.services import create_stock_ledger_entry, get_raw_material_stock_balance
from masters.models import Customer, Product, RawMaterial, Supplier
from production.models import ProductionOrder, ProductionStatus
from production.services import (
    approve_production_order,
    complete_production_order,
    create_production_order,
)
from purchases.models import PurchaseInvoice, PurchaseItem
from purchases.services import receive_purchase_invoice
from sales.billing import create_sales_invoice, record_payment, reverse_payment
from sales.models import DispatchRecord, Payment, SalesInvoice, SalesOrder
from sales.services import (
    approve_sales_order,
    confirm_sales_order,
    create_sales_order,
    dispatch_sales_order,
)

class Phase1SetupTests(TestCase):
    """
    Automated verification tests for Phase 1:
    - Environment sanity
    - Database connection
    - Home view routing and template rendering
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="owner", password="Password123!")
        self.user.profile.role = Role.OWNER
        self.user.profile.save()
        self.client.force_login(self.user)

    def test_database_connection(self):
        """Verify that SQLite/PostgreSQL connection is healthy."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            row = cursor.fetchone()
            self.assertEqual(row[0], 1)

    def test_home_page_status_code(self):
        """Verify that home page responds with HTTP 200 OK."""
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)

    def test_home_page_content(self):
        """Verify that home page contains FactoryFlow branding and Phase 1 checklist."""
        response = self.client.get(reverse('home'))
        self.assertContains(response, "FactoryFlow")
        self.assertContains(response, "Smart Order-to-Audit Management System")
        self.assertContains(response, "NIT Tiruchirappalli")
        self.assertContains(response, reverse('product_profitability_report'))
        self.assertTemplateUsed(response, 'core/home.html')
        self.assertTemplateUsed(response, 'base.html')

    def test_dashboard_phase_statuses_follow_costing_implementation(self):
        response = self.client.get(reverse('home'))
        phases = {phase['number']: phase for phase in response.context['phases']}
        self.assertEqual(len(phases), 13)
        for phase in phases.values():
            expected_status = 'Partial' if phase['missing'] else 'Complete'
            self.assertEqual(phase['status'], expected_status)

        self.assertEqual(phases[11]['status'], 'Complete')
        self.assertEqual(phases[11]['missing'], [])
        self.assertEqual(phases[12]['status'], 'Complete')

    def test_dashboard_requires_owner_or_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('home')).status_code, 302)
        salesman = User.objects.create_user(username='salesman')
        salesman.profile.role = Role.SALESMAN
        salesman.profile.save()
        self.client.force_login(salesman)
        self.assertEqual(self.client.get(reverse('home')).status_code, 403)

    def test_health_endpoint_is_available(self):
        response = self.client.get(reverse('health'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    def test_authorized_report_exports(self):
        for route_name in (
            'inventory_valuation_csv',
            'production_csv',
            'production_excel',
            'production_pdf',
            'production_variance_csv',
            'production_cost_variance_csv',
            'production_material_variance_csv',
            'production_damage_csv',
            'production_approval_history_csv',
            'product_profitability_csv',
            'product_profitability_report',
            'sales_orders_csv',
            'sales_approval_audit_csv',
            'dispatch_register_csv',
            'sales_invoice_register_csv',
            'payment_register_csv',
            'outstanding_invoices_csv',
            'inventory_ledger_audit_csv',
        ):
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200, route_name)
        self.assertIn(b'Order,Product,Material', self.client.get(
            reverse('production_material_variance_csv')
        ).content)
        self.assertIn(b'Date,Order,Type,Material,Product', self.client.get(
            reverse('production_damage_csv')
        ).content)

    def test_dashboard_and_audit_report_permissions_follow_role_definitions(self):
        report_url = reverse('sales_orders_csv')
        profitability_url = reverse('product_profitability_report')
        for username, role, dashboard_status, report_status in (
            ('role-admin', Role.ADMIN, 200, 200),
            ('role-owner', Role.OWNER, 200, 200),
            ('role-accountant', Role.ACCOUNTANT, 403, 200),
            ('role-sales', Role.SALESMAN, 403, 403),
            ('role-production', Role.PRODUCTION_SUPERVISOR, 403, 403),
        ):
            user = User.objects.create_user(username=username)
            user.profile.role = role
            user.profile.save()
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('home')).status_code, dashboard_status)
            self.assertEqual(self.client.get(report_url).status_code, report_status)
            self.assertEqual(
                self.client.get(profitability_url).status_code,
                report_status,
            )

        accountant = User.objects.get(username='role-accountant')
        self.client.force_login(accountant)
        self.assertEqual(
            self.client.get(reverse('sales_orders_csv'), {'from': 'bad-date'}).status_code,
            400,
        )

    def test_inactive_report_users_are_denied(self):
        user = User.objects.create_user(username='inactive-report-user')
        user.profile.role = Role.ACCOUNTANT
        user.profile.is_active = False
        user.profile.save()
        self.client.force_login(user)
        for route_name in (
            'production_csv',
            'inventory_valuation_csv',
            'product_profitability_csv',
            'product_profitability_report',
            'production_damage_csv',
        ):
            with self.subTest(route=route_name):
                self.assertEqual(self.client.get(reverse(route_name)).status_code, 403)

    def test_inventory_valuation_supports_material_product_and_date_filters(self):
        material = RawMaterial.objects.create(name='Filtered Flour', code='FILTER-RM-1')
        other_material = RawMaterial.objects.create(name='Other Flour', code='FILTER-RM-2')
        product = Product.objects.create(name='Filtered Product', code='FILTER-PRD-1')
        other_product = Product.objects.create(name='Other Product', code='FILTER-PRD-2')
        url = reverse('inventory_valuation_csv')

        material_response = self.client.get(url, {'material': material.pk})
        material_csv = material_response.content.decode()
        self.assertIn(material.name, material_csv)
        self.assertNotIn(other_material.name, material_csv)
        self.assertIn(other_product.name, material_csv)

        product_response = self.client.get(url, {'product': product.pk})
        product_csv = product_response.content.decode()
        self.assertIn(product.name, product_csv)
        self.assertNotIn(other_product.name, product_csv)
        self.assertIn(other_material.name, product_csv)

        self.assertEqual(
            self.client.get(url, {'from': '2025-02-02', 'to': '2025-02-01'}).status_code,
            400,
        )
        self.assertEqual(self.client.get(url, {'material': 'invalid'}).status_code, 400)


class BenchmarkEndToEndTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='benchmark-admin')
        self.admin.profile.role = Role.ADMIN
        self.admin.profile.save()
        self.client = Client()
        self.client.force_login(self.admin)

    def _receive_purchase(self, supplier, invoice_number, lines):
        invoice = PurchaseInvoice.objects.create(
            invoice_number=invoice_number,
            supplier=supplier,
        )
        for material, quantity, unit_price in lines:
            PurchaseItem.objects.create(
                purchase=invoice,
                raw_material=material,
                quantity=quantity,
                unit=material.unit,
                unit_price=unit_price,
            )
        return receive_purchase_invoice(invoice.pk, self.admin)

    def test_seeded_benchmark_runs_through_purchase_production_sales_and_reports(self):
        output = io.StringIO()
        call_command('seed_benchmark_data', stdout=output)
        call_command('verify_benchmark', stdout=output)
        master_counts = (
            Product.objects.count(),
            RawMaterial.objects.count(),
            PurchaseInvoice.objects.count(),
            ProductionOrder.objects.count(),
            SalesOrder.objects.count(),
        )
        call_command('seed_benchmark_data', stdout=output)
        call_command('verify_benchmark', stdout=output)
        self.assertEqual(
            master_counts,
            (
                Product.objects.count(),
                RawMaterial.objects.count(),
                PurchaseInvoice.objects.count(),
                ProductionOrder.objects.count(),
                SalesOrder.objects.count(),
            ),
        )

        product = Product.objects.get(code='PRD-MUR-200G')
        supplier = Supplier.objects.get(name='Sample Raw Material Supplier')
        materials = {
            material.code: material
            for material in RawMaterial.objects.filter(
                code__in=('RM-FLOUR', 'RM-OIL', 'RM-SPICE', 'RM-POUCH', 'RM-CARTON')
            )
        }
        self._receive_purchase(supplier, 'BENCH-PUR-1', [
            (materials['RM-FLOUR'], Decimal('80.0000'), Decimal('42.00')),
            (materials['RM-OIL'], Decimal('6.0000'), Decimal('100.00')),
            (materials['RM-SPICE'], Decimal('2.0000'), Decimal('200.00')),
            (materials['RM-POUCH'], Decimal('250.0000'), Decimal('2.50')),
            (materials['RM-CARTON'], Decimal('20.0000'), Decimal('15.00')),
        ])
        self._receive_purchase(supplier, 'BENCH-PUR-2', [
            (materials['RM-OIL'], Decimal('10.0000'), Decimal('200.00')),
        ])
        self.assertEqual(
            get_raw_material_stock_balance(materials['RM-FLOUR']),
            Decimal('80.0000'),
        )
        with self.assertRaises(ValidationError):
            receive_purchase_invoice(
                PurchaseInvoice.objects.get(invoice_number='BENCH-PUR-1').pk,
                self.admin,
            )
        self.assertEqual(PurchaseInvoice.objects.count(), 2)

        production_order = create_production_order(
            product,
            Decimal('200.0000'),
            user=self.admin,
            order_number='BENCH-PRD-1',
        )
        approve_production_order(production_order.pk, self.admin)
        actuals = {
            materials['RM-FLOUR'].pk: Decimal('40.0000'),
            materials['RM-OIL'].pk: Decimal('10.5000'),
            materials['RM-SPICE'].pk: Decimal('1.2000'),
            materials['RM-POUCH'].pk: Decimal('200.0000'),
            materials['RM-CARTON'].pk: Decimal('10.0000'),
        }
        completed = complete_production_order(
            production_order.pk,
            produced_quantity=Decimal('188.0000'),
            user=self.admin,
            actual_material_quantities=actuals,
            damaged_pouch_quantity=Decimal('2.0000'),
            labour_cost=Decimal('100.00'),
            power_cost=Decimal('50.00'),
        )
        completed.refresh_from_db()
        self.assertEqual(completed.status, ProductionStatus.COMPLETED)
        self.assertEqual(completed.material_cost, Decimal('4075.00'))
        self.assertEqual(completed.total_cost, Decimal('4225.00'))
        later_lot_quantity = InventorySourceAllocation.objects.filter(
            stock_ledger_entry__reference_number='BENCH-PRD-1',
            source__purchase_item__purchase__invoice_number='BENCH-PUR-2',
        ).aggregate(total=Sum('quantity'))['total']
        self.assertEqual(later_lot_quantity, Decimal('4.5000'))
        self.assertEqual(
            completed.damage_records.get().stock_ledger_entry.quantity,
            Decimal('2.0000'),
        )
        pouch_usage = completed.material_usages.get(
            raw_material=materials['RM-POUCH']
        )
        self.assertEqual(pouch_usage.planned_unit_rate, Decimal('2.5000'))
        self.assertEqual(
            pouch_usage.actual_consumed_quantity,
            Decimal('202.0000'),
        )
        self.assertEqual(
            pouch_usage.variance_quantity,
            Decimal('2.0000'),
        )
        self.assertEqual(ProductionOrder.objects.count(), 1)

        second_product = Product.objects.create(
            name='Benchmark Side Product',
            code='BENCH-SIDE',
            unit_price=Decimal('20.00'),
            gst_rate=Decimal('5.00'),
        )
        create_stock_ledger_entry(
            movement_type=StockMovementType.IN,
            product=second_product,
            quantity=Decimal('4.0000'),
            reference_number='BENCH-OPENING-SIDE',
            created_by=self.admin,
        )
        retailer = Customer.objects.get(name='Retailer A')
        distributor = Customer.objects.get(name='Distributor B')
        retailer_order = create_sales_order(
            retailer,
            [{
                'product': product,
                'quantity': Decimal('50.0000'),
                'unit_price': product.unit_price,
                'gst_rate': product.gst_rate,
            }],
            user=self.admin,
            order_number='BENCH-SO-RETAIL',
        )
        distributor_order = create_sales_order(
            distributor,
            [
                {
                    'product': product,
                    'quantity': Decimal('100.0000'),
                    'unit_price': product.unit_price,
                    'gst_rate': product.gst_rate,
                },
                {
                    'product': second_product,
                    'quantity': Decimal('4.0000'),
                    'unit_price': second_product.unit_price,
                    'gst_rate': second_product.gst_rate,
                },
            ],
            user=self.admin,
            order_number='BENCH-SO-DISTRIBUTOR',
        )
        for order in (retailer_order, distributor_order):
            approve_sales_order(order.pk, self.admin)
            confirm_sales_order(order.pk, self.admin)
            dispatch_sales_order(order.pk, self.admin)
        self.assertEqual(DispatchRecord.objects.count(), 2)
        with self.assertRaises(ValidationError):
            dispatch_sales_order(retailer_order.pk, self.admin)
        self.assertEqual(DispatchRecord.objects.count(), 2)

        retailer_invoice = create_sales_invoice(retailer_order.pk, self.admin)
        distributor_invoice = create_sales_invoice(distributor_order.pk, self.admin)
        self.assertEqual(
            create_sales_invoice(retailer_order.pk, self.admin).pk,
            retailer_invoice.pk,
        )
        self.assertEqual(SalesInvoice.objects.count(), 2)
        retailer_payment = record_payment(
            retailer_invoice.pk,
            retailer_invoice.outstanding_amount,
            user=self.admin,
            reference='BENCH-RETAIL-PAY',
        )
        distributor_payment = record_payment(
            distributor_invoice.pk,
            Decimal('500.00'),
            user=self.admin,
            reference='BENCH-DIST-PAY',
        )
        with self.assertRaises(ValidationError):
            record_payment(
                distributor_invoice.pk,
                Decimal('500.00'),
                user=self.admin,
                reference='BENCH-DIST-PAY',
            )
        reverse_payment(distributor_payment.pk, self.admin, 'Benchmark correction')
        self.assertEqual(Payment.objects.count(), 2)
        distributor_invoice.refresh_from_db()
        self.assertEqual(distributor_invoice.amount_paid, Decimal('0.00'))
        self.assertEqual(retailer_payment.amount, Decimal('2625.00'))

        sales_report = self.client.get(reverse('sales_orders_csv'))
        self.assertEqual(sales_report.status_code, 200)
        self.assertContains(sales_report, 'BENCH-SO-DISTRIBUTOR')
        profitability = self.client.get(reverse('product_profitability_csv'))
        product_rows = {
            row['Product']: row
            for row in csv.DictReader(io.StringIO(profitability.content.decode()))
        }
        self.assertEqual(
            float(product_rows['Murukku 200g']['Line-item Revenue (ex GST)']),
            7500.0,
        )
        self.assertEqual(product_rows['Murukku 200g']['Product Code'], 'PRD-MUR-200G')
        self.assertEqual(
            Decimal(product_rows['Murukku 200g']['Dispatched Quantity']),
            Decimal('150.0000'),
        )
        self.assertEqual(
            float(product_rows['Murukku 200g']['Dispatch COGS']),
            3371.01,
        )
        self.assertEqual(
            float(product_rows['Murukku 200g']['Gross Contribution']),
            4128.99,
        )
        self.assertEqual(
            Decimal(product_rows['Murukku 200g']['Gross Margin %']),
            Decimal('55.05'),
        )
        self.assertEqual(
            product_rows['Murukku 200g']['Cost Source Status'],
            'FIFO production sources',
        )
        self.assertEqual(
            float(product_rows['Benchmark Side Product']['Line-item Revenue (ex GST)']),
            80.0,
        )
        self.assertEqual(
            product_rows['Benchmark Side Product']['Product Code'],
            'BENCH-SIDE',
        )
        self.assertEqual(
            product_rows['Benchmark Side Product']['Dispatch COGS'],
            '',
        )
        self.assertEqual(
            product_rows['Benchmark Side Product']['Cost Source Status'],
            'Legacy source cost unavailable',
        )
        profitability_page = self.client.get(
            reverse('product_profitability_report')
        )
        self.assertEqual(profitability_page.status_code, 200)
        self.assertContains(profitability_page, 'PRD-MUR-200G')
        self.assertContains(profitability_page, 'Gross profit')
        self.assertContains(profitability_page, '55.05%')
        self.assertContains(profitability_page, 'Unavailable')
        side_row = next(
            row for row in profitability_page.context['rows']
            if row['product_name'] == 'Benchmark Side Product'
        )
        self.assertIsNone(side_row['fifo_cogs'])
        self.assertIsNone(side_row['gross_profit'])
        self.assertIsNone(side_row['gross_margin'])
        self.assertContains(
            profitability_page,
            reverse('product_profitability_csv'),
        )
        filtered_profitability = self.client.get(
            reverse('product_profitability_report'),
            {'product': product.pk},
        )
        self.assertContains(filtered_profitability, 'Murukku 200g')
        self.assertEqual(
            [row['product_name'] for row in filtered_profitability.context['rows']],
            ['Murukku 200g'],
        )
        self.assertContains(
            self.client.get(reverse('production_material_variance_csv')),
            'FIFO receipt sources',
        )
        self.assertContains(
            self.client.get(reverse('inventory_ledger_audit_csv')),
            'BENCH-PRD-1',
        )
        self.assertContains(
            self.client.get(reverse('payment_register_csv')),
            'Benchmark correction',
        )
        self.assertContains(
            self.client.get(reverse('sales_approval_audit_csv')),
            'BENCH-SO-RETAIL',
        )
        self.assertContains(
            self.client.get(reverse('dispatch_register_csv')),
            'BENCH-SO-DISTRIBUTOR',
        )
        self.assertContains(
            self.client.get(reverse('outstanding_invoices_csv')),
            'BENCH-SO-DISTRIBUTOR',
        )
        dashboard = self.client.get(reverse('home'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertFalse(dashboard.context['dispatch_cost_complete'])
        dashboard_profitability = {
            row['name']: row for row in dashboard.context['profitability_rows']
        }
        self.assertEqual(
            dashboard_profitability['Murukku 200g']['profit'],
            4128.99,
        )
        self.assertIsNone(
            dashboard_profitability['Benchmark Side Product']['profit']
        )
