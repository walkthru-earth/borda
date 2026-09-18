"""WooCommerce Store API (`/wp-json/wc/store/v1/products`) – public, JSON, paginated.

Prices are returned in minor units as strings (`"22000"` with `currency_minor_unit: 2`)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from decimal import Decimal

from ..models import Availability, RawOffer
from .base import BaseScraper
from .docs import extract_doc_links

_TAG_RE = re.compile(r"<[^>]+>")


class WooCommerceScraper(BaseScraper):
    platform = "woocommerce"
    page_size = 100

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        endpoint = f"{self.base}/wp-json/wc/store/v1/products"
        for page in range(1, self.max_pages + 1):
            data = await self.fetch.json(
                endpoint, params={"per_page": self.page_size, "page": page, "orderby": "id"}
            )
            if not isinstance(data, list) or not data:
                break
            self.pages += 1
            for p in data:
                prices = p.get("prices") or {}
                minor = int(prices.get("currency_minor_unit", 2))
                raw_price = prices.get("price")
                price = Decimal(raw_price) / (10**minor) if raw_price else None
                cats = [c["name"] for c in p.get("categories") or []]
                # Variable products: Store API reports the min price, which is what we track.
                extra = {}
                if p.get("type") == "variable":
                    extra["variable"] = True
                if p.get("on_sale"):
                    extra["on_sale"] = True
                offer = self.make_offer(
                    raw_name=_TAG_RE.sub("", p["name"]),
                    url=p["permalink"],
                    price=price,
                    currency=prices.get("currency_code") or self.currency,
                    availability=Availability.IN_STOCK
                    if p.get("is_in_stock")
                    else Availability.OUT_OF_STOCK,
                    sku=p.get("sku") or None,
                    category=cats[-1] if cats else None,
                    store_tags=cats + [t["name"] for t in p.get("tags") or []],
                    image=(p.get("images") or [{}])[0].get("src"),
                    description=(p.get("description") or p.get("short_description")) or None,
                    links=extract_doc_links(
                        (p.get("description") or "") + (p.get("short_description") or ""),
                        p["permalink"],
                    ),
                    extra=extra,
                )
                if offer:
                    yield offer
            if len(data) < self.page_size:
                break
