"""PrestaShop 1.7+ – listing pages answer with JSON when requested via XHR headers.

`params.listing` is the category path that lists every product (e.g. `2-home`)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..models import Availability, RawOffer
from .base import BaseScraper
from .docs import extract_doc_links

_XHR = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}


class PrestaShopScraper(BaseScraper):
    platform = "prestashop"

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        listing = self.store.params.get("listing", "2-home")
        currency = self.store.params.get("currency", self.currency)
        page, pages_count = 1, 1
        while page <= min(pages_count, self.max_pages):
            data = await self.fetch.json(
                f"{self.base}/{listing}", params={"page": page, "from-xhr": ""}, headers=_XHR
            )
            self.pages += 1
            pages_count = int(data.get("pagination", {}).get("pages_count") or 1)
            for p in data.get("products") or []:
                # `add_to_cart_url` is null when the product cannot be bought (out of stock).
                in_stock = bool(p.get("add_to_cart_url"))
                if (q := p.get("quantity")) is not None:
                    in_stock = int(q) > 0
                cover = p.get("cover") or {}
                offer = self.make_offer(
                    raw_name=p["name"],
                    url=p["url"],
                    price=p.get("price_amount"),
                    currency=currency,
                    availability=Availability.IN_STOCK if in_stock else Availability.OUT_OF_STOCK,
                    sku=p.get("reference") or None,
                    brand=p.get("manufacturer_name") or None,
                    category=p.get("category_name") or None,
                    image=((cover.get("large") or cover.get("medium") or {}).get("url")),
                    description=p.get("description_short") or p.get("description") or None,
                    links=extract_doc_links(
                        (p.get("description") or "") + (p.get("description_short") or ""), p["url"]
                    ),
                )
                if offer:
                    yield offer
            page += 1
