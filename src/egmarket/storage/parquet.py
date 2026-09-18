"""Parquet store – the canonical on-disk format, readable directly by the SvelteKit
frontend through hyparquet (snappy + Parquet format 2.x, flat/list columns only).

Layout under `data/`:
  catalog.parquet                              products (rewritten every run)
  stats.parquet                                per-product price stats (rewritten)
  offers/year=YYYY/month=MM/<run_id>.parquet   history rows, append-only, hive partitioned
  store_runs/year=YYYY/month=MM/<run_id>.parquet   scraper health per store per run
  series/bucket=xx/points.parquet              chart-ready points, bucket = id[:2] (rebuilt)
  enrichment.parquet                           LLM cache
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .. import SCHEMA_VERSION
from ..models import Enrichment, OfferRecord, Product, Run, StoreReport
from ..normalize import Catalog

PARQUET_FORMAT = "2.6"  # latest Parquet format spec version pyarrow can emit
WRITE_KW: dict[str, Any] = {
    "version": PARQUET_FORMAT,
    "compression": "snappy",  # hyparquet decodes snappy without extra packages
    "use_dictionary": True,
    "write_statistics": True,
    "coerce_timestamps": "ms",
    "allow_truncated_timestamps": True,
}

TS = pa.timestamp("ms", tz="UTC")
STR_LIST = pa.list_(pa.string())

OFFERS_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("ts", TS),
        ("product_id", pa.string()),
        ("seller", pa.string()),
        ("raw_name", pa.string()),
        ("url", pa.string()),
        ("price", pa.float64()),
        ("currency", pa.string()),
        ("availability", pa.string()),
        ("sku", pa.string()),
        ("category", pa.string()),
        ("image", pa.string()),
        ("flags", STR_LIST),
    ],
    metadata={"schema_version": str(SCHEMA_VERSION)},
)
PARTITION_SCHEMA = pa.schema([("year", pa.int16()), ("month", pa.int8())])
HIVE_PARTITIONING = ds.partitioning(PARTITION_SCHEMA, flavor="hive")
OFFERS_DATASET_SCHEMA = pa.schema([*OFFERS_SCHEMA, *PARTITION_SCHEMA])  # as read back
STORE_RUNS_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("ts", TS),
        ("seller", pa.string()),
        ("status", pa.string()),
        ("offers", pa.int32()),
        ("pages", pa.int32()),
        ("duration_s", pa.float32()),
        ("error", pa.string()),
    ]
)
CATALOG_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("canonical_name", pa.string()),
        ("raw_names", STR_LIST),
        ("tags", STR_LIST),
        ("description", pa.string()),
        ("category", pa.string()),
        ("brand", pa.string()),
        ("image", pa.string()),
        ("sellers", STR_LIST),
        ("listings", pa.string()),  # JSON object: listing_key -> url
        ("enriched", pa.bool_()),
        ("extra_metadata", pa.string()),  # JSON object
    ],
    metadata={"schema_version": str(SCHEMA_VERSION)},
)
SERIES_SCHEMA = pa.schema(
    [
        ("product_id", pa.string()),
        ("ts", TS),
        ("seller", pa.string()),
        ("price", pa.float64()),
        ("currency", pa.string()),
        ("availability", pa.string()),
        ("url", pa.string()),
    ]
)
STATS_SCHEMA = pa.schema(
    [
        ("product_id", pa.string()),
        ("canonical_name", pa.string()),
        ("currency", pa.string()),
        ("min", pa.float64()),
        ("max", pa.float64()),
        ("median", pa.float64()),
        ("latest_min", pa.float64()),
        ("latest_ts", TS),
        ("in_stock_sellers", pa.int32()),
        ("observations", pa.int32()),
        ("sellers", STR_LIST),
        ("tags", STR_LIST),
        ("image", pa.string()),
    ]
)
ENRICHMENT_SCHEMA = pa.schema(
    [
        ("key", pa.string()),
        ("canonical_name", pa.string()),
        ("description", pa.string()),
        ("tags", STR_LIST),
        ("brand", pa.string()),
        ("model", pa.string()),
        ("ts", TS),
    ]
)


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def write_table(path: Path, table: pa.Table) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, **WRITE_KW)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def partition_dir(root: Path, ts: datetime) -> Path:
    return root / f"year={ts:%Y}" / f"month={ts:%m}"


class ParquetStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.offers_dir = data_dir / "offers"
        self.store_runs_dir = data_dir / "store_runs"
        self.series_dir = data_dir / "series"
        self.catalog_path = data_dir / "catalog.parquet"
        self.stats_path = data_dir / "stats.parquet"
        self.enrichment_path = data_dir / "enrichment.parquet"
        self.written: dict[str, str] = {}  # relative path -> sha256 (feeds the manifest)

    def _write(self, path: Path, table: pa.Table) -> str:
        sha = write_table(path, table)
        self.written[str(path.relative_to(self.data_dir))] = sha
        return sha

    # ------------------------------------------------------------------ history (append)
    def write_run(self, run: Run) -> None:
        rows = [
            {
                "run_id": run.run_id,
                "ts": run.ts,
                "product_id": o.product_id,
                "seller": o.seller,
                "raw_name": o.raw_name,
                "url": o.url,
                "price": _f(o.price),
                "currency": o.currency,
                "availability": o.availability.value,
                "sku": o.sku,
                "category": o.category,
                "image": o.image,
                "flags": o.flags,
            }
            for o in sorted(run.offers, key=lambda o: (o.product_id, o.seller, o.url))
        ]
        self._write(
            partition_dir(self.offers_dir, run.ts) / f"{run.run_id}.parquet",
            pa.Table.from_pylist(rows, schema=OFFERS_SCHEMA),
        )
        self._write(
            partition_dir(self.store_runs_dir, run.ts) / f"{run.run_id}.parquet",
            store_reports_table(run.run_id, run.ts, run.stores),
        )

    def read_offers(self, columns: list[str] | None = None) -> pa.Table:
        """Whole history (all partitions). Empty table when nothing has been written."""
        if not self.offers_dir.exists():
            return OFFERS_SCHEMA.empty_table()
        dataset = ds.dataset(
            self.offers_dir, schema=OFFERS_DATASET_SCHEMA, partitioning=HIVE_PARTITIONING
        )
        return dataset.to_table(columns=columns)

    def run_ids(self) -> list[str]:
        return sorted(p.stem for p in self.offers_dir.glob("year=*/month=*/*.parquet"))

    # ------------------------------------------------------------------ catalog
    def read_catalog(self) -> Catalog:
        if not self.catalog_path.exists():
            return Catalog()
        table = pq.read_table(self.catalog_path)
        products: list[Product] = []
        redirects: dict[str, str] = {}
        meta = table.schema.metadata or {}
        if b"redirects" in meta:
            redirects = json.loads(meta[b"redirects"])
        for row in table.to_pylist():
            row["listings"] = json.loads(row.pop("listings") or "{}")
            row["extra_metadata"] = json.loads(row.pop("extra_metadata") or "{}")
            products.append(Product.model_validate(row))
        return Catalog.from_products(products, redirects)

    def write_catalog(self, catalog: Catalog) -> str:
        rows = [
            {
                **p.model_dump(mode="json", exclude={"listings", "extra_metadata"}),
                "listings": json.dumps(p.listings, ensure_ascii=False, sort_keys=True),
                "extra_metadata": json.dumps(p.extra_metadata, ensure_ascii=False, sort_keys=True),
            }
            for p in catalog.sorted_products()
        ]
        schema = CATALOG_SCHEMA.with_metadata(
            {
                **{k.decode(): v.decode() for k, v in (CATALOG_SCHEMA.metadata or {}).items()},
                "redirects": json.dumps(dict(sorted(catalog.redirects.items()))),
            }
        )
        return self._write(self.catalog_path, pa.Table.from_pylist(rows, schema=schema))

    # ------------------------------------------------------------------ derived exports
    def write_series(self, points: Iterable[dict[str, Any]]) -> int:
        """`points` rows follow SERIES_SCHEMA; files are bucketed by product_id[:2]."""
        if self.series_dir.exists():
            shutil.rmtree(self.series_dir)  # fully rebuilt -> never stale
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in points:
            buckets.setdefault(row["product_id"][:2], []).append(row)
        for bucket, rows in sorted(buckets.items()):
            rows.sort(key=lambda r: (r["product_id"], r["ts"], r["seller"]))
            self._write(
                self.series_dir / f"bucket={bucket}" / "points.parquet",
                pa.Table.from_pylist(rows, schema=SERIES_SCHEMA),
            )
        return len(buckets)

    def write_stats(self, rows: list[dict[str, Any]]) -> str:
        rows.sort(key=lambda r: r["product_id"])
        return self._write(self.stats_path, pa.Table.from_pylist(rows, schema=STATS_SCHEMA))

    # ------------------------------------------------------------------ enrichment cache
    def read_enrichment(self) -> dict[str, Enrichment]:
        if not self.enrichment_path.exists():
            return {}
        out = {}
        for row in pq.read_table(self.enrichment_path).to_pylist():
            row.pop("model", None)
            row.pop("ts", None)
            out[row["key"]] = Enrichment.model_validate(row)
        return out

    def write_enrichment(self, cache: dict[str, Enrichment], model: str, ts: datetime) -> str:
        rows = [
            {**e.model_dump(mode="json"), "key": key, "model": model, "ts": ts}
            for key, e in sorted(cache.items())  # dict key wins: products may be re-keyed
        ]
        return self._write(
            self.enrichment_path, pa.Table.from_pylist(rows, schema=ENRICHMENT_SCHEMA)
        )


def store_reports_table(run_id: str, ts: datetime, reports: list[StoreReport]) -> pa.Table:
    rows = [
        {
            "run_id": run_id,
            "ts": ts,
            "seller": r.seller,
            "status": r.status.value,
            "offers": r.offers,
            "pages": r.pages,
            "duration_s": r.duration_s,
            "error": r.error,
        }
        for r in sorted(reports, key=lambda r: r.seller)
    ]
    return pa.Table.from_pylist(rows, schema=STORE_RUNS_SCHEMA)


def offer_record_rows(records: list[OfferRecord]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in records]
