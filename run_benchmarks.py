import os
import django
import io
import re

# Setup django context
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'etl_project.settings')
django.setup()

from django.core.management import call_command
from django.db import connection
from orders.models import LegacyOrder, Order, OrderLine

def reset_db():
    print("Resetting database...")
    Order.objects.all().delete()
    LegacyOrder.objects.all().update(migrated=False)

def run_naive():
    reset_db()
    print("Running naive migration for 1000 records...")
    
    out = io.StringIO()
    # Reset queries to measure exact queries
    connection.queries_log.clear()
    
    call_command('migrate_orders_naive', limit=1000, stdout=out)
    output_text = out.getvalue()
    print(output_text)
    
    # Parse metrics
    time_match = re.search(r"Total time:\s+([\d\.]+)\s+seconds", output_text)
    mem_match = re.search(r"Peak memory:\s+([\d\.]+)\s+MB", output_text)
    queries_match = re.search(r"Database queries:\s+(\d+)", output_text)
    
    elapsed = float(time_match.group(1)) if time_match else 0.0
    memory = float(mem_match.group(1)) if mem_match else 0.0
    queries = int(queries_match.group(1)) if queries_match else 0
    
    return elapsed, memory, queries

def run_optimized_queries():
    reset_db()
    print("Running optimized query count check for 1000 records...")
    
    connection.queries_log.clear()
    out = io.StringIO()
    call_command('migrate_orders', batch_size=1000, limit=1000, stdout=out)
    output_text = out.getvalue()
    
    queries = len(connection.queries)
    print(f"Optimized Queries in DEBUG mode: {queries}")
    return queries

def run_optimized_benchmarks():
    results = {}
    batch_sizes = [100, 500, 1000, 5000]
    for size in batch_sizes:
        reset_db()
        print(f"Running optimized benchmark with batch-size {size} for 50,000 records...")
        
        out = io.StringIO()
        call_command('migrate_orders', batch_size=size, limit=50000, stdout=out)
        output_text = out.getvalue()
        
        # Parse metrics
        time_match = re.search(r"Total time:\s+([\d\.]+)\s+seconds", output_text)
        mem_match = re.search(r"Peak memory usage:\s+([\d\.]+)\s+MB", output_text)
        
        elapsed = float(time_match.group(1)) if time_match else 0.0
        memory = float(mem_match.group(1)) if mem_match else 0.0
        
        extrapolated_time = elapsed * 10
        print(f"Batch Size {size} Results: Time={elapsed:.2f}s (Extrapolated={extrapolated_time:.2f}s), PeakMemory={memory:.2f}MB")
        
        results[size] = {
            'time_50k': elapsed,
            'time_500k_extrapolated': extrapolated_time,
            'memory': memory
        }
    return results

if __name__ == '__main__':
    naive_time, naive_mem, naive_queries = run_naive()
    opt_queries = run_optimized_queries()
    opt_runs = run_optimized_benchmarks()
    
    print("\n=======================================================")
    print("               BENCHMARK REPORT MARKDOWN               ")
    print("=======================================================\n")
    print("### 1. Memory and Query Count Comparison (1,000 records)")
    print("| Metric | Naive Approach | Optimized (Iterator + bulk_create) | Reduction |")
    print("| :--- | :--- | :--- | :--- |")
    print(f"| Peak Memory (MB) | {naive_mem:.2f} MB | {opt_runs[1000]['memory']:.2f} MB | {((naive_mem - opt_runs[1000]['memory'])/naive_mem)*100:.2f}% |")
    print(f"| Query Count | {naive_queries} | {opt_queries} | {((naive_queries - opt_queries)/naive_queries)*100:.2f}% |")
    print(f"| Time Taken (s) | {naive_time:.2f} s | {opt_runs[1000]['time_50k']/50:.2f} s | {((naive_time - (opt_runs[1000]['time_50k']/50))/naive_time)*100:.2f}% |")
    
    print("\n### 2. Execution Time vs Batch Size (50,000 records run, extrapolated to 500,000)")
    print("| Batch Size | Time for 50,000 records (s) | Extrapolated Time for 500,000 records (s) | Throughput (rec/s) | Peak Memory (MB) |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    for size, res in opt_runs.items():
        throughput = 50000 / res['time_50k']
        print(f"| {size} | {res['time_50k']:.2f} s | {res['time_500k_extrapolated']:.2f} s | {throughput:.2f} rec/s | {res['memory']:.2f} MB |")
