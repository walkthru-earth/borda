"""Mecha-tronx custom Laravel storefront (public JSON API).

  GET /api/products?per_page=100&page=N   -> {data: [...], meta: {last_page, total}}
  GET /api/products/{slug}                -> detail with `variations`, categories, descriptions

The listing carries price, stock flags, image and category for every visible product, so one
request per 100 products covers the catalog (~36 pages). Variant parents only report the
cheapest child price and an aggregate `inStock: false`, and their children are not listed, so
each parent costs one detail request (~76) and is emitted as one offer per variation. Every
child keeps its own listing path `/product/{variant_id}`, which the store redirects to
`/product/{parent}?variant={id}`. That keeps colour, resistance, wattage or voltage variants
apart in identity and listing ownership.

Availability follows the store's own JSON-LD. `comingSoon` is published as PreOrder, and
`availableOnline: false` items that are `inStock` are published as InStock (shop counter only),
so that flag is kept in `extra` rather than turned into a sold-out state."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from ..http import FetchError
from ..models import Availability, RawOffer, html_to_text
from .base import BaseScraper
from .docs import extract_doc_links

log = logging.getLogger(__name__)

PAGE_SIZE = 100

# CMS template text the store left in place of real copy (seen on arduino-uno-r3).
_PLACEHOLDER = re.compile(
    r"^\s*(full product description goes here|short product summary|"
    r"basic html tags are preserved)\b",
    re.I,
)


def _real_text(value: Any) -> str | None:
    """Seller HTML unless it is empty or template placeholder copy."""
    if not isinstance(value, str) or not value.strip():
        return None
    plain = html_to_text(value)
    if not plain or _PLACEHOLDER.match(plain):
        return None
    return value


def _availability(p: dict[str, Any]) -> Availability:
    if p.get("comingSoon"):
        return Availability.PREORDER
    stock = p.get("inStock")
    if stock is True:
        return Availability.IN_STOCK
    if stock is False:
        return Availability.OUT_OF_STOCK
    return Availability.UNKNOWN


def _flags(p: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if p.get("availableOnline") is False:
        extra["available_online"] = False
    if p.get("comingSoon"):
        extra["coming_soon"] = True
    if p.get("originalPrice") and p.get("price") and p["originalPrice"] > p["price"]:
        extra["on_sale"] = True
    return extra


def _tags(*pairs: tuple[Any, Any]) -> list[str]:
    out: list[str] = []
    for en, ar in pairs:
        for t in (en, ar):
            if isinstance(t, str) and t.strip() and t.strip() not in out:
                out.append(t.strip())
    return out


def _variant_name(parent_name: str, child_name: Any, attributes: dict[str, Any]) -> str:
    """The child's own name when it spells out every attribute value, else parent + values.

    Child names are inconsistent (`strip light -100 meter- 240 led- 220v- COB` omits its
    colour) and parent names can describe one child (`Resistor 8.2 ohm - 5W` for a 4.7k
    variant), so the seller's child name wins only when it carries the distinction itself."""
    values = [str(v).strip() for v in attributes.values() if str(v or "").strip()]
    child = child_name.strip() if isinstance(child_name, str) else ""
    if child and all(re.search(rf"(?<![\w.]){re.escape(v)}(?![\w.])", child, re.I) for v in values):
        return child
    if not values:
        return child or parent_name
    return f"{parent_name} - {' / '.join(values)}"


class MechatronxScraper(BaseScraper):
    platform = "mechatronx"

    async def iter_offers(self) -> AsyncIterator[RawOffer]:
        endpoint = f"{self.base}/api/products"
        last_page: int | None = None
        page = 1
        while page <= self.max_pages and (last_page is None or page <= last_page):
            data = await self.fetch.json(endpoint, params={"per_page": PAGE_SIZE, "page": page})
            if not isinstance(data, dict) or data.get("success") is False:
                raise FetchError(f"unexpected listing payload on page {page}")
            items = data.get("data") or []
            last_page = int((data.get("meta") or {}).get("last_page") or page)
            self.pages += 1
            for p in items:
                if p.get("isVariantParent"):
                    async for o in self._variants(p):
                        yield o
                elif offer := self._listed(p):
                    yield offer
            if not items:
                break
            page += 1

    def _listed(self, p: dict[str, Any]) -> RawOffer | None:
        return self.make_offer(
            raw_name=p.get("name") or "",
            url=f"{self.base}/product/{p['slug']}",
            price=p.get("price"),
            currency=p.get("currency") or self.currency,
            availability=_availability(p),
            category=p.get("category") or None,
            store_tags=_tags((p.get("category"), p.get("categoryAr"))),
            image=p.get("image") or None,
            extra={"mechatronx_id": p.get("id"), **_flags(p)},
        )

    async def _variants(self, p: dict[str, Any]) -> AsyncIterator[RawOffer]:
        try:
            detail = (await self.fetch.json(f"{self.base}/api/products/{p['slug']}"))["data"]
            variations = detail.get("variations") or []
        except (FetchError, KeyError, TypeError) as exc:
            if len(self.warnings) < 50:
                self.warnings.append(f"{p.get('slug')}: variant detail unavailable ({exc})")
            detail, variations = {}, []
        if not variations:
            # Parent-only fallback. Its price is the cheapest child and its `inStock` is an
            # aggregate that reads false even when children are stocked, so stock is unknown.
            if offer := self._listed(p):
                offer.availability = (
                    Availability.PREORDER if p.get("comingSoon") else Availability.UNKNOWN
                )
                offer.extra = {**offer.extra, "variable": True}
                yield offer
            return

        parent_name = detail.get("name") or p.get("name") or ""
        cats = detail.get("categories") or []
        tags = _tags(*((c.get("name"), c.get("nameAr")) for c in cats if isinstance(c, dict)))
        main = detail.get("category") if isinstance(detail.get("category"), dict) else {}
        # `meta.description` is SEO copy that often describes one child ("An 8.2 ohm ...
        # resistor" on a multi-resistance parent), so it is never used as seller text.
        description = _real_text(detail.get("description")) or _real_text(
            detail.get("shortDescription")
        )
        links = extract_doc_links(
            (detail.get("description") or "") + (detail.get("shortDescription") or ""),
            f"{self.base}/product/{p['slug']}",
        )
        for v in variations:
            if not isinstance(v, dict) or v.get("id") is None:
                continue
            attrs = v.get("attributes") if isinstance(v.get("attributes"), dict) else {}
            extra: dict[str, Any] = {
                "mechatronx_id": v.get("id"),
                "parent_id": detail.get("id") or p.get("id"),
                "parent_slug": p.get("slug"),
                **_flags(v),
            }
            if attrs:
                extra["variant"] = attrs
            if v.get("name"):
                extra["variant_name"] = v["name"]
            offer = self.make_offer(
                raw_name=_variant_name(parent_name, v.get("name"), attrs),
                url=f"{self.base}/product/{v['id']}",
                price=v.get("price"),
                currency=v.get("currency") or self.currency,
                availability=_availability(v),
                sku=(str(v.get("sku") or "").strip() or None),
                category=main.get("name") or p.get("category") or None,
                store_tags=tags or _tags((p.get("category"), p.get("categoryAr"))),
                image=v.get("image") or detail.get("image") or p.get("image") or None,
                description=description,
                links=links,
                extra=extra,
            )
            if offer:
                yield offer
