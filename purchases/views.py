from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.access import PURCHASE_ROLES, active_user_required, role_required
from .forms import PurchaseInvoiceForm, PurchaseItemFormSet
from .models import PurchaseInvoice
from .services import cancel_purchase_invoice, receive_purchase_invoice


@active_user_required
def purchase_list(request):
    can_manage = (
        request.user.is_superuser
        or request.user.profile.role in PURCHASE_ROLES
    )
    return render(request, "purchases/list.html", {
        "purchases": PurchaseInvoice.objects.select_related(
            "supplier"
        ).prefetch_related("items"),
        "can_manage": can_manage,
    })


@role_required(*PURCHASE_ROLES)
def purchase_create(request):
    invoice_form = PurchaseInvoiceForm(request.POST or None)
    item_formset = PurchaseItemFormSet(
        request.POST or None,
        instance=PurchaseInvoice(),
        prefix='items',
    )
    if (
        request.method == 'POST'
        and invoice_form.is_valid()
        and item_formset.is_valid()
    ):
        with transaction.atomic():
            invoice = invoice_form.save(commit=False)
            invoice.created_by = request.user
            invoice.save()
            item_formset.instance = invoice
            item_formset.save()
        messages.success(request, 'Purchase invoice created as draft.')
        return redirect('purchases:list')
    return render(request, 'purchases/form.html', {
        'invoice_form': invoice_form,
        'item_formset': item_formset,
        'title': 'New purchase / GRN',
    })


@active_user_required
def purchase_detail(request, invoice_id):
    invoice = get_object_or_404(
        PurchaseInvoice.objects.select_related('supplier', 'created_by')
        .prefetch_related('items__raw_material'),
        pk=invoice_id,
    )
    can_manage = (
        request.user.is_superuser
        or request.user.profile.role in PURCHASE_ROLES
    )
    return render(request, 'purchases/detail.html', {
        'purchase': invoice,
        'can_manage': can_manage,
    })


@role_required(*PURCHASE_ROLES)
def purchase_update(request, invoice_id):
    invoice = get_object_or_404(PurchaseInvoice, pk=invoice_id)
    if invoice.status != 'DRAFT':
        messages.error(request, 'Only draft purchase invoices can be edited.')
        return redirect('purchases:detail', invoice_id=invoice.pk)

    invoice_form = PurchaseInvoiceForm(
        request.POST or None,
        instance=invoice,
    )
    item_formset = PurchaseItemFormSet(
        request.POST or None,
        instance=invoice,
        prefix='items',
    )
    if (
        request.method == 'POST'
        and invoice_form.is_valid()
        and item_formset.is_valid()
    ):
        with transaction.atomic():
            invoice_form.save()
            item_formset.save()
        messages.success(request, 'Draft purchase invoice updated.')
        return redirect('purchases:detail', invoice_id=invoice.pk)
    return render(request, 'purchases/form.html', {
        'invoice_form': invoice_form,
        'item_formset': item_formset,
        'title': f'Edit purchase {invoice.invoice_number}',
        'purchase': invoice,
    })


@role_required(*PURCHASE_ROLES)
def receive(request, invoice_id):
    if request.method == 'POST':
        try:
            receive_purchase_invoice(invoice_id, created_by=request.user)
        except (ValidationError, PermissionError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Goods Receipt Note posted and stock updated.')
    return redirect('purchases:list')


@role_required(*PURCHASE_ROLES)
def cancel(request, invoice_id):
    if request.method == 'POST':
        try:
            cancel_purchase_invoice(
                invoice_id,
                cancelled_by=request.user,
                reason=request.POST.get('reason', ''),
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Purchase invoice cancelled with ledger reversal.')
        return redirect('purchases:detail', invoice_id=invoice_id)
    invoice = get_object_or_404(PurchaseInvoice, pk=invoice_id)
    return render(request, 'purchases/confirm_cancel.html', {'purchase': invoice})
