"""EasyTest (custom Laravel shop) – listing cards expose `data-et-*` attributes.

The shop moved from `www.easytest.com.eg` to `easytestgroup.com` in 2026 and its paginated
catalog lives at `/<lang>/store?page=N` (`params.listing` overrides the path). Product URLs
kept the `/<lang>/product/<id>/<slug>` shape, so listing keys – and price history – carry over.
Trailing pages repeat a handful of featured cards; the scraper stops once a page adds nothing."""

from __future__ import annotations

from collections.abc import AsyncIterator

from selectolax.parser import HTMLParser

from ..models import Availability, RawOffer
from .base import BaseScraper


class EasyTestScraper(BaseScraper):
    platform = "easytest"

    default_listing = "store"

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        listing = str(self.store.params.get("listing") or self.default_listing).strip("/")
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            html = await self.fetch.text(f"{self.base}/{listing}", params={"page": page})
            tree = HTMLParser(html)
            cards = tree.css("ul.quick-action-buttons[data-et-id]")
            if not cards:
                break
            self.pages += 1
            new = 0
            for card in cards:
                a = card.attributes
                pid = a.get("data-et-id", "")
                if pid in seen:
                    continue
                seen.add(pid)
                new += 1
                # title is "<arabic> # <english>" – keep the English part when present
                title = (a.get("data-et-title") or "").split("#")[-1].strip()
                if not title:
                    continue
                link = card.parent.parent.css_first("a.image-wrap") if card.parent else None
                href = link.attributes.get("href") if link else None
                url = (href or f"{self.base}/product/{pid}").replace("/ar/", "/en/")
                stock = int(a.get("data-et-stock") or 0)
                offer = self.make_offer(
                    raw_name=title,
                    url=url,
                    price=a.get("data-et-price"),
                    currency=self.currency,
                    availability=Availability.IN_STOCK if stock > 0 else Availability.OUT_OF_STOCK,
                    image=a.get("data-et-image") or None,
                    extra={"stock": stock} if stock else {},
                )
                if offer:
                    yield offer
            if new == 0:
                break
