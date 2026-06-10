import time
import tracemalloc
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from orders.models import LegacyOrder, Order, OrderLine

class Command(BaseCommand):
    help = 'Production-grade, memory-efficient, and resumable order migration pipeline'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of records to process in a single batch'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Pretend to run migration without writing to the database'
        )
        parser.add_argument(
            '--start-from',
            type=str,
            default=None,
            help='External ID to start/resume the migration from'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit the number of records to process (for benchmarking)'
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        start_from = options['start_from']
        limit = options.get('limit')

        # Start tracking memory and time
        tracemalloc.start()
        start_time = time.perf_counter()

        # Build base queryset for unprocessed legacy orders
        queryset = LegacyOrder.objects.filter(migrated=False).order_by('external_id')
        if start_from:
            queryset = queryset.filter(external_id__gte=start_from)

        orders_to_create = []
        lines_to_create = []
        processed_ids = []
        total_processed = 0

        def process_batch(orders, lines, legacy_ids):
            if dry_run:
                self.stdout.write(f"[Dry Run] Would process {len(orders)} records.")
                return

            try:
                with transaction.atomic():
                    # Step 1: Create Orders in bulk
                    Order.objects.bulk_create(orders)
                    
                    # Step 2: Re-fetch created orders by their unique external_id to retrieve the auto-generated primary keys
                    # Using in_bulk to map external_id to the Order object containing its new PK
                    created_orders = Order.objects.in_bulk(
                        id_list=[o.external_id for o in orders],
                        field_name='external_id'
                    )

                    # Step 3: Associate OrderLines with their newly created parent Orders
                    for line in lines:
                        # line.order was pointing to an unsaved Order instance; update it to the database-persisted instance
                        line.order = created_orders[line.order.external_id]
                    
                    # Step 4: Create OrderLines in bulk
                    OrderLine.objects.bulk_create(lines)

                    # Step 5: Mark legacy orders as migrated
                    LegacyOrder.objects.filter(id__in=legacy_ids).update(migrated=True)
                    
                    self.stdout.write(self.style.SUCCESS(f"Successfully processed batch of {len(orders)} records."))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"An error occurred: {e}"))
                raise e

        # Use iterator(chunk_size=...) to fetch records from db in database-level chunks
        for legacy_order in queryset.iterator(chunk_size=batch_size):
            if limit is not None and total_processed >= limit:
                if orders_to_create:
                    process_batch(orders_to_create, lines_to_create, processed_ids)
                    orders_to_create = []
                    lines_to_create = []
                    processed_ids = []
                break

            raw = legacy_order.raw_data
            
            # 1. Transform LegacyOrder into Order instance (not saved yet)
            new_order = Order(
                external_id=legacy_order.external_id,
                customer_email=raw.get('customer_email', ''),
                total=raw.get('total', '0.00')
            )
            
            # 2. Transform items list into OrderLine instances (not saved yet)
            for item in raw.get('items', []):
                new_line = OrderLine(
                    sku=item.get('sku', ''),
                    quantity=int(item.get('quantity', 0)),
                    unit_price=item.get('unit_price', '0.00')
                )
                # Temporarily reference the unsaved Order instance so we can inspect its external_id during batch processing
                new_line.order = new_order
                lines_to_create.append(new_line)
                
            orders_to_create.append(new_order)
            processed_ids.append(legacy_order.id)
            total_processed += 1

            # 3. Process the batch when it reaches the specified batch size
            if len(orders_to_create) >= batch_size:
                process_batch(orders_to_create, lines_to_create, processed_ids)
                orders_to_create = []
                lines_to_create = []
                processed_ids = []

        # 4. Process any remaining records in the last partial batch
        if orders_to_create:
            process_batch(orders_to_create, lines_to_create, processed_ids)

        elapsed = time.perf_counter() - start_time
        snapshot = tracemalloc.take_snapshot()
        tracemalloc.stop()

        top_stats = snapshot.statistics('lineno')
        total_mem = sum(stat.size for stat in top_stats) / (1024 * 1024) # MB

        throughput = total_processed / elapsed if elapsed > 0 else 0

        self.stdout.write(self.style.SUCCESS("Migration completed successfully!"))
        self.stdout.write(f"Total processed records: {total_processed}")
        self.stdout.write(f"Total time: {elapsed:.4f} seconds")
        self.stdout.write(f"Throughput: {throughput:.2f} records per second")
        self.stdout.write(f"Peak memory usage: {total_mem:.2f} MB")
