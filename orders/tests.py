from django.test import TestCase
from django.core.management import call_command
from orders.models import LegacyOrder, Order, OrderLine

class ETLPipelineTestCase(TestCase):
    def setUp(self):
        # Clear database records to ensure isolated test runs
        LegacyOrder.objects.all().delete()
        Order.objects.all().delete()
        OrderLine.objects.all().delete()

    def test_seed_command(self):
        """
        Verify that seed_legacy_data creates exactly the requested number of records.
        """
        call_command('seed_legacy_data', count=50)
        self.assertEqual(LegacyOrder.objects.count(), 50)
        self.assertEqual(LegacyOrder.objects.filter(migrated=False).count(), 50)
        
        # Verify JSON schema is valid
        first_order = LegacyOrder.objects.first()
        raw = first_order.raw_data
        self.assertIn('customer_email', raw)
        self.assertIn('total', raw)
        self.assertIn('items', raw)
        self.assertEqual(len(raw['items']), 2)

    def test_dry_run_migration(self):
        """
        Verify that the --dry-run flag prevents any database writes.
        """
        call_command('seed_legacy_data', count=20)
        
        # Run migration with --dry-run
        call_command('migrate_orders', batch_size=5, dry_run=True)
        
        # Verify no target records were created and legacy flag remains False
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderLine.objects.count(), 0)
        self.assertEqual(LegacyOrder.objects.filter(migrated=True).count(), 0)

    def test_successful_migration_and_idempotency(self):
        """
        Verify that a normal migration successfully translates all records,
        and running it a second time does not create duplicates.
        """
        call_command('seed_legacy_data', count=30)
        
        # Run migration
        call_command('migrate_orders', batch_size=10)
        
        # Verify normalization counts (each legacy order has 2 line items)
        self.assertEqual(Order.objects.count(), 30)
        self.assertEqual(OrderLine.objects.count(), 60)
        self.assertEqual(LegacyOrder.objects.filter(migrated=False).count(), 0)
        self.assertEqual(LegacyOrder.objects.filter(migrated=True).count(), 30)
        
        # Record target counts before second run
        order_count = Order.objects.count()
        line_count = OrderLine.objects.count()
        
        # Run migration again (Idempotency test)
        call_command('migrate_orders', batch_size=10)
        
        # Verify no duplicate entries were added
        self.assertEqual(Order.objects.count(), order_count)
        self.assertEqual(OrderLine.objects.count(), line_count)

    def test_migration_resumability(self):
        """
        Verify that the migration can resume from a specified external ID.
        """
        call_command('seed_legacy_data', count=10)
        # External IDs are legacy-1, legacy-2, ..., legacy-10.
        # Alphabetically sorted order: legacy-1, legacy-10, legacy-2, legacy-3, legacy-4, legacy-5, legacy-6, legacy-7, legacy-8, legacy-9.
        
        # Start migration from legacy-5 (should migrate legacy-5, legacy-6, legacy-7, legacy-8, legacy-9)
        # Wait, let's verify alphabetical order:
        # legacy-5, legacy-6, legacy-7, legacy-8, legacy-9 are >= legacy-5.
        # legacy-1, legacy-10, legacy-2, legacy-3, legacy-4 are < legacy-5.
        # So it should migrate exactly 5 records: legacy-5, legacy-6, legacy-7, legacy-8, legacy-9.
        call_command('migrate_orders', batch_size=5, start_from='legacy-5')
        
        # Verify target counts
        self.assertEqual(Order.objects.count(), 5)
        # Verify which ones are migrated
        migrated_ids = list(Order.objects.values_list('external_id', flat=True))
        for ext_id in ['legacy-5', 'legacy-6', 'legacy-7', 'legacy-8', 'legacy-9']:
            self.assertIn(ext_id, migrated_ids)
        for ext_id in ['legacy-1', 'legacy-2', 'legacy-3', 'legacy-4', 'legacy-10']:
            self.assertNotIn(ext_id, migrated_ids)
