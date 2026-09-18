"""Orchestration: scrape -> dedupe -> enrich -> validate -> persist (Parquet) -> export -> report.

Every stage is fail-safe at the store level; the run only fails (exit 1) when *no* store
produced offers."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from . import SCHEMA_VERSION, __version__
from .config import settings
from .enrich import Enricher, EnrichReport
from .http import Fetcher
from .models import (
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
from .scrapers import STORES, Store, build_scraper
from .storage import PARQUET_FORMAT, ParquetStore, build_series, reference_prices

log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    stores: list[str] | None = None
    max_pages: int | None = None
    ai: bool = True
    ai_limit: int | None = None
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

    # 5. persist canonical history + catalog (Parquet)
    store.write_run(run)
    store.write_catalog(catalog)

    # 6. derived exports rebuilt from the full history (merges/redirects apply retroactively)
    series_rows, stats_rows = build_series(store.read_offers(), catalog)
    store.write_series(series_rows)
    store.write_stats(stats_rows)

    # 7. manifest
    _write_manifest(store, run, catalog, new_products)

    diag.duration_s = round(time.monotonic() - t0, 1)
    _write_diagnostics(diag_dir, diag)
    return diag, 0


def rebuild_exports(data_dir: Path) -> tuple[int, int]:
    """Recompute series/stats from history without scraping (after manual catalog fixes)."""
    store = ParquetStore(data_dir)
    catalog = store.read_catalog()
    series_rows, stats_rows = build_series(store.read_offers(), catalog)
    store.write_series(series_rows)
    store.write_stats(stats_rows)
    return len(series_rows), len(stats_rows)


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
        files=dict(sorted(store.written.items())),
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
