"""
FactoryFlow ERP - Masters & BOM Services
Handles Bill of Materials (BOM) recipe calculations, material requirements,
and stock availability verification.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Union, List, Dict, Tuple
from django.core.exceptions import ValidationError
from masters.models import Product, RawMaterial, BillOfMaterials
from inventory.services import get_raw_material_stock_balance


def calculate_bom_requirements(
    product: Union[Product, int],
    target_quantity: Decimal
) -> List[Dict]:
    """
    Calculates exact raw material and packaging requirements for a given finished product quantity.

    Formula per BOM item:
        unit_consumption = item.quantity / item.batch_size
        required_quantity = unit_consumption * target_quantity
    """
    if isinstance(target_quantity, float):
        raise ValidationError("target_quantity must be a Decimal instance, not float.")
    if not isinstance(target_quantity, Decimal):
        raise ValidationError(f"target_quantity must be a Decimal instance, got {type(target_quantity).__name__}.")
    if target_quantity <= Decimal('0.0000'):
        raise ValidationError("target_quantity must be strictly greater than zero.")

    prod = Product.objects.get(pk=product) if isinstance(product, int) else product
    bom_items = BillOfMaterials.objects.filter(product=prod, is_active=True).select_related('raw_material')

    if not bom_items.exists():
        raise ValidationError(f"No active Bill of Materials (BOM) found for product '{prod.name}'.")

    requirements = []
    for item in bom_items:
        consumption_per_unit = item.quantity / item.batch_size
        required_qty = (consumption_per_unit * target_quantity).quantize(
            Decimal('0.0001'),
            rounding=ROUND_HALF_UP
        )
        current_stock = get_raw_material_stock_balance(item.raw_material)
        is_sufficient = current_stock >= required_qty
        shortage = (required_qty - current_stock).quantize(Decimal('0.0001')) if not is_sufficient else Decimal('0.0000')

        requirements.append({
            'raw_material': item.raw_material,
            'raw_material_id': item.raw_material.pk,
            'raw_material_name': item.raw_material.name,
            'raw_material_code': item.raw_material.code,
            'unit': item.unit,
            'unit_display': item.get_unit_display(),
            'standard_recipe_qty': item.quantity,
            'standard_batch_size': item.batch_size,
            'required_quantity': required_qty,
            'current_stock': current_stock,
            'is_sufficient': is_sufficient,
            'shortage': shortage,
        })

    return requirements


def check_bom_material_availability(
    product: Union[Product, int],
    target_quantity: Decimal
) -> Tuple[bool, List[Dict]]:
    """
    Checks if all required raw materials for target_quantity of product are available in stock.
    Returns (all_available: bool, requirements: List[Dict]).
    """
    requirements = calculate_bom_requirements(product, target_quantity)
    all_available = all(item['is_sufficient'] for item in requirements)
    return all_available, requirements
