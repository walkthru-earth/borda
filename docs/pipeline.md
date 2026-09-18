# Scraping, product identity and enrichment

[Project home](../README.md) · [Architecture](architecture.md) · [Development](development.md)

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

## Normalization & dedupe (`src/egmarket/normalize/`)

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
(`EGMARKET_AI_MODEL=openai:gpt-5-mini`). Missing credentials → enrichment is skipped, run continues.

See [Arabic content and optional AI discovery](frontend.md) for cached translations, embedding compatibility and browser behavior.
