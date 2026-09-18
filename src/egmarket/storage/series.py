"""Chart-ready series + per-product stats, rebuilt from the full offers history so
product merges/redirects apply retroactively."""

from __future__ import annotations

import json
from collections import defaultdict
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
    # A run may scrape only a subset of stores. Observations from untouched stores
    # remain current until that store is observed again.
    seller_latest = {}
    for seller, ts in zip(cols["seller"], cols["ts"], strict=True):
        seller_latest[seller] = max(seller_latest.get(seller, ts), ts)

    points: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for i in range(offers.num_rows):
        pid = catalog.resolve_listing(cols["product_id"][i], cols["seller"][i], cols["url"][i])
        if pid not in catalog.products:
            continue
        flags = cols["flags"][i] or []
        if _EXCLUDE_FLAGS.intersection(flags) or cols["currency"][i] != "EGP":
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
        latest = [p for p in pts if p["ts"] == seller_latest[p["seller"]]]
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
                "latest_ts": max(p["ts"] for p in pts),
                "in_stock_sellers": len(
                    {p["seller"] for p in latest if p["availability"] == Availability.IN_STOCK}
                ),
                "observations": len(pts),
                "sellers": prod.sellers,
                "tags": prod.tags,
                "image": str(prod.image) if prod.image else None,
                "group": prod.group or "other",
                "current_offers": json.dumps(
                    [
                        {
                            k: v.isoformat() if k == "ts" else v
                            for k, v in p.items()
                            if k != "product_id"
                        }
                        for p in sorted(latest, key=lambda p: (p["seller"], p["url"]))
                    ],
                    separators=(",", ":"),
                ),
            }
        )
    return series_rows, stats_rows


def reference_prices(
    offers: pa.Table, catalog: Catalog, last_runs: int = 3
) -> dict[str, list[float]]:
    """Recent unflagged prices per product – context for outlier detection."""
    if offers.num_rows == 0:
        return {}
    if last_runs <= 0:
        return {}
    cols = offers.select(
        ["run_id", "product_id", "seller", "url", "price", "currency", "flags"]
    ).to_pydict()
    keep = set(sorted(set(cols["run_id"]))[-last_runs:])
    ref: dict[str, list[float]] = defaultdict(list)
    for i in range(offers.num_rows):
        if (
            cols["run_id"][i] in keep
            and cols["price"][i] is not None
            and not cols["flags"][i]
            and cols["currency"][i] == "EGP"
        ):
            pid = catalog.resolve_listing(cols["product_id"][i], cols["seller"][i], cols["url"][i])
            if pid in catalog.products:
                ref[pid].append(cols["price"][i])
    return {k: sorted(v) for k, v in ref.items()}
