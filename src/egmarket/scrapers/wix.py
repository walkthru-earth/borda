"""Wix Stores – product URLs come from `store-products-sitemap.xml`, each product page
embeds a schema.org `Product` JSON-LD block with price and availability.

One request per product, so `params.max_products` bounds the run."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator

from ..models import Availability, RawOffer
from .base import BaseScraper

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)

_AVAIL = {
    "instock": Availability.IN_STOCK,
    "outofstock": Availability.OUT_OF_STOCK,
    "preorder": Availability.PREORDER,
}


def parse_product_ld(html: str) -> dict | None:
    for m in _LD.finditer(html):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        items = d if isinstance(d, list) else [d]
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "Product":
                return it
    return None


def _image(img: object) -> str | None:
    """JSON-LD `image` is a url, a list of urls, or a list of ImageObject dicts."""
    if isinstance(img, list):
        img = img[0] if img else None
    if isinstance(img, dict):
        img = img.get("contentUrl") or img.get("url")
    return img if isinstance(img, str) else None


class WixScraper(BaseScraper):
    platform = "wix"
    concurrency = 4
    chunk = 50  # products per "page" for max_pages accounting

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        sitemap = await self.fetch.text(f"{self.base}/store-products-sitemap.xml", use_cache=False)
        cap = min(int(self.store.params.get("max_products", 2000)), self.max_pages * self.chunk)
        urls = _LOC.findall(sitemap)[:cap]
        self.pages += 1
        sem = asyncio.Semaphore(self.concurrency)

        async def one(url: str) -> RawOffer | None:
            async with sem:
                try:
                    html = await self.fetch.text(url)
                except Exception as exc:  # noqa: BLE001 - single product failure is non-fatal
                    if len(self.warnings) < 50:
                        self.warnings.append(f"{url}: {exc}")
                    return None
            ld = parse_product_ld(html)
            if not ld:
                return None
            offers = ld.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            avail = (offers.get("availability") or "").rsplit("/", 1)[-1].lower()
            return self.make_offer(
                raw_name=ld.get("name", ""),
                url=offers.get("url") or url,
                price=offers.get("price") or offers.get("lowPrice"),
                currency=offers.get("priceCurrency") or "EGP",
                availability=_AVAIL.get(avail, Availability.UNKNOWN),
                sku=ld.get("sku") or None,
                brand=(ld.get("brand") or {}).get("name")
                if isinstance(ld.get("brand"), dict)
                else None,
                image=_image(ld.get("image")),
            )

        for chunk_start in range(0, len(urls), self.chunk):
            results = await asyncio.gather(
                *(one(u) for u in urls[chunk_start : chunk_start + self.chunk])
            )
            self.pages += 1
            for r in results:
                if r:
                    yield r
