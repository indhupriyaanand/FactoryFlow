from decimal import Decimal

from django import forms
from django.forms import modelformset_factory

from .models import ProductionDamageRecord, ProductionOrder, ProductionMaterialUsage


class ProductionOrderForm(forms.ModelForm):
    class Meta:
        model = ProductionOrder
        fields = ('order_number', 'product', 'target_quantity', 'start_date', 'notes')


class ProductionCompletionForm(forms.Form):
    produced_quantity = forms.DecimalField(min_value=0, decimal_places=4, max_digits=12)
    damaged_pouch_quantity = forms.DecimalField(min_value=0, required=False, initial=0, decimal_places=4, max_digits=12)
    labour_cost = forms.DecimalField(min_value=0, required=False, initial=0, decimal_places=2, max_digits=12)
    power_cost = forms.DecimalField(min_value=0, required=False, initial=0, decimal_places=2, max_digits=12)


class ProductionMaterialUsageForm(forms.ModelForm):
    class Meta:
        model = ProductionMaterialUsage
        fields = ('actual_quantity',)
        labels = {'actual_quantity': 'Actual consumed quantity'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['actual_quantity'].min_value = Decimal('0.0001')
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial['actual_quantity'] = self.instance.planned_quantity


ProductionMaterialUsageFormSet = modelformset_factory(
    ProductionMaterialUsage,
    form=ProductionMaterialUsageForm,
    extra=0,
)


class DamageRecordForm(forms.ModelForm):
    class Meta:
        model = ProductionDamageRecord
        fields = (
            'production_order',
            'raw_material',
            'product',
            'quantity',
            'unit',
            'reason',
            'recorded_at',
        )
        widgets = {
            'recorded_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, damage_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.damage_type = damage_type or (
            self.instance.damage_type if self.instance.pk else None
        )
        if self.damage_type == 'DAMAGED_POUCH':
            self.fields['product'].required = False
            self.fields['product'].widget = forms.HiddenInput()
        else:
            self.fields['raw_material'].required = False

    def clean(self):
        cleaned = super().clean()
        raw_material = cleaned.get('raw_material')
        product = cleaned.get('product')
        if self.damage_type == 'DAMAGED_POUCH' and not raw_material:
            self.add_error('raw_material', 'Select the damaged pouch material.')
        if self.damage_type != 'DAMAGED_POUCH' and not raw_material and not product:
            raise forms.ValidationError('Select a scrap raw material or finished product.')
        return cleaned


class DamagedPouchForm(DamageRecordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, damage_type='DAMAGED_POUCH', **kwargs)


class ScrapForm(DamageRecordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, damage_type='SCRAP', **kwargs)


class DamageEditForm(forms.ModelForm):
    class Meta:
        model = ProductionDamageRecord
        fields = ('reason', 'recorded_at')
        widgets = {
            'recorded_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }
