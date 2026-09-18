"""Row-level validation after dedupe: flag outliers and missing mandatory data.

Pydantic already rejected structurally invalid rows inside the scrapers; here we look at
a product across sellers and history and *flag* (never drop) suspicious observations so
they are excluded from stats but remain auditable in the snapshot."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from statistics import median

from ..models import OfferRecord


def flag_offers(
    offers: list[OfferRecord],
    *,
    reference_prices: dict[str, list[float]] | None = None,
    factor: float = 8.0,
) -> dict[str, int]:
    """Mutates `offers[*].flags`; returns a counter of flag -> occurrences.

    `reference_prices` maps product id -> recent historic prices (helps when a product
    has a single seller this run)."""
    by_product: dict[str, list[Decimal]] = defaultdict(list)
    for o in offers:
        if o.price is not None and o.currency == "EGP":
            by_product[o.product_id].append(o.price)
    if reference_prices:
        for pid, hist in reference_prices.items():
            by_product[pid].extend(Decimal(str(v)) for v in hist)

    counts: dict[str, int] = defaultdict(int)
    for o in offers:
        flags: list[str] = []
        if o.price is None:
            flags.append("missing_price")
        elif o.price < Decimal("0.05"):
            flags.append("implausible_price")
        elif o.currency == "EGP":
            prices = by_product[o.product_id]
            if len(prices) >= 3:
                med = median(prices)
                if med > 0 and o.price > med * Decimal(factor):
                    flags.append("outlier_high")
                elif med > 0 and o.price * Decimal(factor) < med:
                    flags.append("outlier_low")
        if o.currency != "EGP":
            flags.append("foreign_currency")
        o.flags = flags
        for f in flags:
            counts[f] += 1
    return dict(counts)
