from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from accounts.access import active_user_required, role_required, PRODUCTION_ROLES
from .forms import (
    DamageEditForm,
    DamagedPouchForm,
    ProductionCompletionForm,
    ProductionMaterialUsageFormSet,
    ProductionOrderForm,
    ScrapForm,
)
from .models import ProductionDamageRecord, ProductionDamageType, ProductionOrder
from .services import (
    approve_production_order,
    complete_production_order,
    create_production_order,
    record_damage,
    cancel_production_order,
    void_damage_record,
)


@active_user_required
def production_list(request):
    can_manage = (
        request.user.is_superuser
        or request.user.profile.role in PRODUCTION_ROLES
    )
    return render(request, "production/list.html", {
        "orders": ProductionOrder.objects.select_related("product", "created_by"),
        "can_manage": can_manage,
    })


@role_required(*PRODUCTION_ROLES)
def create(request):
    form = ProductionOrderForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            create_production_order(
                product=form.cleaned_data['product'],
                target_quantity=form.cleaned_data['target_quantity'],
                user=request.user,
                start_date=form.cleaned_data['start_date'],
                notes=form.cleaned_data['notes'],
                order_number=form.cleaned_data['order_number'],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('production:list')
    return render(request, 'production/form.html', {'form': form, 'title': 'Create production order'})


@role_required(*PRODUCTION_ROLES)
def edit(request, order_id):
    order = get_object_or_404(ProductionOrder, pk=order_id)
    if order.status in ('COMPLETED', 'CANCELLED'):
        messages.error(request, 'Finalized production orders cannot be edited.')
        return redirect('production:detail', order_id=order.pk)
    form = ProductionOrderForm(request.POST or None, instance=order)
    if request.method == 'POST' and form.is_valid():
        try:
            form.save()
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('production:list')
    return render(request, 'production/form.html', {'form': form, 'title': 'Edit production order'})


@active_user_required
def detail(request, order_id):
    order = get_object_or_404(
        ProductionOrder.objects.select_related('product', 'created_by', 'approved_by')
        .prefetch_related(
            'material_usages__raw_material',
            'damage_records__raw_material',
            'damage_records__product',
        ),
        pk=order_id,
    )
    can_manage = (
        request.user.is_superuser
        or request.user.profile.role in PRODUCTION_ROLES
    )
    return render(request, 'production/detail.html', {
        'order': order,
        'can_manage': can_manage,
    })


@role_required(*PRODUCTION_ROLES)
def complete(request, order_id):
    order = get_object_or_404(
        ProductionOrder.objects.prefetch_related('material_usages__raw_material'),
        pk=order_id,
    )
    form = ProductionCompletionForm(request.POST or None, initial={'produced_quantity': order.target_quantity})
    usage_formset = ProductionMaterialUsageFormSet(
        request.POST or None,
        queryset=order.material_usages.select_related('raw_material').order_by('pk'),
    )
    if request.method == 'POST' and form.is_valid() and usage_formset.is_valid():
        actuals = {
            form.instance.raw_material_id: form.cleaned_data['actual_quantity']
            for form in usage_formset
        }
        try:
            complete_production_order(
                order_id,
                produced_quantity=form.cleaned_data['produced_quantity'],
                user=request.user,
                actual_material_quantities=actuals,
                damaged_pouch_quantity=form.cleaned_data['damaged_pouch_quantity'],
                labour_cost=form.cleaned_data['labour_cost'],
                power_cost=form.cleaned_data['power_cost'],
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Production batch completed.')
            return redirect('production:list')
    return render(request, 'production/form.html', {
        'form': form,
        'usage_formset': usage_formset,
        'title': 'Complete production order',
        'order': order,
    })


@role_required(*PRODUCTION_ROLES)
def approve(request, order_id):
    if request.method == "POST":
        try:
            approve_production_order(order_id, request.user)
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Production order approved.")
    return redirect("production:list")


@role_required(*PRODUCTION_ROLES)
def cancel(request, order_id):
    order = get_object_or_404(ProductionOrder, pk=order_id)
    if request.method == 'POST':
        try:
            cancel_production_order(
                order.pk,
                request.user,
                request.POST.get('reason', ''),
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect('production:detail', order_id=order.pk)
        messages.success(request, 'Production order cancelled with stock reversals where required.')
        return redirect('production:list')
    return render(request, 'production/confirm_cancel.html', {'order': order})


@role_required(*PRODUCTION_ROLES)
def damage_list(request):
    return render(request, 'production/damage_list.html', {
        'records': ProductionDamageRecord.objects.select_related(
            'production_order', 'raw_material', 'product', 'recorded_by'
        ).order_by('-recorded_at', '-id'),
    })


@role_required(*PRODUCTION_ROLES)
def damage_create(request, damage_type):
    if damage_type not in ProductionDamageType.values:
        raise Http404
    form_class = DamagedPouchForm if damage_type == ProductionDamageType.DAMAGED_POUCH else ScrapForm
    form = form_class(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            record_damage(
                data['production_order'].pk,
                damage_type,
                data['quantity'],
                request.user,
                raw_material=data.get('raw_material'),
                product=data.get('product'),
                unit=data.get('unit'),
                reason=data.get('reason', ''),
                recorded_at=data.get('recorded_at'),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Damage or scrap record created.')
            return redirect('production:damage_list')
    return render(request, 'production/damage_form.html', {
        'form': form,
        'title': 'Record damaged pouches' if damage_type == ProductionDamageType.DAMAGED_POUCH else 'Record scrap',
    })


@role_required(*PRODUCTION_ROLES)
def damage_edit(request, record_id):
    record = get_object_or_404(ProductionDamageRecord, pk=record_id)
    if record.is_voided:
        messages.error(request, 'Voided damage records cannot be edited.')
        return redirect('production:damage_list')
    form = DamageEditForm(request.POST or None, instance=record)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Damage record updated.')
        return redirect('production:damage_list')
    return render(request, 'production/damage_form.html', {
        'form': form,
        'title': 'Edit damage or scrap record',
        'record': record,
    })


@role_required(*PRODUCTION_ROLES)
def damage_void(request, record_id):
    if request.method == 'POST':
        try:
            void_damage_record(
                record_id,
                request.user,
                request.POST.get('reason', ''),
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Damage record voided with a stock reversal.')
    return redirect('production:damage_list')
