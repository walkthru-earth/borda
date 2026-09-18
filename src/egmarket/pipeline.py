"""Orchestration: scrape -> dedupe -> enrich -> validate -> persist (Parquet) -> export -> report.

Every stage is fail-safe at the store level; the run only fails (exit 1) when *no* store
produced offers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from . import SCHEMA_VERSION, __version__
from .config import settings
from .enrich import Enricher, EnrichReport
from .enrich.embed import embed_catalog
from .http import Fetcher
from .models import (
    FileInfo,
    Manifest,
    OfferRecord,
    RawOffer,
    Run,
    RunSummary,
    ScrapeStatus,
    StoreReport,
    utcnow,
)
from .normalize import Catalog, flag_offers
from .normalize.categories import assign_group
from .scrapers import STORES, Store, build_scraper
from .storage import PARQUET_FORMAT, ParquetStore, build_series, reference_prices

log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    stores: list[str] | None = None
    max_pages: int | None = None
    ai: bool = True
    ai_limit: int | None = None
    embeddings: bool = True
    dry_run: bool = False
    run_id: str | None = None
    ts: datetime | None = None  # override observation time (tests / backfills)
    store_concurrency: int = 4
    data_dir: Path | None = None
    diagnostics_dir: Path | None = None
    cache_dir: Path | None = None  # http page cache (default settings.cache)
    extra_stores: list[Store] = field(default_factory=list)


class Diagnostics(BaseModel):
    run_id: str
    ts: str
    duration_s: float
    stores: list[StoreReport]
    offers_scraped: int
    offers_kept: int
    products_total: int
    products_new: int
    flags: dict[str, int] = Field(default_factory=dict)
    enrichment: dict | None = None
    embeddings: dict | None = None
    errors: list[str] = Field(default_factory=list)


async def scrape_all(
    stores: list[Store], opts: RunOptions
) -> tuple[list[RawOffer], list[StoreReport]]:
    sem = asyncio.Semaphore(opts.store_concurrency)
    max_pages = opts.max_pages or settings.max_pages_per_store
    async with Fetcher(cache_dir=opts.cache_dir) as fetcher:

        async def one(store: Store) -> tuple[list[RawOffer], StoreReport]:
            async with sem:
                if not store.enabled and not (opts.stores and store.slug in opts.stores):
                    return [], StoreReport(
                        seller=store.slug, status=ScrapeStatus.SKIPPED, error=store.note
                    )
                try:
                    scraper = build_scraper(store, fetcher, max_pages=max_pages)
                except ValueError as exc:
                    return [], StoreReport(
                        seller=store.slug, status=ScrapeStatus.FAILED, error=str(exc)
                    )
                log.info("scraping %s", store.slug)
                offers, report = await scraper.run()
                log.info(
                    "%s: %s, %d offers, %d pages",
                    store.slug,
                    report.status,
                    report.offers,
                    report.pages,
                )
                return offers, report

        results = await asyncio.gather(*(one(s) for s in stores))
        log.info("http requests=%d cache_hits=%d", fetcher.requests, fetcher.cache_hits)
    offers = [o for chunk, _ in results for o in chunk]
    return offers, [r for _, r in results]


def dedupe_offers(offers: list[RawOffer], catalog: Catalog) -> tuple[list[OfferRecord], int]:
    """Stable order (seller, url) so ids are deterministic; one record per listing."""
    new_products = 0
    seen: set[str] = set()
    out: list[OfferRecord] = []
    for o in sorted(offers, key=lambda o: (o.seller, str(o.url))):
        if o.listing_key in seen:
            continue
        seen.add(o.listing_key)
        pid, is_new = catalog.resolve(o, fuzzy_threshold=settings.fuzzy_threshold)
        new_products += is_new
        out.append(
            OfferRecord(
                product_id=pid,
                seller=o.seller,
                raw_name=o.raw_name,
                url=str(o.url),
                price=o.price,
                currency=o.currency,
                availability=o.availability,
                sku=o.sku,
                category=o.category,
                image=str(o.image) if o.image else None,
            )
        )
    return out, new_products


async def run_pipeline(opts: RunOptions) -> tuple[Diagnostics, int]:
    t0 = time.monotonic()
    ts = opts.ts or utcnow()
    run_id = opts.run_id or ts.strftime("%Y%m%dT%H%M%SZ")
    data_dir = opts.data_dir or settings.data
    diag_dir = opts.diagnostics_dir or settings.diagnostics
    store = ParquetStore(data_dir)
    errors: list[str] = []

    stores = [*STORES, *opts.extra_stores]
    if opts.stores:
        stores = [s for s in stores if s.slug in opts.stores]

    # 1. scrape (fail-safe per store)
    raw_offers, reports = await scrape_all(stores, opts)
    ok_stores = [r for r in reports if r.status in (ScrapeStatus.OK, ScrapeStatus.PARTIAL)]
    errors += [f"{r.seller}: {r.error}" for r in reports if r.status == ScrapeStatus.FAILED]

    # 2. dedupe against the existing catalog
    catalog = store.read_catalog()
    records, new_products = dedupe_offers(raw_offers, catalog)

    # 3. enrich (best effort) then follow merges
    enrich_report: EnrichReport | None = None
    if opts.ai and settings.ai_enabled and records and not opts.dry_run:
        enrich_report = await Enricher(store).enrich(catalog, limit=opts.ai_limit)
        if enrich_report.error:
            errors.append(f"enrichment: {enrich_report.error}")
        for r in records:
            r.product_id = catalog.resolve_id(r.product_id)

    # 4. validate / flag against recent history
    history = store.read_offers()
    flags = flag_offers(
        records,
        reference_prices=reference_prices(history, catalog),
        factor=settings.outlier_factor,
    )

    run = Run(
        run_id=run_id,
        ts=ts,
        schema_version=SCHEMA_VERSION,
        pipeline_version=__version__,
        stores=reports,
        offers=records,
    )
    diag = Diagnostics(
        run_id=run_id,
        ts=ts.isoformat(),
        duration_s=0,
        stores=reports,
        offers_scraped=len(raw_offers),
        offers_kept=len(records),
        products_total=len(catalog.products),
        products_new=new_products - (enrich_report.merged if enrich_report else 0),
        flags=flags,
        enrichment=(
            {k: v for k, v in enrich_report.__dict__.items() if k != "merges"}
            if enrich_report
            else None
        ),
        errors=errors,
    )

    if not ok_stores:
        diag.errors.append("all stores failed – nothing persisted")
        diag.duration_s = round(time.monotonic() - t0, 1)
        _write_diagnostics(diag_dir, diag, dry_run=opts.dry_run)
        return diag, 1

    if opts.dry_run:
        log.info(
            "dry run: %d offers -> %d products (%d new)",
            len(records),
            len(catalog.products),
            new_products,
        )
        diag.duration_s = round(time.monotonic() - t0, 1)
        return diag, 0

    # 5. embeddings + nearest neighbours (best effort; needs the ONNX model download)
    if opts.embeddings and settings.embeddings_enabled:
        emb = embed_catalog(store, catalog)
        diag.embeddings = emb.__dict__
        if emb.error:
            diag.errors.append(f"embeddings: {emb.error}")

    # 6. persist canonical history + catalog (Parquet)
    store.write_run(run)
    store.write_catalog(catalog)

    # 7. derived exports rebuilt from the full history (merges/redirects apply retroactively)
    series_rows, stats_rows = build_series(store.read_offers(), catalog)
    store.write_series(series_rows)
    store.write_stats(stats_rows)

    # 8. manifest
    _write_manifest(store, run, catalog, new_products)

    diag.duration_s = round(time.monotonic() - t0, 1)
    _write_diagnostics(diag_dir, diag)
    return diag, 0


def rebuild_exports(data_dir: Path, *, embeddings: bool = True) -> tuple[int, int]:
    """Recompute groups, embeddings/neighbours, series and stats from what is on disk –
    no scraping. Use after manual catalog fixes or a storage-format upgrade."""
    store = ParquetStore(data_dir)
    catalog = store.read_catalog()
    for p in catalog.products.values():
        if not p.group:
            p.group = assign_group(name=p.canonical_name, tags=p.tags, store_category=p.category)
    if embeddings and settings.embeddings_enabled:
        emb = embed_catalog(store, catalog)
        if emb.error:
            log.warning("embeddings: %s", emb.error)
    store.write_catalog(catalog)
    series_rows, stats_rows = build_series(store.read_offers(), catalog)
    store.write_series(series_rows)
    store.write_stats(stats_rows)
    _refresh_manifest(store, catalog)
    return len(series_rows), len(stats_rows)


async def reindex(data_dir: Path, *, ai: bool = False, embeddings: bool = True) -> tuple[int, int]:
    """Rebuild the catalog from scratch by replaying every historical offer through the
    current normalisation rules, then re-apply the enrichment cache (by product id) and
    recompute exports. Use after changing dedupe rules / the Arabic glossary."""
    store = ParquetStore(data_dir)
    offers = store.read_offers()
    old = store.read_catalog()
    catalog = Catalog()
    cols = offers.select(
        [
            "run_id",
            "product_id",
            "seller",
            "raw_name",
            "url",
            "price",
            "currency",
            "availability",
            "sku",
            "category",
            "image",
        ]
    ).to_pydict()
    order = sorted(
        range(offers.num_rows), key=lambda i: (cols["run_id"][i], cols["seller"][i], cols["url"][i])
    )
    seen: set[str] = set()
    for i in order:
        key = f"{cols['seller'][i]}:{cols['url'][i]}"
        if key in seen:
            continue  # first observation of a listing decides; later runs re-attach anyway
        seen.add(key)
        try:
            raw = RawOffer(
                seller=cols["seller"][i],
                raw_name=cols["raw_name"][i],
                url=cols["url"][i],
                price=cols["price"][i],
                currency=cols["currency"][i],
                availability=cols["availability"][i],
                sku=cols["sku"][i],
                category=cols["category"][i],
                image=cols["image"][i],
            )
        except Exception as exc:  # noqa: BLE001 - a bad historical row must not stop the reindex
            log.debug("skip %s: %s", key, exc)
            continue
        catalog.resolve(raw, fuzzy_threshold=settings.fuzzy_threshold)
    enricher = Enricher(store)
    for start in old.redirects:  # first hop along the redirect chain that has a cache entry
        cur, hops = start, 0
        while cur in old.redirects and hops < 10:
            cur, hops = old.redirects[cur], hops + 1
            if cur in enricher.cache:
                enricher.key_aliases[start] = cur
                break
    report = await enricher.enrich(catalog, limit=settings.ai_max_items_per_run if ai else 0)
    log.info(
        "reindex: %d products, enrichment cached=%d enriched=%d merged=%d",
        len(catalog.products),
        report.cached,
        report.enriched,
        report.merged,
    )
    store.write_catalog(catalog)
    return rebuild_exports(data_dir, embeddings=embeddings)


def _rebuild_redirects(old: Catalog, new: Catalog) -> dict[str, str]:
    """Redirects for a replayed catalog, computed once ids are final:
    every id that used to exist (product or redirect source) but does not now points to the
    product that currently owns one of its listings. Self-redirects and dangling targets are
    dropped."""
    by_listing = {k: p.id for p in new.products.values() for k in p.listings}
    redirects: dict[str, str] = {}

    def target_for(old_id: str) -> str | None:
        final = old.resolve_id(old_id)
        if final in new.products:
            return final
        op = old.products.get(final)
        if op is None:
            return None
        return next((by_listing[k] for k in op.listings if k in by_listing), None)

    for old_id in [*old.products, *old.redirects]:
        if old_id in new.products:
            continue
        if (t := target_for(old_id)) and t != old_id:
            redirects[old_id] = t
    for k, v in new.redirects.items():  # renames/merges made during this reindex
        t = new.resolve_id(v)
        if k not in new.products and t in new.products and t != k:
            redirects[k] = t
    return redirects


def _refresh_manifest(store: ParquetStore, catalog: Catalog) -> None:
    """Rewrite manifest.json file facts after a rebuild (runs list is preserved)."""
    path = store.data_dir / "manifest.json"
    prev_ts, prev_runs = _previous_manifest(path)
    manifest = Manifest(
        schema_version=SCHEMA_VERSION,
        pipeline_version=__version__,
        parquet_format=PARQUET_FORMAT,
        generated_at=prev_ts or utcnow(),
        products=len(catalog.products),
        offers_total=store.read_offers(columns=["run_id"]).num_rows,
        groups=dict(sorted(Counter(p.group or "other" for p in catalog.products.values()).items())),
        files={k: FileInfo(**v) for k, v in sorted(store.written.items())},
        runs=prev_runs,
    )
    path.write_text(manifest.model_dump_json(indent=1) + "\n")


def _previous_manifest(path: Path) -> tuple[datetime | None, list[RunSummary]]:
    """Read only the stable parts of an existing manifest (tolerates older schemas)."""
    if not path.exists():
        return None, []
    raw = json.loads(path.read_text())
    runs = [RunSummary.model_validate(r) for r in raw.get("runs", [])]
    ts = datetime.fromisoformat(raw["generated_at"]) if raw.get("generated_at") else None
    return ts, runs


def _write_manifest(store: ParquetStore, run: Run, catalog: Catalog, new_products: int) -> None:
    path = store.data_dir / "manifest.json"
    prev: list[RunSummary] = []
    if path.exists():
        prev = Manifest.model_validate_json(path.read_bytes()).runs
    prev = [r for r in prev if r.run_id != run.run_id]
    summary = RunSummary(
        run_id=run.run_id,
        ts=run.ts,
        digest=run.digest,
        products=len(catalog.products),
        offers=len(run.offers),
        new_products=new_products,
        stores_ok=sum(r.status in (ScrapeStatus.OK, ScrapeStatus.PARTIAL) for r in run.stores),
        stores_failed=sum(r.status == ScrapeStatus.FAILED for r in run.stores),
    )
    manifest = Manifest(
        schema_version=SCHEMA_VERSION,
        pipeline_version=__version__,
        parquet_format=PARQUET_FORMAT,
        generated_at=run.ts,
        products=len(catalog.products),
        offers_total=store.read_offers(columns=["run_id"]).num_rows,
        groups=dict(sorted(Counter(p.group or "other" for p in catalog.products.values()).items())),
        files={k: FileInfo(**v) for k, v in sorted(store.written.items())},
        runs=[*prev, summary][-240:],
    )
    path.write_text(manifest.model_dump_json(indent=1) + "\n")


def _write_diagnostics(diag_dir: Path, diag: Diagnostics, *, dry_run: bool = False) -> None:
    if dry_run:
        return
    text = diag.model_dump_json(indent=1, exclude_none=True) + "\n"
    (diag_dir / "runs").mkdir(parents=True, exist_ok=True)
    (diag_dir / "runs" / f"{diag.run_id}.json").write_text(text)
    (diag_dir / "latest.json").write_text(text)
