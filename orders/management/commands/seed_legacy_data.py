import time
from django.core.management.base import BaseCommand
from orders.models import LegacyOrder

class Command(BaseCommand):
    help = 'Seeds the LegacyOrder table with 500,000 records'

    def handle(self, *args, **options):
        self.stdout.write("Clearing existing LegacyOrder records...")
        LegacyOrder.objects.all().delete()

        self.stdout.write("Generating 500,000 legacy order records...")
        start_time = time.perf_counter()
        
        batch_size = 10000
        total_records = 500000
        batch = []

        for i in range(1, total_records + 1):
            external_id = f"legacy-{i}"
            
            # Generate deterministic item details to keep it fast and realistic
            sku_1 = f"SKU-A{(i % 100) + 1}"
            qty_1 = (i % 5) + 1
            price_1 = 19.99 + (i % 10)
            
            sku_2 = f"SKU-B{(i % 50) + 1}"
            qty_2 = (i % 3) + 1
            price_2 = 9.99 + (i % 5)
            
            total_val = (qty_1 * price_1) + (qty_2 * price_2)
            total_str = f"{total_val:.2f}"
            
            raw_data = {
                "customer_email": f"customer_{i}@example.com",
                "total": total_str,
                "items": [
                    {"sku": sku_1, "quantity": qty_1, "unit_price": f"{price_1:.2f}"},
                    {"sku": sku_2, "quantity": qty_2, "unit_price": f"{price_2:.2f}"}
                ]
            }
            
            batch.append(LegacyOrder(
                external_id=external_id,
                raw_data=raw_data,
                migrated=False
            ))
            
            if len(batch) >= batch_size:
                LegacyOrder.objects.bulk_create(batch)
                batch = []
                if i % 100000 == 0:
                    self.stdout.write(f"Seeded {i} records...")
                    
        if batch:
            LegacyOrder.objects.bulk_create(batch)

        elapsed = time.perf_counter() - start_time
        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded 500,000 legacy records in {elapsed:.2f} seconds."
        ))
