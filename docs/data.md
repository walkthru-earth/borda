# Data and snapshot contracts

[Project home](../README.md) · [Architecture](architecture.md) · [Development](development.md)

## Data layout (`data/`, Parquet format 2.6 · zstd-9 · DataPage v2 · page index)

| path | contents | layout |
|---|---|---|
| `offers/year=YYYY/month=MM/<run_id>.parquet` | every observed listing: run_id, ts, product_id, seller, raw_name, url, price, currency, availability, sku, category, image, flags | append-only, hive partitioned, sorted by (product_id, seller) |
| `store_runs/year=YYYY/month=MM/<run_id>.parquet` | scraper health per store per run | append-only |
| `catalog.parquet` | products: id, canonical_name / canonical_name_ar, **raw_names** (local/Arabic names), tags, description / description_ar (2-3 sentences), **specs** / specs_ar ("Key: value"), **mpn**, **datasheet_url**, **brand** (manufacturer only – never a shop; canonical spelling), category, **group** (taxonomy), **image**, sellers, listings (JSON), **similar** (nearest neighbours), enriched, extra_metadata (JSON; `description_source: "seller"` marks a compact summary distilled from seller text until the model describes the product) | sorted by id, 2k-row groups, Bloom(id) |
| `series.parquet` | chart points (product_id, ts, seller, price, currency, availability, url) | sorted by (product_id, ts), 4k-row groups, Bloom(product_id) |
| `stats.parquet` | per product historical min / max / median, latest_min / latest_ts / in_stock_sellers, observations, and **current_offers** (JSON listing observations) | sorted by product_id |
| `embeddings.parquet` | int8 MiniLM sentence vectors (384 B/product) + text hash | sorted by product_id |
| `redirects.parquet` | merged / renamed id → surviving id (old URLs keep working) | sorted |
| `listings.parquet` | per seller listing: plain-text **seller description** + **documentation links** found on the page (latest text, kept if the seller drops it) | sorted by product_id, Bloom |
| `enrichment.parquet` | versioned Pydantic AI enrichment cache | – |
| `.borda-profile.json` | ownership marker written before the first Parquet update, allowing interrupted runs to resume without relabeling a country | internal, not published to the frontend |
| `manifest.json` | **profile** (country, comparison currency, locales, timezone, languages), versions, taxonomy counts, per-file **sha256 / bytes / footer size / rows / row groups**, per-run summaries + digests | – |
| `diagnostics/latest.json`, `diagnostics/runs/*.json` | store statuses, errors, flags, enrichment/embedding reports | – |

Every derived file declares `sorting_columns`, carries column statistics and a page index, and
keeps row groups small. hyparquet uses those to **prune**: a product's whole price history is
`filter: {product_id: {$eq}}` → footer (one exact read, size from the manifest) + Bloom filter +
one ~100 KB row group, whatever the file size. Series/stats are rebuilt from the full offers
history and resolved per *listing* (seller + URL), so merges and splits apply retroactively.
Outliers (`outlier_high/low`, `implausible_price`) stay in history but are excluded from charts.
Not used (yet): the Parquet VARIANT type – hyparquet decodes it but pyarrow 25 cannot write it.



## Listing-level filters and freshness

`stats.current_offers` is a JSON array of the latest valid listing observations for each seller,
with `seller`, `url`, `price`, `currency`, `availability` and `ts`. The browser parses timestamps
into `Date` values. An observation is identified by seller + URL; a seller can have multiple
listings for one component. These are recorded observations, not live inventory: a seller whose
scrape failed can have an older last check than another seller.

Current offers use each seller's newest observed run timestamp, so scraping a subset of stores
does not erase prices from untouched stores. Outlier and foreign-currency rows remain in raw
history but are excluded from comparisons in the selected profile’s currency (EGP for Egypt). `latest_ts` records the product's newest valid
observation, including products no longer present in a seller's current observations. Two
limitations remain: a partial scrape can omit previously seen listings, and a successful
zero-offer scrape cannot clear an earlier snapshot because exports currently derive freshness
from offer rows rather than store-run status. Do not interpret either case as confirmed stock.

Seller, stock and budget constraints must match **the same listing**. A cheap out-of-stock
offer does not satisfy an in-stock budget filter because another seller has stock at a higher
price. The displayed and sorted filtered price is the lowest eligible listing price in the snapshot’s comparison currency;
unknown prices sort last and do not satisfy a budget. Unfiltered `latest_min` is a listed
price and can include out-of-stock offers. Zero confirmed in-stock sellers does not prove
all sellers are out of stock; availability can be unknown or preorder.

Older snapshots without `current_offers` still load. Global price and stock filters can use
legacy summary fields independently, but seller-specific stock/price and combined stock/budget
cannot be verified and do not produce matches. Regenerate and publish the data with
`uv run borda rebuild --no-embeddings` to enable the full filter contract.

## Country isolation

Each snapshot belongs to one country profile. `manifest.profile` carries `id`, `country_code`,
`country_name`, `country_name_ar`, `currency`, `locale`, `locale_ar`, `timezone` and `languages`.
The default `egypt` profile uses EGP and retains `data/` and `diagnostics/`. Other profiles
default to `data/<id>/` and `diagnostics/<id>/`, with separate HTTP caches and checkpoints.
Explicit data directories are checked against the ownership marker and existing manifest, so a
profile cannot silently append into another country's snapshot. Historical files are retained.

The frontend uses profile metadata for country copy, eligible comparison prices, number/date
formatting and SEO. Older manifests without a profile fall back to Egypt. Changing a profile
does not convert prices: observations in other currencies stay in raw history and are omitted
from that profile's comparisons. See [country profile setup](pipeline.md#country-profiles).

Prices currently use two decimal places throughout the stored model and UI. Country profiles
do not expand this precision; currencies requiring three decimal places need a schema and
formatting update before use.
