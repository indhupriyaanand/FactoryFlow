from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import Payment, SalesOrder, SalesOrderItem


class SalesOrderForm(forms.ModelForm):
    class Meta:
        model = SalesOrder
        fields = ('order_number', 'customer', 'order_date', 'notes')


class SalesOrderItemForm(forms.ModelForm):
    class Meta:
        model = SalesOrderItem
        fields = ('product', 'quantity', 'unit_price', 'discount_amount', 'gst_rate')


class SalesOrderItemFormSetBase(BaseInlineFormSet):
    def clean(self):
        if any(self.errors):
            return

        products = set()
        has_item = False
        for form in self.forms:
            data = form.cleaned_data
            if data.get('DELETE') or not data.get('product'):
                continue
            has_item = True
            product_id = data['product'].pk
            if product_id in products:
                raise forms.ValidationError(
                    'Each product may appear only once in a sales order.'
                )
            products.add(product_id)

        if not has_item:
            raise forms.ValidationError('A sales order must contain at least one item.')
        super().clean()


SalesOrderItemFormSet = inlineformset_factory(
    SalesOrder,
    SalesOrderItem,
    form=SalesOrderItemForm,
    formset=SalesOrderItemFormSetBase,
    extra=1,
    can_delete=True,
)


class SalesOrderEditForm(forms.ModelForm):
    class Meta:
        model = SalesOrder
        fields = ('customer', 'order_date', 'notes')


class DispatchForm(forms.Form):
    dispatch_date = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )
    vehicle_reference = forms.CharField(max_length=100, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))


class DispatchQuantityForm(forms.Form):
    item_id = forms.IntegerField(widget=forms.HiddenInput())
    quantity = forms.DecimalField(min_value=0, decimal_places=4, max_digits=12)


DispatchQuantityFormSet = forms.formset_factory(
    DispatchQuantityForm,
    extra=0,
)


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'payment_date', 'method', 'reference', 'notes')
