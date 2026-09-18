"""Chart-ready series + per-product stats, rebuilt from the full offers history so
product merges/redirects apply retroactively."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Any

import pyarrow as pa

from ..models import Availability
from ..normalize import Catalog

_EXCLUDE_FLAGS = {"outlier_high", "outlier_low", "implausible_price"}


def build_series(
    offers: pa.Table, catalog: Catalog
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (series_rows, stats_rows). Flagged outliers stay in history, not in charts."""
    if offers.num_rows == 0:
        return [], []
    cols = offers.select(
        ["ts", "product_id", "seller", "price", "currency", "availability", "url", "flags"]
    ).to_pydict()
    latest_ts: datetime = max(cols["ts"])

    points: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for i in range(offers.num_rows):
        pid = catalog.resolve_id(cols["product_id"][i])
        if pid not in catalog.products:
            continue
        flags = cols["flags"][i] or []
        if _EXCLUDE_FLAGS.intersection(flags):
            continue
        points[pid].append(
            {
                "product_id": pid,
                "ts": cols["ts"][i],
                "seller": cols["seller"][i],
                "price": cols["price"][i],
                "currency": cols["currency"][i],
                "availability": cols["availability"][i],
                "url": cols["url"][i],
            }
        )

    series_rows: list[dict[str, Any]] = []
    stats_rows: list[dict[str, Any]] = []
    for pid, pts in points.items():
        series_rows.extend(pts)
        prices = [p["price"] for p in pts if p["price"] is not None]
        latest = [p for p in pts if p["ts"] == latest_ts]
        latest_prices = [p["price"] for p in latest if p["price"] is not None]
        prod = catalog.products[pid]
        stats_rows.append(
            {
                "product_id": pid,
                "canonical_name": prod.canonical_name,
                "currency": pts[0]["currency"],
                "min": min(prices) if prices else None,
                "max": max(prices) if prices else None,
                "median": round(median(prices), 2) if prices else None,
                "latest_min": min(latest_prices) if latest_prices else None,
                "latest_ts": latest_ts if latest else None,
                "in_stock_sellers": len(
                    {p["seller"] for p in latest if p["availability"] == Availability.IN_STOCK}
                ),
                "observations": len(pts),
                "sellers": prod.sellers,
                "tags": prod.tags,
                "image": str(prod.image) if prod.image else None,
            }
        )
    return series_rows, stats_rows


def reference_prices(
    offers: pa.Table, catalog: Catalog, last_runs: int = 3
) -> dict[str, list[float]]:
    """Recent unflagged prices per product – context for outlier detection."""
    if offers.num_rows == 0:
        return {}
    cols = offers.select(["run_id", "product_id", "price", "flags"]).to_pydict()
    keep = set(sorted(set(cols["run_id"]))[-last_runs:])
    ref: dict[str, list[float]] = defaultdict(list)
    for i in range(offers.num_rows):
        if cols["run_id"][i] in keep and cols["price"][i] is not None and not cols["flags"][i]:
            ref[catalog.resolve_id(cols["product_id"][i])].append(cols["price"][i])
    return {k: sorted(v) for k, v in ref.items()}
