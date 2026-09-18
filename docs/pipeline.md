# Scraping, product identity and enrichment

[Project home](../README.md) · [Architecture](architecture.md) · [Development](development.md)

## Country profiles

The Python distribution, import package and command are all `borda`. Egypt is the built-in
profile and remains the default. Its authoritative country metadata and store definitions
live in `src/borda/profiles/egypt.json`.

```bash
uv run borda --profile egypt stores
# Equivalent persistent setting: BORDA_PROFILE=egypt
uv run borda --profile /absolute/path/to/uae.json stores
```

`--profile` is a global argument, placed **before** the command. `BORDA_PROFILE` accepts the
same built-in name or JSON path. One profile selects one country/currency/store catalog; it
is not a live cross-country selector or a foreign-exchange converter. Use a separate data
snapshot and frontend deployment for each country.

A custom profile starts with this shape. The illustrative store is disabled; replace it with
an actual compatible storefront and enable it only when ready to scrape:

```json
{
  "id": "uae",
  "country_code": "AE",
  "country_name": "United Arab Emirates",
  "country_name_ar": "الإمارات العربية المتحدة",
  "currency": "AED",
  "locale": "en-AE",
  "locale_ar": "ar-AE",
  "timezone": "Asia/Dubai",
  "languages": ["en", "ar"],
  "minimum_price": 0.05,
  "stores": [
    {
      "slug": "example-components",
      "name": "Example Components",
      "base_url": "https://example.com",
      "platform": "woocommerce",
      "enabled": false
    }
  ]
}
```

Store definitions support `enabled`, `note`, `params` and an optional currency override.
A profile configures existing adapters; it does not automatically support every storefront.
Shopify and WooCommerce adapters target their public catalog interfaces, while store-specific
adapters may require changes for another site. Add and test an adapter for unsupported formats.
A store's currency must reflect its actual prices. Only observations in the profile currency
enter comparisons; other currencies remain in raw history. Prices currently support two decimal
places. A profile using a currency that needs three-decimal minor-unit precision (such as KWD
or BHD) requires a monetary-schema and formatting change before its prices can be represented
accurately.

Egypt keeps the existing `data/`, `diagnostics/` and `.cache/http` paths. Other profile IDs
use `data/<id>/`, `diagnostics/<id>/` and `.cache/<id>/http`, with isolated checkpoints.
`BORDA_DATA_DIR` sets the exact data path. `BORDA_DIAGNOSTICS_DIR` and `BORDA_CACHE_DIR`
override their base paths; non-Egypt pipeline runs append the profile ID to keep them isolated.
Checkpoint directories also include the profile ID and configuration fingerprint, preventing
resume data from a different country or changed store configuration from being reused.
Existing manifest identity is checked before writing another profile into a data directory.
The frontend reads exported country metadata from `manifest.profile`; legacy snapshots fall
back to Egypt. [Frontend deployment](frontend.md#publishing-another-country-snapshot) explains
how to publish a selected snapshot. UI translations currently cover English and Arabic only.

## Stores (default Egypt profile)

| slug | platform | method | status |
|---|---|---|---|
| fut-electronics, devboardsmarket, circuits-elec | Shopify | `/products.json` | on |
| uge-one, makerselectronics, mostelectronic, fares-pcb | WooCommerce | Store API `/wp-json/wc/store/v1/products` | on |
| microohm | WooCommerce | Store API `/wp-json/wc/store/v1/products` | off: temporarily skipped after runner 403 |
| ic-hat | PrestaShop | XHR JSON listing | off: temporarily skipped after runner 403 |
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

Add a store to the selected profile JSON (`src/borda/profiles/egypt.json` for Egypt). Add a platform:
subclass `BaseScraper` (yield `RawOffer`) and register it in `scrapers/__init__.py`.

## Normalization & dedupe (`src/borda/normalize/`)

1. `clean()` – lowercase, strip marketing noise, unify units (`5 V`→`5v`, `10 K Ohm`→`10kohm`),
   alias table (`node mcu`→`nodemcu`), **Arabic glossary** (`اردوينو اونو`→`arduino uno`).
2. alias lookup → **canonical rules** (regexes for ~70 well-known products) → **fuzzy**
   (`rapidfuzz` token-sort ≥ 93 inside a blocking key, only if numeric signatures agree:
   `10k`≠`100k`, `4gb`≠`8gb`, and accessory identities agree) → new product (slug id;
   hash suffix only on collision).
3. Provenance: every raw name, seller and listing URL is kept on the product.
4. Pydantic AI (`enrich/ai.py`) corrects the name to the manufacturer's original, writes a
   one-line description and tags; identical official names are **merged** (redirect recorded);
   products created in the same run get their id re-slugged from the official name.

Board rules are conservative: ESP32 chipsets, memory and pin variants, USB bridge variants,
compatible boards, and integrated application boards retain their specific listing names.
Bare PCBs, shields, cases, expansion boards and relay boards cannot become a generic development
board just because its name appears in the title. Fuzzy matching also preserves accessory
nouns, preventing long, nearly identical PCB and assembled-board names from merging. Peripheral
part numbers embedded in a board title do not turn that board into a standalone GPS or USB
converter. Arduino and Raspberry Pi rules likewise protect accessories and hardware variants.
AI renames and merges also reject conflicting accessory identities and explicit hardware
variants, including contradictory cached answers replayed during an offline reindex.
Reindexing applies corrected identities to historical prices by seller/listing ownership while
preserving old product redirects. Cached embeddings should be retained only when both the
product ID and embedded-text hash still match; changed products remain lexically searchable
until embeddings are regenerated.

## Descriptions & datasheets

Seller descriptions come for free from the JSON we already fetch (Shopify `body_html`,
WooCommerce `description`, PrestaShop `description_short`, Wix `description`) – converted to
plain text and stored per listing in `listings.parquet`, with every documentation-looking link
(`datasheet`, `.pdf`, manual, schematic, wiki, manufacturer domains) extracted from the HTML.
The LLM gets the longest seller text as context and writes a neutral 2-3 sentence description,
up to 6 spec highlights and the MPN. `datasheet_url` is set **only** from a link a seller
actually published (ranked datasheet+pdf > pdf > docs); otherwise the frontend offers a
"find datasheet" search by MPN (alldatasheet / Octopart) – no LLM-invented URLs. Cache rows carry
a `version`; bumping `ENRICHMENT_VERSION` refreshes older answers once (≤ items/run cap).

## Enrichment model

Default: Hetzner Inference (OpenAI-compatible) `hetzner:Qwen/Qwen3.6-35B-A3B-FP8`, tool-call
structured output, thinking disabled (`chat_template_kwargs.enable_thinking=false`), paced to
8 req/min, ≤5000 products/run, batch 20. Any Pydantic AI `provider:model` string works too
(`BORDA_AI_MODEL=openai:gpt-5-mini`). Missing credentials → enrichment is skipped, run continues.

See [Arabic content and optional AI discovery](frontend.md) for cached translations, embedding compatibility and browser behavior.
