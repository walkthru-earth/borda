"""Shopify storefronts expose `/products.json?limit=250&page=N` publicly."""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..models import Availability, RawOffer
from .base import BaseScraper


class ShopifyScraper(BaseScraper):
    platform = "shopify"
    page_size = 250

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        currency = self.store.params.get("currency", "EGP")
        for page in range(1, self.max_pages + 1):
            data = await self.fetch.json(
                f"{self.base}/products.json", params={"limit": self.page_size, "page": page}
            )
            products = data.get("products") or []
            if not products:
                break
            self.pages += 1
            for p in products:
                url = f"{self.base}/products/{p['handle']}"
                variants = p.get("variants") or []
                if not variants:
                    continue
                # Cheapest variant wins; variant title kept in extra for provenance.
                priced = [v for v in variants if v.get("price")]
                v = min(priced, key=lambda v: float(v["price"])) if priced else variants[0]
                multi = len(variants) > 1 and v.get("title") not in (None, "Default Title")
                offer = self.make_offer(
                    raw_name=p["title"],
                    url=url,
                    price=v.get("price"),
                    currency=currency,
                    availability=Availability.IN_STOCK
                    if any(x.get("available") for x in variants)
                    else Availability.OUT_OF_STOCK,
                    sku=v.get("sku") or None,
                    brand=p.get("vendor") or None,
                    category=p.get("product_type") or None,
                    store_tags=p.get("tags") or [],
                    image=(p.get("images") or [{}])[0].get("src"),
                    extra={"variant": v.get("title")} if multi else {},
                )
                if offer:
                    yield offer
            if len(products) < self.page_size:
                break
