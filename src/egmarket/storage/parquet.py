"""Parquet store – the canonical on-disk format, readable directly by the SvelteKit
frontend through hyparquet + hyparquet-compressors (zstd, Parquet format 2.x).

Layout under `data/`:
  catalog.parquet                              products, sorted by id (rewritten every run)
  stats.parquet                                per-product price stats, sorted by product_id
  series.parquet                               chart points sorted by (product_id, ts)
  embeddings.parquet                           int8 sentence embeddings, sorted by product_id
  redirects.parquet                            merged/renamed id -> surviving id
  offers/year=YYYY/month=MM/<run_id>.parquet   history rows, append-only, hive partitioned
  store_runs/year=YYYY/month=MM/<run_id>.parquet   scraper health per store per run
  enrichment.parquet                           LLM cache

Reader-side pruning (hyparquet): every derived file declares `sorting_columns`, carries
column statistics + a page index, uses small row groups and a split-block Bloom filter on
the key column. A `filter: {product_id: {$eq: id}}` therefore touches the footer, the Bloom
filter and one or two row groups – a few KB of range requests instead of the whole file.
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
    "compression": "zstd",  # ~40% smaller than snappy; browser decodes via hyparquet-compressors
    "compression_level": 9,
    "use_dictionary": True,
    "write_statistics": True,
    "write_page_index": True,  # column index + offset index -> page-level pruning
    "data_page_version": "2.0",
    "coerce_timestamps": "ms",
    "allow_truncated_timestamps": True,
    "store_schema": True,  # Arrow schema in metadata -> exact types on read-back
}
EMBEDDING_DIM = 384

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
        ("group", pa.string()),  # top-level taxonomy category (see normalize/categories.py)
        ("similar", STR_LIST),  # nearest neighbours by embedding, best first
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
        ("group", pa.string()),
    ]
)
REDIRECTS_SCHEMA = pa.schema([("old_id", pa.string()), ("new_id", pa.string())])
EMBEDDINGS_SCHEMA = pa.schema(
    [
        ("product_id", pa.string()),
        ("vec_i8", pa.binary(EMBEDDING_DIM)),  # L2-normalised vector * 127, int8 per dim
        ("text_hash", pa.string()),  # sha1[:12] of the embedded text -> recompute only on change
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


def write_table(
    path: Path,
    table: pa.Table,
    *,
    sort_by: list[str] | None = None,
    row_group_size: int | None = None,
    bloom: list[str] | None = None,
) -> str:
    """Write with reader-friendly layout: sorted rows (declared via `sorting_columns`),
    bounded row groups and optional Bloom filters on key columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    kw: dict[str, Any] = dict(WRITE_KW)
    if sort_by:
        table = table.sort_by([(c, "ascending") for c in sort_by])
        kw["sorting_columns"] = [pq.SortingColumn(table.schema.get_field_index(c)) for c in sort_by]
    if row_group_size:
        kw["row_group_size"] = row_group_size
    if bloom and table.num_rows:
        kw["bloom_filter_options"] = {
            c: {"ndv": max(table.num_rows, 1), "fpp": 0.01} for c in bloom
        }
    pq.write_table(table, path, **kw)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_info(path: Path, sha: str) -> dict[str, Any]:
    meta = pq.read_metadata(path)
    return {
        "sha256": sha,
        "bytes": path.stat().st_size,
        "footer": meta.serialized_size + 8,  # + 4-byte length + "PAR1"
        "rows": meta.num_rows,
        "row_groups": meta.num_row_groups,
    }


def partition_dir(root: Path, ts: datetime) -> Path:
    return root / f"year={ts:%Y}" / f"month={ts:%m}"


class ParquetStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.offers_dir = data_dir / "offers"
        self.store_runs_dir = data_dir / "store_runs"
        self.series_dir = data_dir / "series"  # legacy bucket layout, removed on write
        self.series_path = data_dir / "series.parquet"
        self.catalog_path = data_dir / "catalog.parquet"
        self.stats_path = data_dir / "stats.parquet"
        self.embeddings_path = data_dir / "embeddings.parquet"
        self.redirects_path = data_dir / "redirects.parquet"
        self.enrichment_path = data_dir / "enrichment.parquet"
        self.written: dict[str, dict[str, Any]] = {}  # relative path -> file_info (manifest)

    def _write(self, path: Path, table: pa.Table, **layout: Any) -> str:
        sha = write_table(path, table, **layout)
        self.written[str(path.relative_to(self.data_dir))] = file_info(path, sha)
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
            sort_by=["product_id", "seller"],
            row_group_size=8192,
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
        if b"redirects" in meta:  # legacy location (pre redirects.parquet)
            redirects = json.loads(meta[b"redirects"])
        if self.redirects_path.exists():
            t = pq.read_table(self.redirects_path).to_pydict()
            redirects.update(zip(t["old_id"], t["new_id"], strict=True))
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
        self._write(
            self.redirects_path,
            pa.Table.from_pylist(
                [{"old_id": k, "new_id": v} for k, v in sorted(catalog.redirects.items())],
                schema=REDIRECTS_SCHEMA,
            ),
            sort_by=["old_id"],
        )
        return self._write(
            self.catalog_path,
            pa.Table.from_pylist(rows, schema=CATALOG_SCHEMA),
            sort_by=["id"],
            row_group_size=2048,
            bloom=["id"],
        )

    # ------------------------------------------------------------------ derived exports
    def write_series(self, points: Iterable[dict[str, Any]]) -> int:
        """One file sorted by (product_id, ts); small row groups + Bloom filter on
        product_id let hyparquet fetch a single product's history with 1-2 range reads."""
        if self.series_dir.exists():
            shutil.rmtree(self.series_dir)  # legacy bucket layout
        table = pa.Table.from_pylist(list(points), schema=SERIES_SCHEMA)
        self._write(
            self.series_path,
            table,
            sort_by=["product_id", "ts", "seller"],
            row_group_size=4096,
            bloom=["product_id"],
        )
        return table.num_rows

    def write_stats(self, rows: list[dict[str, Any]]) -> str:
        return self._write(
            self.stats_path,
            pa.Table.from_pylist(rows, schema=STATS_SCHEMA),
            sort_by=["product_id"],
            row_group_size=2048,
        )

    # ------------------------------------------------------------------ embeddings
    def read_embeddings(self) -> dict[str, tuple[bytes, str]]:
        """product_id -> (int8 vector bytes, text hash)."""
        if not self.embeddings_path.exists():
            return {}
        t = pq.read_table(self.embeddings_path).to_pydict()
        return {
            pid: (vec, h)
            for pid, vec, h in zip(t["product_id"], t["vec_i8"], t["text_hash"], strict=True)
        }

    def write_embeddings(self, vectors: dict[str, tuple[bytes, str]], model: str) -> str:
        rows = [
            {"product_id": k, "vec_i8": v, "text_hash": h} for k, (v, h) in sorted(vectors.items())
        ]
        schema = EMBEDDINGS_SCHEMA.with_metadata(
            {"model": model, "dim": str(EMBEDDING_DIM), "quant": "int8/127", "pooling": "mean+l2"}
        )
        return self._write(
            self.embeddings_path,
            pa.Table.from_pylist(rows, schema=schema),
            sort_by=["product_id"],
            row_group_size=4096,
        )

    # ------------------------------------------------------------------ enrichment cache
    def read_enrichment(self) -> dict[str, Enrichment]:
        return read_enrichment_file(self.enrichment_path)

    def write_enrichment(self, cache: dict[str, Enrichment], model: str, ts: datetime) -> str:
        rows = [
            {**e.model_dump(mode="json"), "key": key, "model": model, "ts": ts}
            for key, e in sorted(cache.items())  # dict key wins: products may be re-keyed
        ]
        return self._write(
            self.enrichment_path, pa.Table.from_pylist(rows, schema=ENRICHMENT_SCHEMA)
        )


def read_enrichment_file(path: Path) -> dict[str, Enrichment]:
    if not path.exists():
        return {}
    out = {}
    for row in pq.read_table(path).to_pylist():
        row.pop("model", None)
        row.pop("ts", None)
        out[row["key"]] = Enrichment.model_validate(row)
    return out


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
