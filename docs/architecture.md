# Architecture

[Project home](../README.md) · [Architecture](architecture.md) · [Development](development.md)

The Python package and CLI remain **`egmarket`**. Its monthly GitHub Actions pipeline scrapes
stores, normalizes and deduplicates listings, optionally enriches products with Pydantic AI,
and publishes **Parquet** snapshots. The **SvelteKit static frontend** reads those snapshots
directly in the browser with [hyparquet](https://github.com/hyparam/hyparquet); there is no
Python web server or API required to browse the site. Seller aliases, including Arabic names,
remain searchable alongside the official product name.

```
scrape (per-store, fail-safe) ─▶ normalize + dedupe ─▶ Pydantic AI enrich ─▶ validate/flag
        ─▶ data/*.parquet (history, catalog, series, stats) ─▶ diagnostics ─▶ git commit
```

## Code map

| Area | Responsibility |
|---|---|
| `src/egmarket/scrapers/` | Store adapters yielding validated listing observations |
| `src/egmarket/normalize/` | Name cleaning, identity safeguards, taxonomy and outlier flags |
| `src/egmarket/enrich/` | Optional cached AI descriptions, Arabic translations and embeddings |
| `src/egmarket/storage/` | Parquet schemas, listing-resolved history and search exports |
| `src/egmarket/pipeline.py` | Scrape orchestration, reindexing, manifests and diagnostics |
| `frontend/src/lib/` | Browser data loading, search, filters, localization and components |
| `frontend/src/routes/` | Catalog and product interfaces |
| `frontend/scripts/` | Snapshot synchronization and static SEO generation |

## System boundaries

The browser reads published observations; it does not scrape shops or request paid enrichment.
Python owns product identity and export contracts. The frontend owns presentation, local search,
optional local semantic inference and listing-level filtering. Publish static HTML, the manifest
and Parquet from the same snapshot. Historical offers remain the source for regenerated exports.

Continue with [data contracts](data.md), [scraping and enrichment](pipeline.md),
[frontend behavior](frontend.md), or [development and deployment](development.md).
