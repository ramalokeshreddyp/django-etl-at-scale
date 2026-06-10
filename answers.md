# Django ETL Pipeline Architectural & Concept Q&A

This file contains the detailed answers to the design, optimization, and database theory questions regarding the Django ETL pipeline implementation.

---

## 1. Final Design of the `migrate_orders` Command

### Structure Choice:
The `migrate_orders` command is structured as an iterative batch processing engine leveraging a server-side cursor (`.iterator()`) and atomic transactions.

```mermaid
graph TD
    subgraph Extract Phase
        A[PostgreSQL Cursor] -->|Stream chunk_size=N| B[LegacyOrder Queryset]
    end
    subgraph Transform Phase
        B -->|Parse JSON raw_data| C[Instantiate Orders & OrderLines in memory]
    end
    subgraph Load Phase (Atomic Transaction)
        C -->|1. bulk_create| D[Order Table]
        D -->|2. in_bulk| E[Refetch generated Order PK IDs]
        E -->|3. Map parent IDs| F[Link OrderLines]
        F -->|4. bulk_create| G[OrderLine Table]
        G -->|5. update migrated=True| H[LegacyOrder Table]
    end
```

### Rationale:
1. **Loop**: QuerySet iteration streams legacy records in database-level chunks using `.iterator(chunk_size=batch_size)`. This ensures that we only hold a small batch in memory at any point.
2. **Batching**: Transformation occurs in memory, appending instantiated objects to buffer lists (`orders_to_create`, `lines_to_create`, `processed_ids`). When the buffers match the `--batch-size`, they are processed together to minimize round trips.
3. **Transaction**: The processing function wraps the database write operations in a `with transaction.atomic()` block. This ensures that for every batch of legacy orders, either the target tables (`Order`, `OrderLine`) are successfully populated and the legacy records are marked as migrated, or the database is rolled back to its exact pre-batch state in case of any failure.
4. **Primary Key Resolution**: Because `bulk_create` does not return primary keys on all database backends (particularly when bulk creating multi-tier relationships), we refetch the newly inserted `Order` records using `in_bulk(field_name='external_id')` to retrieve their database-generated IDs. This allows us to correctly populate the `order_id` foreign key on the `OrderLine` instances before bulk creating them.

---

## 2. Trade-offs: `bulk_create(ignore_conflicts=True)` vs. Loop of `update_or_create()`

| Feature | `bulk_create(ignore_conflicts=True)` | Loop of `update_or_create()` |
| :--- | :--- | :--- |
| **Performance** | **Extremely Fast**: Performs a single `INSERT` SQL statement for the entire batch. | **Slow**: Generates $2 \times N$ SQL statements (one `SELECT` to check existence, and one `INSERT` or `UPDATE` per record). |
| **Database Support** | Requires database support for conflict ignoring (e.g., PostgreSQL `ON CONFLICT DO NOTHING`). | Universally supported across all SQL databases. |
| **Django Signals** | Bypasses `pre_save` / `post_save` signals and model `.save()` method hooks. | Triggers all signals, model validations, and save hooks. |
| **Data Consistency** | Skips conflicting rows entirely; does not update existing records if data has changed. | Updates outdated records in-place, keeping data in sync. |

### Real-World Choice:
* **Choose `bulk_create(ignore_conflicts=True)`** when migrating a clean, read-only historical dataset where speed is critical, conflicting IDs are expected to be ignored, and no side-effect logic (like search indexing, cache busting, or signal-based third-party integrations) is bound to the save lifecycle.
* **Choose `update_or_create()`** (or optimized bulk update methods like `bulk_update()`) in incremental synchronization systems where existing database records might receive updates, model validation rules must be run on every record, or business logic requires signal handlers to fire.

---

## 3. Beyond `iterator()` and `bulk_create`: Advanced Performance Optimizations

If the transformation phase is complex (e.g. heavy JSON parsing, decryption, or computation), the following optimizations can be implemented:

1. **Parallel Processing**:
   - Utilize Python's `multiprocessing` library to split the CPU-intensive transformation phase across multiple CPU cores. A worker pool can transform batches of raw JSON into dictionaries in parallel, feeding the main process which handles the single-threaded database writes.
2. **Decommit / Disable Database Constraints Temporarily**:
   - Temporarily drop or disable foreign key checks, database triggers, and indexes before the migration, and recreate/re-index them after ingestion. This significantly reduces write write-path operations.
3. **Database COPY Command**:
   - Write transformed data directly to a temporary CSV/TSV buffer in memory (`io.StringIO`) and use PostgreSQL's native `COPY` protocol via raw SQL cursor commands (`cursor.copy_expert`). This completely bypasses the Django ORM overhead and is the fastest way to write data into Postgres.
4. **Deferring Calculation / Denormalization**:
   - Ingest data in its simplest state. Defer computationally heavy tasks (like calculating user stats, sending webhooks, or generating search indices) to asynchronous workers (e.g., Celery, RabbitMQ) to be executed post-migration.

---

## 4. Failure Handling & Resilience

### Connection Drops:
- Since we use `transaction.atomic()`, a connection drop mid-batch triggers a database-level rollback of the current transaction. No duplicate or half-written records are saved.
- Because the script is idempotent and supports the `--start-from` argument, the operator can safely restart the migration, and the pipeline will pick up exactly from the first unmigrated legacy record.

### Validation Errors (Bad Data):
- Currently, a validation error on a single record crashes the entire batch and rolls it back.
- **Improvements for Production Resilience**:
  1. **Dead Letter Queue (DLQ) / Error Logging**: Implement a `try-except` block for individual record transformation. If validation fails, log the offending legacy record ID and validation error details to a `migration_error_log` table (committed outside the main transaction) and proceed with the valid records in the batch.
  2. **Row-by-Row Fallback Strategy**: If a bulk database write fails, catch the error and execute a fallback loop to insert the batch records one-by-one. This isolates and logs the single failing record while successfully committing the remaining 999 records in the batch.

---

## 5. Memory Consumption: Standard QuerySet vs. `.iterator()`

### Standard QuerySet:
* When a standard QuerySet is evaluated (e.g. via `for order in LegacyOrder.objects.all()`), Django queries the database, retrieves **all** matching records, instantiates Python model instances for every row, and saves them in the QuerySet's internal cache (`_result_cache`).
* **The QuerySet Cache**: This cache lives in application memory for the lifecycle of the QuerySet object. While this prevents duplicate queries if the QuerySet is iterated over multiple times, it results in memory consumption scaling linearly with the number of rows. For 500,000 records, this causes immediate memory exhaustion (OOM crashes).

### `.iterator()`:
* `.iterator()` disables the QuerySet cache completely.
* Instead of loading all rows into Django memory, Django opens a **server-side cursor** on the database server.
* The application fetches and instantiates objects in small chunks (defined by `chunk_size`). Once a record has been processed and goes out of scope, Python's garbage collector immediately frees the memory. This guarantees a flat and constant memory footprint (e.g., ~9.5 MB) regardless of the dataset size.
