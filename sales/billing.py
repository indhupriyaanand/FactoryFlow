from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from sales.models import (
    Payment,
    PaymentStatus,
    PaymentReversal,
    SalesInvoice,
    SalesInvoiceLine,
    SalesOrder,
    SalesStatus,
)
from accounts.access import require_role
from accounts.models import Role


def calculate_gst_breakdown(order: SalesOrder, company_state: str = 'Tamil Nadu') -> dict:
    subtotal = order.total_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    tax = order.tax_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if order.customer.state.strip().casefold() == company_state.strip().casefold():
        cgst = (tax / Decimal('2')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        sgst = tax - cgst
        igst = Decimal('0.00')
    else:
        cgst = Decimal('0.00')
        sgst = Decimal('0.00')
        igst = tax
    return {
        'subtotal': subtotal,
        'cgst_amount': cgst,
        'sgst_amount': sgst,
        'igst_amount': igst,
        'tax_amount': tax,
        'grand_total': (subtotal + tax).quantize(Decimal('0.01')),
    }


def create_sales_invoice(
    order_id: int,
    user: Optional[User] = None,
    company_state: str = 'Tamil Nadu',
) -> SalesInvoice:
    require_role(user, Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)
    with transaction.atomic():
        try:
            order = SalesOrder.objects.select_for_update().select_related('customer').get(pk=order_id)
        except (SalesOrder.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError(f"Sales order with ID '{order_id}' does not exist.") from exc
        if order.status != SalesStatus.DISPATCHED:
            raise ValidationError("Invoices can only be generated for dispatched orders.")
        if order.items.filter(dispatched_quantity__lt=F('quantity')).exists():
            raise ValidationError("Invoices require all order quantities to be dispatched.")
        existing = SalesInvoice.objects.filter(sales_order=order).first()
        if existing:
            return existing
        breakdown = calculate_gst_breakdown(order, company_state)
        invoice = SalesInvoice(
            sales_order=order,
            invoice_number=_next_invoice_number(),
            place_of_supply=order.customer.state,
            **breakdown,
        )
        invoice.full_clean()
        invoice.save()
        for item in order.items.select_related('product'):
            SalesInvoiceLine.objects.create(
                invoice=invoice,
                product_name=item.product.name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount_amount=item.discount_amount,
                gst_rate=item.gst_rate,
                subtotal=item.subtotal,
                tax_amount=item.tax_amount,
                total_price=item.total_price,
            )
        return invoice


def record_payment(
    invoice_id: int,
    amount: Decimal,
    method: str = 'Cash',
    reference: str = '',
    user: Optional[User] = None,
    payment_date=None,
    notes: str = '',
) -> Payment:
    require_role(user, Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)
    if not isinstance(amount, Decimal):
        raise ValidationError('Payment amount must be a Decimal.')
    with transaction.atomic():
        try:
            invoice = SalesInvoice.objects.select_for_update().get(pk=invoice_id)
        except (SalesInvoice.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError(f"Sales invoice with ID '{invoice_id}' does not exist.") from exc
        if amount <= Decimal('0.00'):
            raise ValidationError('Payment amount must be greater than zero.')
        if amount > invoice.outstanding_amount:
            raise ValidationError('Payment cannot exceed the outstanding invoice balance.')
        if reference.strip() and Payment.objects.filter(
            invoice=invoice,
            reference=reference.strip(),
            reversal__isnull=True,
        ).exists():
            raise ValidationError('A payment with this reference already exists for the invoice.')
        payment = Payment(
            invoice=invoice,
            amount=amount,
            method=method.strip(),
            payment_date=payment_date or timezone.now().date(),
            reference=reference.strip(),
            notes=notes.strip(),
            created_by=user,
        )
        payment.full_clean()
        payment.save()
        new_paid = invoice.amount_paid + amount
        status = PaymentStatus.PAID if new_paid == invoice.grand_total else PaymentStatus.PARTIAL
        SalesInvoice.objects.filter(pk=invoice.pk).update(
            amount_paid=new_paid,
            payment_status=status,
        )
        return payment


def reverse_payment(
    payment_id: int,
    user: Optional[User] = None,
    reason: str = '',
) -> PaymentReversal:
    require_role(user, Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)
    if not reason.strip():
        raise ValidationError('A payment reversal reason is required.')
    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().select_related('invoice').get(pk=payment_id)
        except (Payment.DoesNotExist, ValueError, TypeError) as exc:
            raise ValidationError(f'Payment with ID {payment_id} does not exist.') from exc
        if hasattr(payment, 'reversal'):
            raise ValidationError('This payment has already been reversed.')
        invoice = SalesInvoice.objects.select_for_update().get(pk=payment.invoice_id)
        reversal = PaymentReversal.objects.create(
            payment=payment,
            amount=payment.amount,
            reason=reason.strip(),
            reversed_by=user,
        )
        new_paid = invoice.amount_paid - payment.amount
        if new_paid < Decimal('0.00'):
            raise ValidationError('Payment reversal would make the invoice balance negative.')
        status = (
            PaymentStatus.PAID if new_paid == invoice.grand_total
            else PaymentStatus.PARTIAL if new_paid > Decimal('0.00')
            else PaymentStatus.UNPAID
        )
        SalesInvoice.objects.filter(pk=invoice.pk).update(
            amount_paid=new_paid,
            payment_status=status,
        )
        return reversal


def _next_invoice_number() -> str:
    prefix = f'INV-{timezone.now().year}-'
    last = SalesInvoice.objects.filter(invoice_number__startswith=prefix).order_by('-invoice_number').first()
    number = int(last.invoice_number.rsplit('-', 1)[-1]) + 1 if last else 1
    return f'{prefix}{number:04d}'
