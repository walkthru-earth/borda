"""Wix Stores.

Primary: the anonymous storefront GraphQL API that Wix's own product-gallery widget uses.
  1. GET  /_api/v2/dynamicmodel                    -> per-app instance tokens
  2. POST /_api/wix-ecommerce-storefront-web/api   (Authorization: <Wix Stores instance>)
     catalog.category(<all-products>).productsWithMetaData(limit: 250, offset)
  ~6 requests for 1.3k products, with price / stock / image / sku.

Fallback: `store-products-sitemap.xml` + per-product JSON-LD (one request per product) when
the API path fails (no Stores app, token rejected, schema change)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator

from ..http import FetchError
from ..models import Availability, RawOffer
from .base import BaseScraper
from .docs import extract_doc_links

log = logging.getLogger(__name__)

WIX_STORES_APP_ID = "1380b703-ce81-ff05-f115-39571d94dfcd"
ALL_PRODUCTS_CATEGORY = "00000000-000000-000000-000000000001"
PAGE_SIZE = 250

_GQL = """
query($id: String!, $limit: Int, $offset: Int) {
  catalog { category(categoryId: $id) {
    productsWithMetaData(limit: $limit, offset: $offset, onlyVisible: true) {
      totalCount
      list { id name urlPart sku price discountedPrice isInStock description
             inventory { status quantity } brand ribbon productType media { fullUrl } }
    } } }
}"""

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_AVAIL = {
    "instock": Availability.IN_STOCK,
    "in_stock": Availability.IN_STOCK,
    "outofstock": Availability.OUT_OF_STOCK,
    "out_of_stock": Availability.OUT_OF_STOCK,
    "preorder": Availability.PREORDER,
}


def parse_product_ld(html: str) -> dict | None:
    for m in _LD.finditer(html):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for it in d if isinstance(d, list) else [d]:
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
    chunk = 50  # products per "page" for max_pages accounting (fallback path)

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        try:
            async for o in self._iter_api():
                yield o
            return
        except (FetchError, KeyError, TypeError, ValueError) as exc:
            self.warnings.append(f"storefront API unavailable ({exc}); using sitemap fallback")
            log.warning("%s: %s", self.store.slug, self.warnings[-1])
        async for o in self._iter_sitemap():
            yield o

    # ------------------------------------------------------------------ API path
    async def _iter_api(self) -> AsyncIterator[RawOffer]:
        model = await self.fetch.json(f"{self.base}/_api/v2/dynamicmodel", use_cache=False)
        token = model["apps"][WIX_STORES_APP_ID]["instance"]
        product_path = self.store.params.get("product_path", "product-page")
        currency = self.store.params.get("currency", self.currency)
        offset, total = 0, None
        while (total is None or offset < total) and self.pages < self.max_pages:
            data = await self.fetch.post_json(
                f"{self.base}/_api/wix-ecommerce-storefront-web/api",
                {
                    "query": _GQL,
                    "variables": {
                        "id": ALL_PRODUCTS_CATEGORY,
                        "limit": PAGE_SIZE,
                        "offset": offset,
                    },
                },
                headers={"Authorization": token},
            )
            if data.get("errors") or not data.get("data"):
                raise FetchError(f"GraphQL error: {str(data)[:160]}")
            page = data["data"]["catalog"]["category"]["productsWithMetaData"]
            total = int(page["totalCount"])
            self.pages += 1
            items = page["list"] or []
            for p in items:
                status = ((p.get("inventory") or {}).get("status") or "").lower()
                avail = _AVAIL.get(status) or (
                    Availability.IN_STOCK if p.get("isInStock") else Availability.OUT_OF_STOCK
                )
                media = p.get("media") or []
                offer = self.make_offer(
                    raw_name=p["name"],
                    url=f"{self.base}/{product_path}/{p['urlPart']}",
                    price=p.get("discountedPrice") or p.get("price"),
                    currency=currency,
                    availability=avail,
                    sku=p.get("sku") or None,
                    brand=p.get("brand") or None,
                    image=media[0].get("fullUrl") if media else None,
                    description=p.get("description") or None,
                    links=extract_doc_links(p.get("description"), self.base),
                    extra={"ribbon": p["ribbon"]} if p.get("ribbon") else {},
                )
                if offer:
                    yield offer
            if not items:
                break
            offset += len(items)

    # ------------------------------------------------------------------ fallback path
    async def _iter_sitemap(self) -> AsyncIterator[RawOffer]:
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
            brand = ld.get("brand")
            return self.make_offer(
                raw_name=ld.get("name", ""),
                url=offers.get("url") or url,
                price=offers.get("price") or offers.get("lowPrice"),
                currency=offers.get("priceCurrency") or self.currency,
                availability=_AVAIL.get(avail, Availability.UNKNOWN),
                sku=ld.get("sku") or None,
                brand=brand.get("name") if isinstance(brand, dict) else None,
                image=_image(ld.get("image")),
            )

        for start in range(0, len(urls), self.chunk):
            results = await asyncio.gather(*(one(u) for u in urls[start : start + self.chunk]))
            self.pages += 1
            for r in results:
                if r:
                    yield r
