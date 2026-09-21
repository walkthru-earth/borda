"""Locafy v2 storefronts (Laravel + Livewire anonymous product listing).

The storefront has no Shopify/WooCommerce-style public REST catalog. Its own `/products`
page embeds the first product page in a Livewire snapshot; subsequent pages are returned by
anonymous POSTs to `/livewire/update`. Reuse that browser-facing interface rather than the
private `/api` and `/internal/api-data` routes excluded by robots.txt.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urljoin

from ..http import FetchError
from ..models import Availability, RawOffer
from .base import BaseScraper

_SNAPSHOT = re.compile(r'wire:snapshot="([^"]+)"')
_CSRF = re.compile(r'<script[^>]+data-csrf="([^"]+)"')
_COMPONENT = "product.product-listing"
_AVAILABILITY = {
    "in_stock": Availability.IN_STOCK,
    "out_of_stock": Availability.OUT_OF_STOCK,
    "preorder": Availability.PREORDER,
}


def _listing_snapshot(page: str) -> tuple[str, str]:
    """Return the product-listing snapshot and CSRF token embedded in `/products`."""
    token = _CSRF.search(page)
    if token is None:
        raise FetchError("Locafy product page has no Livewire CSRF token")
    for match in _SNAPSHOT.finditer(page):
        raw = html.unescape(match.group(1))
        try:
            snapshot = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if snapshot.get("memo", {}).get("name") == _COMPONENT:
            return raw, token.group(1)
    raise FetchError("Locafy product page has no product-listing snapshot")


def _unwrap_products(value: Any) -> list[dict[str, Any]]:
    """Decode Livewire's nested array tuples into ordinary product dictionaries."""
    while isinstance(value, list) and len(value) == 2 and isinstance(value[1], dict):
        value = value[0]
    if isinstance(value, list) and value and isinstance(value[0], list):
        return [p for item in value for p in _unwrap_products(item)]
    return [value] if isinstance(value, dict) and "slug" in value else []


class LocafyScraper(BaseScraper):
    platform = "locafy"

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        page = await self.fetch.text(f"{self.base}/products", use_cache=False)
        snapshot, csrf = _listing_snapshot(page)
        headers = {"X-CSRF-TOKEN": csrf, "X-Livewire": "true"}
        seen: set[str] = set()
        target_size = min(int(self.store.params.get("per_page", 96)), 96)

        # The initial HTML is page 1. Raise the supported page size immediately to keep the
        # catalog at roughly 56 anonymous requests instead of one request per product.
        if target_size != 24:
            snapshot = await self._update(snapshot, headers, updates={"perPage": target_size})

        for page_number in range(1, self.max_pages + 1):
            state = json.loads(snapshot)["data"]
            products = _unwrap_products(state.get("loadedProducts"))
            self.pages += 1
            for product in products:
                slug = str(product.get("slug") or "").strip()
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                price = product.get("special_price") or product.get("price")
                stock = str(product.get("stock_status") or "").lower()
                offer = self.make_offer(
                    raw_name=product.get("name"),
                    url=f"{self.base}/products/{slug}",
                    price=price,
                    currency=self.currency,
                    availability=_AVAILABILITY.get(stock, Availability.UNKNOWN),
                    sku=(str(product.get("sku") or "").strip() or None),
                    image=urljoin(self.base + "/", str(product.get("image_url") or "")) or None,
                    extra={"locafy_id": product.get("id")},
                )
                if offer:
                    yield offer

            if not state.get("hasMorePages") or page_number >= self.max_pages:
                break
            snapshot = await self._update(snapshot, headers, call="loadMore")

    async def _update(
        self,
        snapshot: str,
        headers: dict[str, str],
        *,
        updates: dict[str, Any] | None = None,
        call: str | None = None,
    ) -> str:
        calls = [{"path": "", "method": call, "params": []}] if call else []
        payload = {"components": [{"snapshot": snapshot, "updates": updates or {}, "calls": calls}]}
        response = await self.fetch.post_json(
            f"{self.base}/livewire/update", payload, headers=headers
        )
        try:
            return response["components"][0]["snapshot"]
        except (KeyError, IndexError, TypeError) as exc:
            raise FetchError("invalid Locafy Livewire response") from exc
