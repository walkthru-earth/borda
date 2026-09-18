# egmarket – Egyptian microcontroller & hardware price tracker

Monthly GitHub Actions pipeline that scrapes Egyptian electronics stores, unifies every listing
under the **official product name** (keeping the Egyptian/Arabic seller names as searchable
aliases), enriches it with Pydantic AI, and appends to a **Parquet** price history that a
SvelteKit frontend reads directly in the browser with [hyparquet](https://github.com/hyparam/hyparquet).

```
scrape (per-store, fail-safe) ─▶ normalize + dedupe ─▶ Pydantic AI enrich ─▶ validate/flag
        ─▶ data/*.parquet (history, catalog, series, stats) ─▶ diagnostics ─▶ git commit
```

## Stores

| slug | platform | method | status |
|---|---|---|---|
| fut-electronics, devboardsmarket, circuits-elec | Shopify | `/products.json` | on |
| uge-one, microohm, makerselectronics, mostelectronic, fares-pcb | WooCommerce | Store API `/wp-json/wc/store/v1/products` | on |
| ic-hat | PrestaShop | XHR JSON listing | on |
| ram-e-shop | Odoo 17 | HTML cards (schema.org microdata), `?ppg=200` → ~16 pages | on |
| easytest | custom | HTML `data-et-*` attributes | on |
| maamoon | Wix | anonymous storefront GraphQL (`/_api/wix-ecommerce-storefront-web/api`, 250/page); sitemap + JSON-LD fallback | on |
| eshopmas | WooCommerce | – | off: Cloudflare challenge |
| rsdelivers | Next.js | – | off: prices rendered client-side, >100k SKUs |
| amazon-eg | – | – | off: optional per brief, anti-bot |

**Why no official SDKs?** Shopify's Admin/Storefront APIs, Wix's REST API and Odoo's JSON-RPC all
need merchant-issued credentials; libraries such as `ShopifyAPI`, `wix-python-sdk` or `odoorpc`
wrap those. For third-party price tracking the anonymous surfaces above are the only ones
available, and they are JSON where it matters (Shopify `/products.json`, WooCommerce Store API,
PrestaShop XHR, Wix storefront GraphQL) – Odoo and EasyTest are the only HTML parsers left.

Add a store: append a `Store(...)` in `src/egmarket/scrapers/stores.py`. Add a platform:
subclass `BaseScraper` (yield `RawOffer`) and register it in `scrapers/__init__.py`.

## Data layout (`data/`, Parquet v2 · snappy · hive-partitioned)

| path | contents | rewritten? |
|---|---|---|
| `offers/year=YYYY/month=MM/<run_id>.parquet` | every observed listing: run_id, ts, product_id, seller, raw_name, url, price, currency, availability, sku, category, image, flags | append-only (one file per run) |
| `store_runs/year=YYYY/month=MM/<run_id>.parquet` | scraper health per store per run | append-only |
| `catalog.parquet` | products: id, canonical_name, **raw_names** (local names), tags, description, brand, category, **image**, sellers, listings (JSON), enriched, extra_metadata; merge redirects in schema metadata | every run |
| `series/bucket=xx/points.parquet` | chart-ready points, bucketed by `id[:2]` so a product page fetches one small file | rebuilt every run |
| `stats.parquet` | per product min / max / median / latest_min / in_stock_sellers / observations | rebuilt |
| `enrichment.parquet` | Pydantic AI cache (one LLM call per product, ever) | append |
| `manifest.json` | schema/pipeline version, file sha256s, per-run summaries + digests (cheap diffs) | every run |
| `diagnostics/latest.json`, `diagnostics/runs/*.json` | store statuses, errors, flags, enrichment report | every run |

Series/stats are derived from the full offers history, so product merges apply retroactively.
Outliers (`outlier_high/low`, `implausible_price`) stay in history but are excluded from charts.

## Normalization & dedupe (`src/egmarket/normalize/`)

1. `clean()` – lowercase, strip marketing noise, unify units (`5 V`→`5v`, `10 K Ohm`→`10kohm`),
   alias table (`node mcu`→`nodemcu`), **Arabic glossary** (`اردوينو اونو`→`arduino uno`).
2. alias lookup → **canonical rules** (regexes for ~70 well-known products) → **fuzzy**
   (`rapidfuzz` token-sort ≥ 93 inside a blocking key, only if numeric signatures agree:
   `10k`≠`100k`, `4gb`≠`8gb`) → new product (slug id; hash suffix only on collision).
3. Provenance: every raw name, seller and listing URL is kept on the product.
4. Pydantic AI (`enrich/ai.py`) corrects the name to the manufacturer's original, writes a
   one-line description and tags; identical official names are **merged** (redirect recorded);
   products created in the same run get their id re-slugged from the official name.

## Enrichment model

Default: Hetzner Inference (OpenAI-compatible) `hetzner:Qwen/Qwen3.6-35B-A3B-FP8`, tool-call
structured output, thinking disabled (`chat_template_kwargs.enable_thinking=false`), paced to
8 req/min, ≤1500 products/run, batch 30. Any Pydantic AI `provider:model` string works too
(`EGMARKET_AI_MODEL=openai:gpt-5-mini`). Missing credentials → enrichment is skipped, run continues.

## Run locally

```bash
uv sync --all-groups
cp .env.example .env            # add HETZNER_INFERENCE_TOKEN (never committed)
uv run egmarket stores
uv run egmarket run --max-pages 2 --no-ai --dry-run      # quick scraper check
uv run egmarket run                                      # full run → data/ + diagnostics/
uv run egmarket search اردوينو اونو                        # local name → official product
uv run egmarket show arduino-uno-r3
uv run egmarket diff                                     # last two runs
uv run pytest -q
```

Env knobs (all `EGMARKET_*`): see `.env.example` and `src/egmarket/config.py`.

## GitHub Actions

- `monthly-scrape.yml` – cron on the 1st: uv (cached) → pytest → scrape (per-store fail-safe,
  page cache restored for retries) → commit `data/` + `diagnostics/` → upload logs as artifact →
  job summary table; turns red only if a store failed (data is still committed).
  Secret: `HETZNER_INFERENCE_TOKEN`. Manual dispatch accepts `stores`, `max_pages`, `ai`.
- `ci.yml` – ruff + pytest, svelte-check + build.
- `deploy-pages.yml` – rebuilds the SPA with the latest Parquet after each data commit.

Pre-commit (ruff format/lint, uv lock, secrets guard, pytest): `uv tool install pre-commit && pre-commit install`.

## Frontend (`frontend/`, SvelteKit 2.70 · Svelte 5.57 · hyparquet)

Static SPA, no server. `catalog.parquet` + `stats.parquet` are loaded once and indexed
client-side (official name, all local names, tags → prefix search); a product page fetches only
its `series/bucket=xx/points.parquet` and draws an SVG chart per seller with min/median/max.

```bash
cd frontend && pnpm install
ln -s ../../data static/data       # dev: serve the pipeline output
pnpm dev                           # http://localhost:5173
pnpm run sync-data ../data && pnpm run build   # static build in build/
```

`VITE_DATA_BASE` can point at any host that supports HTTP range requests (e.g. raw GitHub).
