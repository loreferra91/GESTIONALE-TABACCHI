"""Synthetic, read-only Auto-Order 3.1 sales ingestion benchmark.

Run from repository root:
    python scripts/benchmark_auto_order_3_stream.py
    python scripts/benchmark_auto_order_3_stream.py --sizes 5000 50000 100000 500000

No database connection, filesystem write, secrets, or production data.
Measures incremental event processing, NOT Atlas network/query latency.
"""
from __future__ import annotations

import argparse
import sys
import time
import tracemalloc
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.forecasting.demand_models import NativeSalesAccumulator


def benchmark(size: int, products: int = 250) -> dict:
    end = date(2026, 11, 10)
    collector = NativeSalesAccumulator(end, 90)
    tracemalloc.start()
    started = time.perf_counter()
    for index in range(size):
        collector.add({
            "id": f"sale-{index}",
            "codice": f"P{index % products:05d}",
            "data": (end - timedelta(days=index % 90)).isoformat(),
            "canale": "VENDING" if index % 3 == 0 else "NEGOZIO",
            "sorgente": "MANUALE",
            "quantita": 1,
        })
    series, _ = collector.finish()
    seconds = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    observed = sum(sum(channel) for channels in series.values() for channel in channels.values())
    assert observed == size, f"Truncated sale events: {observed} != {size}"
    return {
        "events": size, "products": len(series), "seconds": round(seconds, 3),
        "events_per_second": round(size / max(seconds, .000001)),
        "peak_mib": round(peak_bytes / 1024**2, 2), "events_verified": observed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[5000, 50000, 100000],
                        help="Synthetic events per run; 500000 may require substantial memory")
    args = parser.parse_args()
    for size in args.sizes:
        if size < 1:
            parser.error("Sizes must be positive")
        print(benchmark(size), flush=True)


if __name__ == "__main__":
    main()
