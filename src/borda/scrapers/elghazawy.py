"""ElGhazawy custom Laravel shop, deliberately limited to configured sub-categories.

Category pages render their entire result set in one HTML response and implement pagination by
hiding cards in the browser. Never crawl the broad catalog: `params.categories` is required and
contains only the category slugs approved for this comparison catalog.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from ..models import Availability, RawOffer
from .base import BaseScraper

_PRICE = re.compile(r"([\d,]+(?:\.\d{1,2})?)\s*EGP", re.I)
_PRODUCT_ID = re.compile(r"/product/([^/]+)/")


class ElGhazawyScraper(BaseScraper):
    platform = "elghazawy"

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        categories = self.store.params.get("categories")
        if not isinstance(categories, list) or not categories:
            raise ValueError(f"{self.store.slug}: params.categories must be a non-empty list")

        seen: set[str] = set()
        for category in categories[: self.max_pages]:
            slug = str(category).strip().strip("/")
            if not slug or "/" in slug:
                raise ValueError(f"{self.store.slug}: invalid category slug {category!r}")
            html = await self.fetch.text(f"{self.base}/en/sub-category/{slug}")
            self.pages += 1
            tree = HTMLParser(html)
            for card in tree.css(".product_card"):
                link = card.css_first('a.flash-sale-card-wrap-anchor[href*="/product/"]')
                if link is None:
                    continue
                url = link.attributes.get("href") or ""
                path = urlparse(url).path
                product_id = match.group(1) if (match := _PRODUCT_ID.search(path)) else path
                if not product_id or product_id in seen:
                    continue
                seen.add(product_id)

                name_node = link.css_first(".pro-frame > p")
                price_node = link.css_first(".pro-frame > h4")
                price_text = price_node.text(strip=True) if price_node else ""
                price_match = _PRICE.search(price_text)
                image_node = link.css_first("img[data-src]") or link.css_first("img[src]")
                label = card.css_first(".sale-sold-label")
                label_text = label.text(strip=True).lower() if label else ""
                availability = (
                    Availability.OUT_OF_STOCK
                    if "sold" in label_text or "out of stock" in label_text
                    else Availability.UNKNOWN
                )
                offer = self.make_offer(
                    raw_name=name_node.text(strip=True) if name_node else link.text(strip=True),
                    url=url,
                    price=price_match.group(1).replace(",", "") if price_match else None,
                    currency=self.currency,
                    availability=availability,
                    category=slug.replace("-", " ").title(),
                    store_tags=[slug],
                    image=(
                        image_node.attributes.get("data-src") or image_node.attributes.get("src")
                        if image_node
                        else None
                    ),
                    extra={"elghazawy_id": product_id, "source_category": slug},
                )
                if offer:
                    yield offer
