from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import PurchaseInvoice, PurchaseItem


class PurchaseInvoiceForm(forms.ModelForm):
    class Meta:
        model = PurchaseInvoice
        fields = ('invoice_number', 'supplier', 'purchase_date', 'notes')


class PurchaseItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseItem
        fields = ('raw_material', 'quantity', 'unit', 'unit_price')


class PurchaseItemFormSetBase(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        materials = set()
        has_item = False
        for form in self.forms:
            data = form.cleaned_data
            if data.get('DELETE') or not data.get('raw_material'):
                continue
            has_item = True
            material_id = data['raw_material'].pk
            if material_id in materials:
                raise forms.ValidationError(
                    'Each raw material may appear only once on a purchase invoice.'
                )
            materials.add(material_id)

        if not has_item:
            raise forms.ValidationError(
                'A purchase invoice must contain at least one item.'
            )


PurchaseItemFormSet = inlineformset_factory(
    PurchaseInvoice,
    PurchaseItem,
    form=PurchaseItemForm,
    formset=PurchaseItemFormSetBase,
    extra=1,
    can_delete=True,
)
