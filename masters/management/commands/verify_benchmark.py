from django.core.management.base import BaseCommand, CommandError

from masters.models import BillOfMaterials, Customer, Product, RawMaterial


class Command(BaseCommand):
    help = 'Verifies the Murukku 200g benchmark master-data scenario.'

    def handle(self, *args, **options):
        product = Product.objects.filter(code='PRD-MUR-200G', is_active=True).first()
        if not product:
            raise CommandError('Benchmark product PRD-MUR-200G is missing.')
        material_codes = {'RM-FLOUR', 'RM-OIL', 'RM-SPICE', 'RM-POUCH', 'RM-CARTON'}
        found_materials = set(
            RawMaterial.objects.filter(code__in=material_codes, is_active=True)
            .values_list('code', flat=True)
        )
        missing_materials = material_codes - found_materials
        if missing_materials:
            raise CommandError(f'Missing benchmark materials: {sorted(missing_materials)}')
        if BillOfMaterials.objects.filter(product=product, is_active=True).count() != 5:
            raise CommandError('Benchmark product must have exactly five active BOM items.')
        if not Customer.objects.filter(name='Retailer A', is_active=True).exists():
            raise CommandError('Retailer A benchmark customer is missing.')
        if not Customer.objects.filter(name='Distributor B', is_active=True).exists():
            raise CommandError('Distributor B benchmark customer is missing.')
        self.stdout.write(self.style.SUCCESS('FactoryFlow benchmark master data verified.'))
