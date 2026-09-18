"""Command line: `egmarket run|stores|search|rebuild|diff|show`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import pyarrow.compute as pc

from .config import settings
from .observability import setup_logging
from .pipeline import RunOptions, rebuild_exports, reindex, run_pipeline
from .scrapers import STORES
from .storage import ParquetStore, build_index, search


def _p() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="egmarket", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="scrape all stores and update history/exports")
    r.add_argument("--stores", nargs="*", help="store slugs (default: all enabled)")
    r.add_argument("--max-pages", type=int, help="cap listing pages per store (dev)")
    r.add_argument("--no-ai", action="store_true", help="skip Pydantic AI enrichment")
    r.add_argument("--no-embeddings", action="store_true", help="skip similarity embeddings")
    r.add_argument("--ai-limit", type=int, help="max products to enrich this run")
    r.add_argument("--dry-run", action="store_true", help="scrape + normalise, write nothing")
    r.add_argument("--run-id")
    r.add_argument(
        "--fresh", action="store_true", help="ignore checkpoints from an earlier attempt this month"
    )
    r.add_argument("--out", type=Path, help="write the run diagnostics JSON here instead of stdout")

    sub.add_parser("stores", help="list registered stores")

    s = sub.add_parser("search", help="search the catalog (official + local names, tags)")
    s.add_argument("query", nargs="+")
    s.add_argument("--limit", type=int, default=15)

    rb = sub.add_parser("rebuild", help="rebuild groups/embeddings/series/stats without scraping")
    rb.add_argument("--no-embeddings", action="store_true")

    ri = sub.add_parser(
        "reindex",
        help="replay all history through current dedupe rules (rule fixes become retroactive)",
    )
    ri.add_argument("--ai", action="store_true", help="also enrich products not in the LLM cache")
    ri.add_argument("--no-embeddings", action="store_true")

    d = sub.add_parser("diff", help="compare the last two runs (new/removed products, price moves)")
    d.add_argument("--top", type=int, default=20)

    sh = sub.add_parser("show", help="print one product with its price history")
    sh.add_argument("product_id")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _p().parse_args(argv)
    setup_logging(args.verbose)

    if args.cmd == "run":
        diag, code = asyncio.run(
            run_pipeline(
                RunOptions(
                    stores=args.stores,
                    max_pages=args.max_pages,
                    ai=not args.no_ai,
                    ai_limit=args.ai_limit,
                    embeddings=not args.no_embeddings,
                    resume=not args.fresh,
                    dry_run=args.dry_run,
                    run_id=args.run_id,
                )
            )
        )
        summary = diag.model_dump_json(indent=1, exclude={"stores": {"__all__": {"warnings"}}})
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(summary + "\n")
        else:
            print(summary)
        return code

    if args.cmd == "stores":
        for s in STORES:
            flag = "on " if s.enabled else "off"
            print(f"{flag} {s.slug:18} {s.platform:12} {s.base_url}  {s.note or ''}")
        return 0

    store = ParquetStore(settings.data)

    if args.cmd == "search":
        idx = build_index(store.read_catalog().sorted_products())
        for pid, name, sellers in search(idx, " ".join(args.query), limit=args.limit):
            print(f"{sellers:>2} sellers  {pid:40} {name}")
        return 0

    if args.cmd == "rebuild":
        points, products = rebuild_exports(settings.data, embeddings=not args.no_embeddings)
        print(f"rebuilt {points} series points for {products} products")
        return 0

    if args.cmd == "reindex":
        points, products = asyncio.run(
            reindex(settings.data, ai=args.ai, embeddings=not args.no_embeddings)
        )
        print(f"reindexed: {products} products, {points} series points")
        return 0

    if args.cmd == "diff":
        return _diff(store, args.top)

    if args.cmd == "show":
        return _show(store, args.product_id)
    return 2


def _diff(store: ParquetStore, top: int) -> int:
    run_ids = store.run_ids()
    if len(run_ids) < 2:
        print("need at least two runs")
        return 1
    prev_id, cur_id = run_ids[-2], run_ids[-1]
    catalog = store.read_catalog()
    offers = store.read_offers(["run_id", "product_id", "price", "flags"])

    def by_product(run_id: str) -> dict[str, float]:
        t = offers.filter(pc.equal(offers["run_id"], run_id)).to_pydict()
        m: dict[str, float] = {}
        for pid, price, flags in zip(t["product_id"], t["price"], t["flags"], strict=True):
            if price is None or flags:
                continue
            pid = catalog.resolve_id(pid)
            m[pid] = min(m.get(pid, price), price)
        return m

    a, b = by_product(prev_id), by_product(cur_id)
    added, removed = sorted(set(b) - set(a)), sorted(set(a) - set(b))
    moves = sorted(
        (
            (pid, a[pid], b[pid], (b[pid] - a[pid]) / a[pid])
            for pid in set(a) & set(b)
            if a[pid] != b[pid]
        ),
        key=lambda x: -abs(x[3]),
    )
    out = {
        "prev": prev_id, "cur": cur_id,
        "products_added": len(added), "products_removed": len(removed),
        "price_changes": len(moves),
        "top_moves": [
            {"id": pid, "from": x, "to": y, "pct": round(pct * 100, 1)} for pid, x, y, pct in moves[:top]
        ],
    }  # fmt: skip
    print(json.dumps(out, indent=1))
    return 0


def _show(store: ParquetStore, product_id: str) -> int:
    catalog = store.read_catalog()
    pid = catalog.resolve_id(product_id)
    p = catalog.products.get(pid)
    if p is None:
        print(f"unknown product {product_id}")
        return 1
    print(p.model_dump_json(indent=1, exclude_none=True))
    if store.series_path.exists():
        import pyarrow.parquet as pq

        t = pq.read_table(store.series_path, filters=[("product_id", "=", pid)])
        for row in t.to_pylist():
            print(
                f"{row['ts']:%Y-%m-%d}  {row['seller']:18} {row['price']!s:>10} {row['currency']}  {row['availability']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
