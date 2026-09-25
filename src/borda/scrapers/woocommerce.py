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
    default_page_size = 10  # what the Store API returns when `per_page` is omitted

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        endpoint = f"{self.base}/wp-json/wc/store/v1/products"
        # `params.page_size: null` omits `per_page` (some WAFs reject it) and relies on the
        # API default page size; an int overrides the class default.
        send_per_page = True
        page_size = self.page_size
        if "page_size" in self.store.params:
            if self.store.params["page_size"] is None:
                send_per_page = False
                page_size = self.default_page_size
            else:
                page_size = int(self.store.params["page_size"])
        for page in range(1, self.max_pages + 1):
            params: dict[str, int | str] = {"page": page, "orderby": "id"}
            if send_per_page:
                params = {"per_page": page_size, **params}
            data = await self.fetch.json(endpoint, params=params)
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
            if len(data) < page_size:
                break
