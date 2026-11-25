from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from accounts.access import active_user_required
from accounts.access import MANAGEMENT_ROLES, role_required
from .forms import (
    BillOfMaterialsForm,
    CustomerForm,
    ProductForm,
    RawMaterialForm,
    SupplierForm,
)
from .models import BillOfMaterials, Customer, Product, RawMaterial, Supplier


MASTER_CONFIG = {
    'products': (Product, ProductForm, 'name', 'code'),
    'raw-materials': (RawMaterial, RawMaterialForm, 'name', 'code'),
    'customers': (Customer, CustomerForm, 'name', 'contact_number'),
    'suppliers': (Supplier, SupplierForm, 'name', 'contact_number'),
    'bom': (BillOfMaterials, BillOfMaterialsForm, 'product__name', 'raw_material__name'),
}


@active_user_required
def master_summary(request):
    return render(request, "masters/summary.html", {
        "products": Product.objects.filter(is_active=True),
        "raw_materials": RawMaterial.objects.filter(is_active=True),
        "customers": Customer.objects.filter(is_active=True),
        "suppliers": Supplier.objects.filter(is_active=True),
        "bom_count": BillOfMaterials.objects.filter(is_active=True).count(),
    })


def _master_config(resource):
    return MASTER_CONFIG.get(resource)


@active_user_required
def master_list(request, resource):
    config = _master_config(resource)
    if not config:
        raise Http404
    model, form_class, first_search, second_search = config
    query = request.GET.get('q', '').strip()
    queryset = model.objects.all()
    if hasattr(model, 'is_active'):
        queryset = queryset.filter(is_active=True)
    if query:
        queryset = queryset.filter(Q(**{f'{first_search}__icontains': query}) | Q(**{f'{second_search}__icontains': query}))
    page = Paginator(queryset, 20).get_page(request.GET.get('page'))
    can_manage = (
        request.user.is_superuser
        or request.user.profile.role in MANAGEMENT_ROLES
    )
    return render(request, 'masters/crud_list.html', {
        'resource': resource,
        'object_list': page,
        'query': query,
        'model_name': model._meta.verbose_name_plural,
        'can_manage': can_manage,
    })


@role_required(*MANAGEMENT_ROLES)
def master_create(request, resource):
    config = _master_config(resource)
    if not config:
        raise Http404
    model, form_class, _, _ = config
    form = form_class(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('masters:list', resource=resource)
    return render(request, 'masters/crud_form.html', {'form': form, 'resource': resource, 'model_name': model._meta.verbose_name})


@active_user_required
def master_detail(request, resource, pk):
    config = _master_config(resource)
    if not config:
        raise Http404
    model = config[0]
    instance = get_object_or_404(model, pk=pk)
    fields = [
        (field.verbose_name, getattr(instance, field.name))
        for field in model._meta.fields
        if not field.primary_key and not field.auto_created
    ]
    can_manage = (
        request.user.is_superuser
        or request.user.profile.role in MANAGEMENT_ROLES
    )
    return render(request, 'masters/crud_detail.html', {
        'object': instance,
        'resource': resource,
        'model_name': model._meta.verbose_name,
        'fields': fields,
        'can_manage': can_manage,
    })


@role_required(*MANAGEMENT_ROLES)
def master_update(request, resource, pk):
    config = _master_config(resource)
    if not config:
        raise Http404
    model, form_class, _, _ = config
    instance = get_object_or_404(model, pk=pk)
    form = form_class(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('masters:list', resource=resource)
    return render(request, 'masters/crud_form.html', {'form': form, 'resource': resource, 'model_name': model._meta.verbose_name})


@role_required(*MANAGEMENT_ROLES)
def master_delete(request, resource, pk):
    config = _master_config(resource)
    if not config:
        raise Http404
    model, _, _, _ = config
    instance = get_object_or_404(model, pk=pk)
    if request.method == 'POST':
        if hasattr(instance, 'is_active'):
            instance.is_active = False
            instance.save(update_fields=['is_active'])
        else:
            instance.delete()
        return redirect('masters:list', resource=resource)
    return render(request, 'masters/crud_confirm_delete.html', {'object': instance, 'resource': resource})
