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

## Data layout (`data/`, Parquet format 2.6 · snappy · DataPage v2 · page index)

| path | contents | layout |
|---|---|---|
| `offers/year=YYYY/month=MM/<run_id>.parquet` | every observed listing: run_id, ts, product_id, seller, raw_name, url, price, currency, availability, sku, category, image, flags | append-only, hive partitioned, sorted by (product_id, seller) |
| `store_runs/year=YYYY/month=MM/<run_id>.parquet` | scraper health per store per run | append-only |
| `catalog.parquet` | products: id, canonical_name, **raw_names** (local/Arabic names), tags, description, brand, category, **group** (taxonomy), **image**, sellers, listings (JSON), **similar** (nearest neighbours), enriched | sorted by id, 2k-row groups, Bloom(id) |
| `series.parquet` | chart points (product_id, ts, seller, price, currency, availability, url) | sorted by (product_id, ts), 2k-row groups, Bloom(product_id) |
| `stats.parquet` | per product min / max / median / latest_min / in_stock_sellers / observations / group | sorted by product_id |
| `embeddings.parquet` | int8 MiniLM sentence vectors (384 B/product) + text hash | sorted by product_id |
| `redirects.parquet` | merged / renamed id → surviving id (old URLs keep working) | sorted |
| `enrichment.parquet` | Pydantic AI cache (one LLM call per product, ever) | – |
| `manifest.json` | versions, taxonomy counts, per-file **sha256 / bytes / footer size / rows / row groups**, per-run summaries + digests | – |
| `diagnostics/latest.json`, `diagnostics/runs/*.json` | store statuses, errors, flags, enrichment/embedding reports | – |

Every derived file declares `sorting_columns`, carries column statistics and a page index, and
keeps row groups small. hyparquet uses those to **prune**: a product's whole price history is
`filter: {product_id: {$eq}}` → footer (one exact read, size from the manifest) + Bloom filter +
one ~100 KB row group, whatever the file size. Series/stats are rebuilt from the full offers
history and resolved per *listing* (seller + URL), so merges and splits apply retroactively.
Outliers (`outlier_high/low`, `implausible_price`) stay in history but are excluded from charts.
Not used (yet): the Parquet VARIANT type – hyparquet decodes it but pyarrow 25 cannot write it.

### Maintenance commands

```bash
uv run egmarket rebuild    # recompute groups, embeddings/neighbours, series, stats, manifest
uv run egmarket reindex    # replay ALL history through current dedupe rules (rule/glossary fixes
                           # become retroactive); enrichment cache is re-applied via redirects
```

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

## Similarity search

* **Pipeline:** `enrich/embed.py` runs the exact ONNX file transformers.js downloads for
  `Xenova/all-MiniLM-L6-v2` (`onnx/model_quantized.onnx`) through onnxruntime, mean-pools,
  L2-normalises and stores int8 vectors; text = official name + brand + group + tags +
  description. Vectors are cached by text hash, so only changed products are re-embedded.
  Top-8 cosine neighbours are precomputed into `catalog.similar` ("Similar products" needs no
  model in the browser).
* **Browser:** the ✨ AI search toggle lazy-loads transformers.js (WebGPU when available, WASM
  otherwise), embeds the query with the same model, scores the int8 matrix with a dot product
  and fuses the ranking with the lexical index (reciprocal-rank fusion). Nothing is downloaded
  until the toggle is used.

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

## Frontend (`frontend/`, SvelteKit 2.70 · Svelte 5.57 · hyparquet · transformers.js)

Static SPA, mobile-first, no server. `manifest.json` is read first (never cached); every Parquet
URL is versioned by sha, whole-table files (catalog, stats, embeddings) are fetched once and kept
in the Cache API, `series.parquet` is read with range requests (exact footer size from the
manifest, Bloom + statistics pruning). Browse by taxonomy group, tag, seller, stock, price;
search official names, local/Arabic names and tags with prefix matching; ✨ AI search adds
meaning-based results; product pages show a touch-friendly SVG price chart per seller,
min/median/max, all seller links, "also sold as" local names and similar products.

```bash
cd frontend && pnpm install
ln -s ../../data static/data       # dev: serve the pipeline output
pnpm dev                           # http://localhost:5173
pnpm run sync-data ../data && pnpm run build   # static build in build/
```

`VITE_DATA_BASE` can point at any host that supports HTTP range requests (e.g. raw GitHub).
