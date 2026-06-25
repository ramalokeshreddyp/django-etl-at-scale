import time
import tracemalloc
from django.core.management.base import BaseCommand
from django.db import connection
from orders.models import LegacyOrder, Order, OrderLine

class Command(BaseCommand):
    help = 'Naive (unoptimized) migration of legacy orders for benchmarking purposes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit the number of records to migrate (for benchmarking)'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        
        # Start memory profiling
        tracemalloc.start()
        start_time = time.perf_counter()

        legacy_orders = LegacyOrder.objects.filter(migrated=False)
        if limit:
            # Evaluate with slicing
            legacy_orders = legacy_orders[:limit]
        
        # To simulate naive loading into memory, evaluate queryset into list
        legacy_orders_list = list(legacy_orders)
        processed_count = 0
        
        # Reset queries to measure query count
        connection.queries_log.clear()

        for legacy_order in legacy_orders_list:
            raw = legacy_order.raw_data
            
            # Create Order (1 query)
            order = Order.objects.create(
                external_id=legacy_order.external_id,
                customer_email=raw.get('customer_email', ''),
                total=raw.get('total', '0.00')
            )
            
            # Create OrderLines (1 query per item)
            for item in raw.get('items', []):
                OrderLine.objects.create(
                    order=order,
                    sku=item.get('sku', ''),
                    quantity=int(item.get('quantity', 0)),
                    unit_price=item.get('unit_price', '0.00')
                )
                
            # Update LegacyOrder migrated flag (1 query)
            legacy_order.migrated = True
            legacy_order.save()
            processed_count += 1

        elapsed = time.perf_counter() - start_time
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        total_mem = peak_bytes / (1024 * 1024)
        
        query_count = len(connection.queries)
        
        self.stdout.write(f"Processed {processed_count} records naively.")
        self.stdout.write(f"Total time: {elapsed:.2f} seconds.")
        self.stdout.write(f"Peak memory: {total_mem:.2f} MB.")
        self.stdout.write(f"Database queries: {query_count}")
