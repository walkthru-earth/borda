"""Odoo eCommerce (`/shop/page/N?ppg=200`) – product cards carry schema.org microdata.

Odoo 17's website_sale accepts `ppg` (products per page) from the query string, so a 3k
catalogue is ~16 pages instead of ~100. There is no anonymous JSON API for the catalogue
(JSON-RPC needs a login), so HTML it stays."""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..models import Availability, RawOffer
from .base import BaseScraper


class OdooScraper(BaseScraper):
    platform = "odoo"

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        seen: set[str] = set()
        ppg = int(self.store.params.get("ppg", 200))
        for page in range(1, self.max_pages + 1):
            html = await self.fetch.text(f"{self.base}/shop/page/{page}", params={"ppg": ppg})
            tree = HTMLParser(html)
            cards = tree.css(".oe_product")  # <td> or <div> depending on theme
            if not cards:
                break
            self.pages += 1
            new = 0
            for card in cards:
                link = card.css_first("a[itemprop=name]") or card.css_first("a[itemprop=url]")
                if link is None:
                    continue
                href = link.attributes.get("href") or ""
                url = urljoin(self.base + "/", href.split("?")[0])
                if url in seen:
                    continue
                seen.add(url)
                new += 1
                name = link.attributes.get("content") or link.text(strip=True)
                price_node = card.css_first("[itemprop=price]")
                price: str | None = price_node.attributes.get("content") if price_node else None
                if not price and (val := card.css_first(".oe_currency_value")) is not None:
                    price = val.text(strip=True)
                cur_node = card.css_first("[itemprop=priceCurrency]")
                ribbon = card.css_first(".o_ribbon")
                ribbon_txt = ribbon.text(strip=True).lower() if ribbon else ""
                availability = (
                    Availability.OUT_OF_STOCK
                    if "out of stock" in ribbon_txt or "sold out" in ribbon_txt
                    else Availability.IN_STOCK
                )
                img = card.css_first("img[itemprop=image]")
                offer = self.make_offer(
                    raw_name=name,
                    url=url,
                    price=price,
                    currency=(cur_node.attributes.get("content") if cur_node else None) or "EGP",
                    availability=availability,
                    image=urljoin(self.base, img.attributes["src"])
                    if img and img.attributes.get("src")
                    else None,
                )
                if offer:
                    yield offer
            if new == 0:  # Odoo repeats the last page instead of 404-ing
                break
