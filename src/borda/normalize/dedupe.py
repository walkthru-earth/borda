"""Deduplication: map every RawOffer to a stable product id.

Resolution order (cheapest and most precise first):
1. alias table   – the cleaned raw name was already assigned to a product in a prior run
2. canonical rule – hand-written regex recognises a well-known product
3. fuzzy match   – rapidfuzz token ratio against products sharing a blocking key,
                   accepted only when numeric signatures (10k vs 100k, 4gb vs 8gb) agree
4. new product   – slug of the cleaned name (hash suffix only on collision)

Provenance is preserved: every raw name and seller listing is recorded on the product."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse

from rapidfuzz import fuzz

from ..config import settings
from ..models import Product, RawOffer
from ..scrapers.docs import rank_datasheet
from . import names
from .brands import brand_tags, infer_brand, normalize_brand
from .categories import assign_group
from .tags import clean_tags, derive_tags


def slugify(s: str, max_len: int = 60) -> str:
    """ASCII slug; Arabic-only names fall back to a hash of the cleaned name."""
    cleaned = names.clean(s)
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    if len(slug) < 3:
        slug = "item-" + hashlib.sha1(cleaned.encode()).hexdigest()[:10]
    return slug[:max_len].rstrip("-")


def _pretty(raw: str) -> str:
    """Fallback canonical name until enrichment: whitespace-normalized, length-capped."""
    return re.sub(r"\s+", " ", raw).strip()[:200].rstrip()


@dataclass
class Catalog:
    """In-memory view of `data/catalog.json` with the indexes dedupe needs."""

    products: dict[str, Product] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)  # clean(raw_name) -> id
    redirects: dict[str, str] = field(default_factory=dict)  # merged-away id -> surviving id
    new_ids: set[str] = field(default_factory=set)  # created in this run (not persisted yet)
    _blocks: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _keys: dict[str, str] = field(default_factory=dict)  # id -> match_key(canonical)
    _listings: dict[str, str] = field(default_factory=dict)
    _hotlink_blocked: frozenset[str] | None = None  # resolved lazily from the active profile
    _image_proxies: dict[str, str] | None = None

    @classmethod
    def from_products(
        cls, products: list[Product], redirects: dict[str, str] | None = None
    ) -> Catalog:
        cat = cls(redirects=dict(redirects or {}))
        for p in products:
            # snapshots written before brand hygiene may still carry the shop as "brand"
            p.brand = normalize_brand(p.brand) or infer_brand(
                p.canonical_name, *p.raw_names, p.category
            )
            if p.brand and not set(brand_tags(p.brand)) <= set(p.tags):
                p.tags = clean_tags({*p.tags, *brand_tags(p.brand)}, brand=brand_tags(p.brand))
            cat._register(p)
        return cat

    def resolve_id(self, pid: str) -> str:
        """Follow merge redirects so historic ids map to the surviving product."""
        seen = set()
        while pid in self.redirects and pid not in seen:
            seen.add(pid)
            pid = self.redirects[pid]
        return pid

    def resolve_listing(self, pid: str, seller: str, url: str) -> str:
        """Listing ownership takes priority after a historical product is split."""
        return self.resolve_id(self._listings.get(f"{seller}:{urlparse(url).path}", pid))

    def _register(self, p: Product) -> None:
        self.products[p.id] = p
        for rn in [p.canonical_name, *p.raw_names]:
            self.aliases.setdefault(names.clean(rn), p.id)
            self._blocks[names.block_key(rn)].add(p.id)
        self._keys[p.id] = names.match_key(p.canonical_name)
        self._listings.update({key: p.id for key in p.listings})

    def unique_id(self, base: str, key: str) -> str:
        if base not in self.products and base not in self.redirects:
            return base
        return f"{base}-{hashlib.sha1(key.encode()).hexdigest()[:4]}"

    # ------------------------------------------------------------------ resolution
    def resolve(self, offer: RawOffer, *, fuzzy_threshold: int) -> tuple[str, bool]:
        """Return (product_id, is_new) and attach the offer to the product."""
        cleaned = names.clean(offer.raw_name)
        pid = self.aliases.get(cleaned)
        is_new = False
        if pid is None and (rule := names.canonical_rule(offer.raw_name)):
            canonical, tags = rule
            pid = self.aliases.get(names.clean(canonical))
            if pid is None:
                pid = self._create(canonical, offer, tags=list(tags), rule=True)
                is_new = True
        if pid is None:
            pid = self._fuzzy(offer.raw_name, fuzzy_threshold)
        if pid is None:
            pid = self._create(_pretty(offer.raw_name), offer)
            is_new = True
        self._attach(pid, offer)
        return pid, is_new

    def _fuzzy(self, raw: str, threshold: int) -> str | None:
        key = names.match_key(raw)
        sig = names.numeric_signature(raw)
        accessories = names.accessory_signature(raw)
        best, best_score = None, 0.0
        for pid in self._blocks.get(names.block_key(raw), ()):
            if names.accessory_signature(self.products[pid].canonical_name) != accessories:
                continue
            if names.numeric_signature(self.products[pid].canonical_name) != sig:
                continue
            score = fuzz.token_sort_ratio(key, self._keys[pid])
            if score >= threshold and score > best_score:
                best, best_score = pid, score
        return best

    def _create(
        self, canonical: str, offer: RawOffer, *, tags: list[str] | None = None, rule: bool = False
    ) -> str:
        key = names.clean(canonical)
        pid = self.unique_id(slugify(canonical), key)
        p = Product(
            id=pid,
            canonical_name=canonical,
            brand=normalize_brand(offer.brand),
            category=offer.category,
            tags=tags or [],
            extra_metadata={"rule": True} if rule else {},
        )
        self._register(p)
        self.new_ids.add(pid)
        return pid

    def rename(self, old_id: str, new_id: str) -> None:
        """Re-key a product (used for products created this run once the official name is
        known, so the permanent id is derived from the official name)."""
        if old_id == new_id or old_id not in self.products or new_id in self.products:
            return
        p = self.products.pop(old_id)
        p.id = new_id
        self.products[new_id] = p
        self._keys[new_id] = self._keys.pop(old_id)
        for alias, pid in self.aliases.items():
            if pid == old_id:
                self.aliases[alias] = new_id
        for ids in self._blocks.values():
            if old_id in ids:
                ids.discard(old_id)
                ids.add(new_id)
        for k, v in self.redirects.items():
            if v == old_id:
                self.redirects[k] = new_id
        self.redirects[old_id] = new_id
        self.new_ids.discard(old_id)
        self.new_ids.add(new_id)

    def _attach(self, pid: str, offer: RawOffer) -> None:
        p = self.products[pid]
        cleaned = names.clean(offer.raw_name)
        if cleaned not in self.aliases:
            self.aliases[cleaned] = pid
            self._blocks[names.block_key(offer.raw_name)].add(pid)
        if offer.raw_name not in p.raw_names:
            p.raw_names = [*p.raw_names, offer.raw_name]
        if offer.seller not in p.sellers:
            p.sellers = [*p.sellers, offer.seller]
        self._choose_image(p, offer)  # before the listing URL below is overwritten
        p.listings = {**p.listings, offer.listing_key: str(offer.url)}
        self._listings[offer.listing_key] = pid
        if not p.category and offer.category:
            p.category = offer.category
        if not p.brand:
            # a maker named in the title beats a store "vendor" field, which is often the
            # shop itself or a placeholder (both are dropped by normalize_brand)
            p.brand = infer_brand(p.canonical_name, *p.raw_names, p.category) or normalize_brand(
                offer.brand
            )
        if offer.links and (
            ds := rank_datasheet(
                [*offer.links, *([str(p.datasheet_url)] if p.datasheet_url else [])]
            )
        ):
            p.datasheet_url = ds
        if not p.enriched:
            p.tags = derive_tags(p, offer)
            p.group = assign_group(name=p.canonical_name, tags=p.tags, store_category=p.category)

    def _choose_image(self, p: Product, offer: RawOffer) -> None:
        """Keep the first image a product gets, except when
        * the listing that supplied it reports a different image – a refreshed picture, or a
          seller that moved domains (EasyTest's `easytest.com.eg` -> `easytestgroup.com`,
          whose old image URLs went dark with the old host), or
        * the image sits on a host that blocks hotlinking (`params.hotlink_blocked` on the
          store) and this seller's does not – a browser can never render the blocked one.
        Stores with `params.image_proxy` get their image URLs rewritten through that CDN
        template first (raw offers keep the original URL)."""
        if not offer.image:
            return
        candidate = self.public_image(offer)
        if not p.image:
            p.image = candidate
            return
        current = str(p.image)
        if candidate == current:
            return
        image_host = self._image_origin_host(current)
        previous_url = p.listings.get(offer.listing_key)
        if previous_url and urlparse(previous_url).netloc == image_host:
            p.image = candidate  # same listing, new picture / new domain
            return
        blocked = self.hotlink_blocked
        if offer.seller in blocked:
            return
        blocked_hosts = {
            urlparse(url).netloc
            for key, url in p.listings.items()
            if key.split(":", 1)[0] in blocked
        }
        if image_host in blocked_hosts:
            p.image = candidate

    def public_image(self, offer: RawOffer) -> str:
        """The image URL a browser can load: the seller's own, or its CDN proxy when the
        profile sets `params.image_proxy` (a template with `{host}` and `{path}`, e.g.
        Jetpack Photon `https://i0.wp.com/{host}{path}?w=800` for Automattic-hosted shops
        whose origin answers hotlinked `<img>` requests with a browser challenge)."""
        template = self.image_proxies.get(offer.seller)
        if not template:
            return str(offer.image)
        parsed = urlparse(str(offer.image))
        return template.format(host=parsed.netloc, path=parsed.path)

    def _image_origin_host(self, image: str) -> str:
        """Host the picture originally came from, seeing through `image_proxy` rewrites
        (templates must start with a literal prefix followed by `{host}`)."""
        for template in self.image_proxies.values():
            prefix = template.split("{", 1)[0]
            if template.startswith(prefix + "{host}") and image.startswith(prefix):
                return image[len(prefix) :].split("/", 1)[0]
        return urlparse(image).netloc

    @property
    def hotlink_blocked(self) -> frozenset[str]:
        """Sellers whose image hosts refuse cross-site `<img>` requests (from the profile)."""
        if self._hotlink_blocked is None:
            self._hotlink_blocked = frozenset(
                s.slug
                for s in settings.country_profile.configured_stores()
                if s.params.get("hotlink_blocked")
            )
        return self._hotlink_blocked

    @property
    def image_proxies(self) -> dict[str, str]:
        """seller -> `params.image_proxy` template (from the profile)."""
        if self._image_proxies is None:
            self._image_proxies = {
                s.slug: str(s.params["image_proxy"])
                for s in settings.country_profile.configured_stores()
                if s.params.get("image_proxy")
            }
        return self._image_proxies

    # ------------------------------------------------------------------ AI unification
    def merge(self, keep_id: str, drop_id: str) -> None:
        """Fold `drop_id` into `keep_id` (used after enrichment yields identical names)."""
        if keep_id == drop_id or drop_id not in self.products:
            return
        keep, drop = self.products[keep_id], self.products.pop(drop_id)
        keep.raw_names = [*keep.raw_names, *drop.raw_names, drop.canonical_name]
        keep.sellers = [*keep.sellers, *drop.sellers]
        keep.tags = [*keep.tags, *drop.tags]
        keep.listings = {**drop.listings, **keep.listings}
        # model-written text beats a seller-text summary, whichever side carries it
        first, second = (drop, keep) if drop.enriched and not keep.enriched else (keep, drop)
        keep.description = first.description or second.description
        keep.canonical_name_ar = first.canonical_name_ar or second.canonical_name_ar
        keep.description_ar = first.description_ar or second.description_ar
        keep.specs = first.specs or second.specs
        keep.specs_ar = first.specs_ar if keep.specs == first.specs else second.specs_ar
        keep.mpn = first.mpn or second.mpn
        keep.image = keep.image or drop.image
        keep.datasheet_url = keep.datasheet_url or drop.datasheet_url
        keep.brand = normalize_brand(keep.brand) or normalize_brand(drop.brand)
        keep.category = keep.category or drop.category
        keep.enriched = keep.enriched or drop.enriched
        keep.group = keep.group if keep.group and keep.group != "other" else drop.group
        source = (
            first.extra_metadata.get("description_source")
            if first.description
            else second.extra_metadata.get("description_source")
        )
        keep.extra_metadata = {
            **{k: v for k, v in keep.extra_metadata.items() if k != "description_source"},
            **({"description_source": source} if source and not keep.enriched else {}),
            "merged": sorted({*keep.extra_metadata.get("merged", []), drop_id}),
        }
        self.redirects[drop_id] = keep_id
        for old, target in list(self.redirects.items()):
            if target == drop_id:
                self.redirects[old] = keep_id
        for alias, pid in list(self.aliases.items()):
            if pid == drop_id:
                self.aliases[alias] = keep_id
        for ids in self._blocks.values():
            if drop_id in ids:
                ids.discard(drop_id)
                ids.add(keep_id)
        self._keys.pop(drop_id, None)
        self._keys[keep_id] = names.match_key(keep.canonical_name)

    def sorted_products(self) -> list[Product]:
        return sorted(self.products.values(), key=lambda p: p.id)
