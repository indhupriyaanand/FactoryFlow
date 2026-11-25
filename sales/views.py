from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.access import MANAGEMENT_ROLES, SALES_ROLES, active_user_required, role_required
from accounts.models import Role
from .billing import create_sales_invoice, record_payment, reverse_payment
from .forms import (
    DispatchForm,
    DispatchQuantityFormSet,
    PaymentForm,
    SalesOrderEditForm,
    SalesOrderForm,
    SalesOrderItemFormSet,
)
from masters.models import Customer
from .models import (
    DispatchItem,
    Payment,
    SalesApprovalStatus,
    SalesInvoice,
    SalesOrder,
    SalesShortage,
    SalesStatus,
)
from .reports import invoice_pdf_response, payment_receipt_pdf_response
from .services import (
    approve_sales_order,
    cancel_sales_order,
    confirm_sales_order,
    create_sales_order,
    dispatch_sales_order,
    reject_sales_order,
    update_sales_order,
)


@active_user_required
def order_list(request):
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    customer_id = request.GET.get('customer', '').strip()
    from_date = request.GET.get('from', '').strip()
    to_date = request.GET.get('to', '').strip()
    orders = SalesOrder.objects.select_related(
        "customer", "created_by", "invoice"
    ).prefetch_related("items")
    if query:
        orders = orders.filter(
            order_number__icontains=query
        ) | orders.filter(customer__name__icontains=query)
    if status:
        orders = orders.filter(status=status)
    if customer_id:
        orders = orders.filter(customer_id=customer_id)
    try:
        if from_date:
            orders = orders.filter(order_date__gte=date.fromisoformat(from_date))
        if to_date:
            orders = orders.filter(order_date__lte=date.fromisoformat(to_date))
    except ValueError:
        messages.error(request, 'Dates must use YYYY-MM-DD format.')
    return render(request, "sales/list.html", {
        "orders": orders,
        "query": query,
        "status": status,
        "customers": Customer.objects.filter(is_active=True),
        "customer_id": customer_id,
        "from_date": from_date,
        "to_date": to_date,
        "can_manage_approvals": request.user.is_superuser
        or request.user.profile.role in MANAGEMENT_ROLES,
        "can_edit_sales": request.user.is_superuser
        or request.user.profile.role in SALES_ROLES,
        "pending_approval_count": SalesOrder.objects.filter(
            status=SalesStatus.DRAFT,
            approval_status=SalesApprovalStatus.PENDING,
        ).count(),
    })


@role_required(*SALES_ROLES)
def create_order(request):
    order_form = SalesOrderForm(
        request.POST if request.method == 'POST' else None
    )
    item_formset = SalesOrderItemFormSet(
        request.POST if request.method == 'POST' else None,
        instance=SalesOrder(),
        prefix='items',
    )
    if request.method == 'POST':
        order_valid = order_form.is_valid()
        items_valid = item_formset.is_valid()
    else:
        order_valid = items_valid = False
    if request.method == 'POST' and order_valid and items_valid:
        try:
            create_sales_order(
                order_form.cleaned_data['customer'],
                [
                    form.cleaned_data
                    for form in item_formset.forms
                    if form.cleaned_data.get('product')
                    and not form.cleaned_data.get('DELETE')
                ],
                user=request.user,
                order_number=order_form.cleaned_data['order_number'],
                notes=order_form.cleaned_data['notes'],
            )
        except ValidationError as exc:
            order_form.add_error(None, exc)
        else:
            return redirect('sales:order_list')
    return render(request, 'sales/form.html', {
        'order_form': order_form,
        'item_formset': item_formset,
    })


@active_user_required
def generate_invoice(request, order_id):
    if request.method != "POST":
        return redirect("sales:order_list")
    try:
        create_sales_invoice(order_id, request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "GST invoice generated.")
    return redirect("sales:order_list")


@role_required(*SALES_ROLES)
def edit_order(request, order_id):
    order = get_object_or_404(
        SalesOrder.objects.select_related('customer').prefetch_related('items'),
        pk=order_id,
    )
    if (
        order.status == SalesStatus.DRAFT
        and order.approval_status == SalesApprovalStatus.APPROVED
    ):
        messages.error(request, 'This sales order can no longer be edited.')
        return redirect('sales:detail', order_id=order.pk)
    order_form = SalesOrderEditForm(
        request.POST if request.method == 'POST' else None,
        instance=order,
    )
    item_formset = SalesOrderItemFormSet(
        request.POST if request.method == 'POST' else None,
        instance=order,
        prefix='items',
    )
    if request.method == 'POST':
        order_valid = order_form.is_valid()
        items_valid = item_formset.is_valid()
    else:
        order_valid = items_valid = False
    if request.method == 'POST' and order_valid and items_valid:
        try:
            update_sales_order(
                order.pk,
                order_form.cleaned_data['customer'],
                [
                    form.cleaned_data
                    for form in item_formset.forms
                    if form.cleaned_data.get('product')
                    or form.cleaned_data.get('id')
                ],
                request.user,
                order_form.cleaned_data['order_date'],
                order_form.cleaned_data['notes'],
            )
        except ValidationError as exc:
            order_form.add_error(None, exc)
        else:
            return redirect('sales:detail', order_id=order.pk)
    return render(request, 'sales/edit.html', {
        'order_form': order_form,
        'item_formset': item_formset,
        'order': order,
    })


@active_user_required
def detail(request, order_id):
    order = get_object_or_404(
        SalesOrder.objects.select_related('customer', 'invoice').prefetch_related(
            'items__product', 'dispatch_records__items__sales_order_item',
            'invoice__payments',
        ),
        pk=order_id,
    )
    can_manage_approvals = request.user.is_superuser or (
        request.user.profile.role in MANAGEMENT_ROLES
    )
    can_create_sales = request.user.is_superuser or (
        request.user.profile.role in SALES_ROLES
    )
    return render(request, 'sales/detail.html', {
        'order': order,
        'can_manage_approvals': can_manage_approvals,
        'can_edit_order': (
            can_create_sales
            and order.status == SalesStatus.DRAFT
            and order.approval_status != SalesApprovalStatus.APPROVED
        ),
    })


@role_required(*MANAGEMENT_ROLES)
def approval_queue(request):
    orders = SalesOrder.objects.filter(
        status=SalesStatus.DRAFT,
        approval_status=SalesApprovalStatus.PENDING,
    ).select_related('customer', 'created_by').prefetch_related('items__product')
    return render(request, 'sales/approval_queue.html', {'orders': orders})


@role_required(*SALES_ROLES)
def dispatch(request, order_id):
    order = get_object_or_404(
        SalesOrder.objects.prefetch_related('items__product'),
        pk=order_id,
    )
    if (
        order.status not in (
            SalesStatus.CONFIRMED,
            SalesStatus.PARTIALLY_DISPATCHED,
        )
        or order.approval_status != SalesApprovalStatus.APPROVED
    ):
        messages.error(
            request,
            'Only confirmed, approved sales orders can be dispatched.',
        )
        return redirect('sales:detail', order_id=order.pk)
    dispatch_form = DispatchForm(request.POST or None)
    initial = [
        {'item_id': item.pk, 'quantity': item.quantity - item.dispatched_quantity}
        for item in order.items.all()
    ]
    quantity_forms = DispatchQuantityFormSet(
        request.POST or None,
        initial=initial,
        prefix='dispatch',
    )
    if request.method == 'POST' and dispatch_form.is_valid() and quantity_forms.is_valid():
        quantities = {
            form.cleaned_data['item_id']: form.cleaned_data['quantity']
            for form in quantity_forms
        }
        try:
            dispatch_sales_order(
                order.pk,
                request.user,
                quantities,
                dispatch_form.cleaned_data['dispatch_date'],
                dispatch_form.cleaned_data['vehicle_reference'],
                dispatch_form.cleaned_data['notes'],
            )
        except ValidationError as exc:
            dispatch_form.add_error(None, exc)
        else:
            messages.success(request, 'Dispatch recorded.')
            return redirect('sales:detail', order_id=order.pk)
    return render(request, 'sales/dispatch_form.html', {
        'order': order,
        'dispatch_form': dispatch_form,
        'quantity_forms': quantity_forms,
        'dispatch_lines': [
            (item, form, item.quantity - item.dispatched_quantity)
            for item, form in zip(order.items.all(), quantity_forms)
        ],
    })


@role_required(Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)
def invoice_pdf(request, invoice_id):
    invoice = get_object_or_404(
        SalesInvoice.objects.select_related("sales_order__customer"), pk=invoice_id
    )
    return invoice_pdf_response(invoice)


@role_required(Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)
def add_payment(request, invoice_id):
    invoice = get_object_or_404(SalesInvoice, pk=invoice_id)
    form = PaymentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            record_payment(
                invoice_id,
                form.cleaned_data['amount'],
                form.cleaned_data['method'],
                form.cleaned_data['reference'],
                request.user,
                form.cleaned_data['payment_date'],
                form.cleaned_data['notes'],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('sales:detail', order_id=invoice.sales_order_id)
    return render(request, 'sales/payment_form.html', {'form': form, 'invoice': invoice})


@role_required(Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)
def collection_history(request):
    return render(request, 'sales/collections.html', {
        'payments': Payment.objects.select_related(
            'invoice__sales_order__customer'
        ).order_by('-payment_date'),
    })


@role_required(Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)
def payment_receipt(request, payment_id):
    payment = get_object_or_404(
        Payment.objects.select_related('invoice__sales_order__customer'), pk=payment_id
    )
    return payment_receipt_pdf_response(payment)


@role_required(Role.ADMIN, Role.ACCOUNTANT, Role.OWNER)
def reverse_payment_view(request, payment_id):
    if request.method == 'POST':
        try:
            reverse_payment(payment_id, request.user, request.POST.get('reason', ''))
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Payment reversed.')
    return redirect('sales:collection_history')


@role_required(*MANAGEMENT_ROLES)
@require_POST
def confirm_order(request, order_id):
    try:
        confirm_sales_order(order_id, request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Sales order confirmed and ready for dispatch.')
    return redirect('sales:detail', order_id=order_id)


@active_user_required
def shortage_list(request):
    return render(request, 'sales/shortages.html', {
        'shortages': SalesShortage.objects.select_related(
            'sales_order__customer', 'product'
        ).order_by('-created_at'),
    })


@role_required(*SALES_ROLES)
def cancel_order(request, order_id):
    if request.method == 'POST':
        try:
            cancel_sales_order(
                order_id,
                user=request.user,
                reason=request.POST.get('reason', ''),
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Sales order cancelled.')
    return redirect('sales:order_list')


@role_required(*MANAGEMENT_ROLES)
@require_POST
def approve_order(request, order_id):
    try:
        approve_sales_order(order_id, request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            'Sales order approved. Confirm it to make it eligible for dispatch.',
        )
    return redirect('sales:detail', order_id=order_id)


@role_required(*MANAGEMENT_ROLES)
@require_POST
def reject_order(request, order_id):
    try:
        reject_sales_order(
            order_id,
            request.user,
            request.POST.get('reason', ''),
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Sales order rejected.')
    return redirect('sales:detail', order_id=order_id)
