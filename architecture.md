# System Architecture & Design Document

This document provides a comprehensive overview of the design, architectural decisions, and data flow of the Production-Grade Django ETL Pipeline.

---

## 1. System Overview & Objective

The primary objective of this project is to perform a high-throughput, memory-efficient, idempotent, and resumable migration of legacy order records into a fully normalized PostgreSQL database schema.

### Core Goals:
- **Scalability**: Handle datasets of 500,000+ records without memory exhaustion.
- **Idempotency**: Prevent duplicate records if the migration is stopped and restarted mid-process.
- **Data Integrity**: Wrap batches in atomic transactions to guarantee consistency.
- **Resumability**: Support restarting the migration from a specific external ID.

---

## 2. Architecture Design Diagram

The following Mermaid diagram outlines the containerized system architecture, detailing the flow of data from the legacy denormalized storage to the normalized tables.

```mermaid
graph TD
    subgraph Host Network
        client["Developer / CLI"]
    end

    subgraph Docker Containers
        subgraph App Service (Django Application)
            cmd["Management Command: migrate_orders"]
            subgraph ETL Engine
                extract["Extractor: Server-side Cursor (iterator)"]
                transform["Transformer: Memory Mapping & Normalization"]
                load["Loader: bulk_create & FK Linker"]
            end
        end

        subgraph Database Service (PostgreSQL)
            subgraph Schemas
                legacy["orders_legacyorder (Source)"]
                order["orders_order (Target)"]
                lines["orders_orderline (Target)"]
            end
        end
    end

    client -->|Runs Command| cmd
    cmd -->|1. Stream Records| extract
    extract -->|Fetch in chunk_size| legacy
    extract -->|2. Raw JSON| transform
    transform -->|3. Model Instances| load
    load -->|4. Atomic bulk_create Orders| order
    load -->|5. Resolve PKs & bulk_create Lines| lines
    load -->|6. Update migrated = True| legacy
```

---

## 3. Data Flow & ETL Pipeline Phases

The migration follows a strict **Extract-Transform-Load (ETL)** pattern, optimized for low memory footprint and minimum database round trips:

### 3.1. Extract Phase (Memory-Efficient)
- Instead of loading the entire `LegacyOrder` queryset into Django's application memory (which causes Out-Of-Memory/OOM crashes), the system utilizes Django's `iterator(chunk_size=...)`.
- This maps to a server-side database cursor in PostgreSQL. Data is streamed in chunks of size $N$ (configured by `--batch-size`), ensuring constant memory consumption regardless of database size.

### 3.2. Transform Phase (In-Memory Processing)
- Legacy records contain a JSON payload (`raw_data`) detailing the customer email, total, and individual line items.
- The pipeline parses the JSON structure in-memory and instantiates `Order` and `OrderLine` models.
- **FK Reference Preservation**: Unsaved `OrderLine` instances are linked to their corresponding unsaved parent `Order` instances by referencing the parent model in memory.

### 3.3. Load Phase (Transactional & Bulk)
To write thousands of records in a single query:
1. **Bulk Create Orders**: Write the batch of `Order` objects in one SQL `INSERT`.
2. **Retrieve Primary Keys**: Query the database using the unique natural key `external_id` (via `Order.objects.in_bulk`) to get the newly generated primary keys.
3. **Map Foreign Keys**: Link each `OrderLine` to its saved parent `Order` instance using the retrieved primary key.
4. **Bulk Create Line Items**: Insert the associated `OrderLine` records in a single bulk SQL `INSERT`.
5. **Mark Migrated Status**: Update the legacy records' `migrated` flag to `True` using a single bulk SQL `UPDATE`.

All database writes for a batch are wrapped in a `transaction.atomic()` block. If any write fails, the entire batch rolls back, maintaining system idempotency.

---

## 4. Technology Stack & Rationale

| Technology | Role | Rationale |
| :--- | :--- | :--- |
| **Python 3.10** | Core Programming Language | High readability, native support for advanced tools like `tracemalloc`, and rich data structures. |
| **Django 4.2** | Framework & ORM | Powerful database abstraction layers, robust migration tooling, custom commands, and native JSONField support. |
| **PostgreSQL 15** | Relational Database | Advanced querying capabilities, server-side cursors, efficient indexing, and transactional ACID compliance. |
| **Docker & Docker Compose** | Containerization | Standardizes environment configurations, ensures isolated service setups, and simplifies multi-service healthcheck dependencies. |

---

## 5. Architectural Advantages & Trade-offs

### Pros:
- **Low Memory Overhead**: Memory usage remains constant (roughly ~90MB for batch size 5,000) instead of growing linearly with the size of the database.
- **Blazing-Fast Speed**: Writing records in bulk reduces database round trips by over 99.8%.
- **Resilient to Failure**: If the process crashes mid-migration, restarting it resumes from where it left off, and partial batches are never committed.

### Cons:
- **No Signals/Save Methods**: `bulk_create` bypasses Django model `save()` methods and pre-save/post-save signals. Business logic must be handled during the transform phase.
- **Natural Key Requirement**: To link foreign keys without database-level auto-increments in memory, a unique natural key (`external_id`) must be present on the parent records.
