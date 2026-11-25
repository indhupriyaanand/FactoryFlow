from django import forms

from .models import BillOfMaterials, Customer, Product, RawMaterial, Supplier


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        exclude = ('created_at', 'updated_at')


class RawMaterialForm(forms.ModelForm):
    class Meta:
        model = RawMaterial
        exclude = ('created_at', 'updated_at')


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        exclude = ('created_at', 'updated_at')


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        exclude = ('created_at', 'updated_at')


class BillOfMaterialsForm(forms.ModelForm):
    class Meta:
        model = BillOfMaterials
        exclude = ('created_at', 'updated_at')
