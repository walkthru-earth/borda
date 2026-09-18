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

from rapidfuzz import fuzz

from ..models import Product, RawOffer
from . import names
from .tags import derive_tags


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

    @classmethod
    def from_products(
        cls, products: list[Product], redirects: dict[str, str] | None = None
    ) -> Catalog:
        cat = cls(redirects=dict(redirects or {}))
        for p in products:
            cat._register(p)
        return cat

    def resolve_id(self, pid: str) -> str:
        """Follow merge redirects so historic ids map to the surviving product."""
        seen = set()
        while pid in self.redirects and pid not in seen:
            seen.add(pid)
            pid = self.redirects[pid]
        return pid

    def _register(self, p: Product) -> None:
        self.products[p.id] = p
        for rn in [p.canonical_name, *p.raw_names]:
            self.aliases.setdefault(names.clean(rn), p.id)
            self._blocks[names.block_key(rn)].add(p.id)
        self._keys[p.id] = names.match_key(p.canonical_name)

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
        best, best_score = None, 0.0
        for pid in self._blocks.get(names.block_key(raw), ()):
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
            brand=offer.brand,
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
        p.listings = {**p.listings, offer.listing_key: str(offer.url)}
        if not p.brand and offer.brand:
            p.brand = offer.brand
        if not p.category and offer.category:
            p.category = offer.category
        if not p.image and offer.image:
            p.image = offer.image
        if not p.enriched:
            p.tags = derive_tags(p, offer)

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
        keep.description = keep.description or drop.description
        keep.image = keep.image or drop.image
        keep.brand = keep.brand or drop.brand
        keep.category = keep.category or drop.category
        keep.enriched = keep.enriched or drop.enriched
        keep.extra_metadata = {
            **keep.extra_metadata,
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
