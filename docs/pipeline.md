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
| fut-electronics, devboardsmarket, circuits-elec | Shopify | `/products.json` (Shopify throttles it per client IP; the shared GitHub runner IP can stay throttled for minutes, see the 429 budget below) | on |
| uge-one, makerselectronics, mostelectronic, fares-pcb | WooCommerce | Store API `/wp-json/wc/store/v1/products` | on |
| microohm | WooCommerce | Store API `/wp-json/wc/store/v1/products` | off: temporarily skipped after runner 403 |
| ic-hat | PrestaShop | XHR JSON listing | off: temporarily skipped after runner 403 |
| ram-e-shop | Odoo 17 | HTML cards (schema.org microdata), `?ppg=200` → ~16 pages | on |
| easytest | custom | HTML `data-et-*` attributes from `easytestgroup.com/en/store?page=N` (moved from `easytest.com.eg` in 2026; product paths and listing keys unchanged) | on |
| electra | Locafy v2 (Laravel/Livewire) | anonymous `/products` Livewire snapshot + `/livewire/update` (96/page) | on |
| elghazawy | custom Laravel | HTML cards from `maintenance-tools` and `electricity-connectors` only; all products are embedded in each category response | on |
| maamoon | Wix | anonymous storefront GraphQL (`/_api/wix-ecommerce-storefront-web/api`, 250/page); sitemap + JSON-LD fallback | on |
| eshopmas | WooCommerce | – | off: Cloudflare challenge |
| rsdelivers | Next.js | – | off: prices rendered client-side, >100k SKUs |
| amazon-eg | – | – | off: optional per brief, anti-bot |

**Why no official SDKs?** Shopify's Admin/Storefront APIs, Wix's REST API and Odoo's JSON-RPC all
need merchant-issued credentials; libraries such as `ShopifyAPI`, `wix-python-sdk` or `odoorpc`
wrap those. For third-party price tracking the anonymous surfaces above are the only ones
available, and they are JSON where it matters (Shopify `/products.json`, WooCommerce Store API,
PrestaShop XHR, Wix storefront GraphQL, and Electra's Livewire update protocol) – Odoo and
EasyTest are the only HTML card parsers left.

Add a store to the selected profile JSON (`src/borda/profiles/egypt.json` for Egypt). Add a platform:
subclass `BaseScraper` (yield `RawOffer`) and register it in `scrapers/__init__.py`.

**HTTP retries** (`src/borda/http.py`): transient errors (timeouts, 403/5xx, dropped HTTP/2
connections) get `BORDA_MAX_RETRIES` (3) quick attempts at 1.5 s, 3 s, 6 s. HTTP 429 has its own
budget – `BORDA_RATE_LIMIT_RETRIES` (5) attempts waiting `max(Retry-After, 15 s → 30 s → 60 s →
120 s → 120 s)`, capped by `BORDA_RATE_LIMIT_MAX_BACKOFF_S` (120) – because a throttled client IP
typically needs minutes, not seconds, and stores scrape concurrently so the wait costs little
wall-clock. Per-store politeness is `BORDA_REQUEST_DELAY_S` (0.6) or `params.delay_s`. A store
that still fails is reported `partial` and keeps the pages it got. When the runner IP itself is
throttled or blocked (403), `BORDA_PROXY_URL` routes the scrape through another egress.

## Normalization & dedupe (`src/borda/normalize/`)

1. `clean()` – lowercase, strip marketing noise, unify units (`5 V`→`5v`, `10 K Ohm`→`10kohm`),
   alias table (`node mcu`→`nodemcu`), **Arabic glossary** (`اردوينو اونو`→`arduino uno`).
2. alias lookup → **canonical rules** (regexes for ~70 well-known products) → **fuzzy**
   (`rapidfuzz` token-sort ≥ 93 inside a blocking key, only if numeric signatures agree:
   `10k`≠`100k`, `4gb`≠`8gb`, and accessory identities agree) → new product (slug id;
   hash suffix only on collision).
3. Provenance: every raw name, seller and listing URL is kept on the product. The product
   image is the first one seen, replaced only when the listing that supplied it reports a new
   picture (including a seller that moved domains) or when it sits on a host marked
   `params.hotlink_blocked` in the profile and another seller offers one. A store with
   `params.image_proxy` (a template with `{host}` and `{path}`) gets its product images
   rewritten through that CDN – Makers Electronics is Automattic-hosted and answers hotlinked
   `<img>` requests with a browser challenge, so its pictures are served via Jetpack Photon
   (`https://i0.wp.com/{host}{path}?w=800`). Raw offer history keeps the original URLs.
4. Pydantic AI (`enrich/ai.py`) corrects the name to the manufacturer's original, writes a
   2-3 sentence description, up to 6 spec highlights, MPN, brand, taxonomy group and tags, plus
   Arabic translations of name/description/specs; identical official names are **merged**
   (redirect recorded); products created in the same run get their id re-slugged from the
   official name.

Board rules are conservative: ESP32 chipsets, memory and pin variants, USB bridge variants,
compatible boards, and integrated application boards retain their specific listing names.
Bare PCBs, shields, cases, expansion boards and relay boards cannot become a generic development
board just because its name appears in the title. Fuzzy matching also preserves accessory
nouns (including one-word forms such as *ProtoShield*, *holder*, *bracket*, *heatsink*),
preventing long, nearly identical PCB and assembled-board names from merging. Peripheral
part numbers embedded in a board title do not turn that board into a standalone GPS or USB
converter. Arduino and Raspberry Pi rules likewise protect accessories and hardware variants.

Part-number rules (`HC-SR04`, `SG90`, `L298N`, `TP4056`, …) only apply when the listing *is*
that part (`names._same_part`): brackets, holders, adapter boards, controller boards "for" the
part, kits, multi-packs (`4-in-1`, cascaded), bare chips sold instead of the module (SOP/DIP/
`IC`) or modules sold instead of the chip, different channel counts, and listings that carry
another part number (`CS100A … compatible with HC-SR04`, `NRF24L01 … ATMEGA328`) are left as
their own products. AI renames and merges reject conflicting accessory identities and explicit
hardware variants – memory, pin count, ESP32 chipset, Arduino revision, **MCU part
(ATmega168P ≠ ATmega328P), USB bridge (CH340 ≠ FT232), channel count and disjoint part numbers
(10131N ≠ CD4013)** – including contradictory cached answers replayed during an offline reindex.
Reindexing applies corrected identities to historical prices by seller/listing ownership while
preserving old product redirects. Ids that already existed keep their slug during a replay (only
genuinely split or merged products get new ids and redirects), so public URLs, embedding cache
keys and enrichment cache keys stay stable; the re-keyed cache is persisted at the end. Cached embeddings should be retained only when both the
product ID and embedded-text hash still match; changed products remain lexically searchable
until embeddings are regenerated.

## Brands

Store JSON is unreliable about brands: Shopify's `vendor` is usually the shop itself
("Circuits Electronics", "Future Electronics Egypt"), Wix/WooCommerce use "Other", and the same
maker is spelled several ways (`BESTON`/`Beston`, `Atmel/Microchip`). `normalize/brands.py`
keeps `Product.brand` a **manufacturer in its canonical spelling**: seller names (any store of
the active profile, or a label containing the country name), placeholders, substitute lists
(`NXP/TI/ST`) and part numbers echoed as brands become `null`; known aliases map to one spelling
(`BRAND_ALIASES`); SHOUTING unknown brands are title-cased while short acronyms (APC, NXP) stay.
`infer_brand()` recognises maker names inside titles and store categories (Waveshare, LILYGO/
TTGO, Seeed Studio/XIAO, Elecrow/CrowPanel, UNI-T, Fluke, …) and maker product-line patterns
(`BRAND_PATTERNS`: LILYGO's `T-Display`, `T-SIM7000G`, `T-A7608SA-H` …, but not T-nuts or
T-type connectors) unless preceded by *for / with / compatible*, and never infers widely
cloned names (Arduino, Raspberry Pi) from a title alone.
Precedence: maker named in the title → model answer → store vendor. Brand search tags include
company aliases (`ttgo` → LILYGO, `seeed` → Seeed Studio) so either spelling finds the product;
the enrichment prompt asks for the maker in official spelling and never receives a shop as hint.
Legacy snapshots are cleaned when loaded, so a `reindex`/`rebuild` fixes old brands offline.

## Descriptions & datasheets

Products the model has not reached yet still get compact information: `normalize/summary.py`
distils the longest seller text into one or two neutral technical sentences (marketing, first-
person seller talk, prices and Arabic-only text are dropped) and up to six `Key: value` spec
highlights from `Key: value` lines and flattened spec tables (pin-outs, package contents and
application lists are skipped). These rows carry `extra_metadata.description_source = "seller"`
and are replaced by the model's answer once enriched; the frontend labels them as summarised
from the seller listing. Summaries are recomputed by every run, `reindex` and `rebuild`.

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

Default: Hetzner Inference (OpenAI-compatible, experimental, EU-hosted)
`hetzner:Qwen/Qwen3.6-35B-A3B-FP8`, tool-call structured output (`tool_choice=required`),
thinking disabled (`chat_template_kwargs.enable_thinking=false`, measured `reasoning_tokens=0`).
Any Pydantic AI `provider:model` string works too (`BORDA_AI_MODEL=openai:gpt-5-mini`).
Missing credentials → enrichment is skipped, run continues.

Hetzner's documented limits per API key are **10 requests, 4M input tokens and 100k output
tokens per 60 s** (HTTP 429 beyond); the model list is served by `/v1/models` and currently
contains Qwen3.6-35B-A3B and Qwen3.8-27B. A 20-item bilingual batch costs ~7k output tokens
and roughly a minute of generation, so the pacing is:

| setting | default | why |
|---|---|---|
| `BORDA_AI_BATCH_SIZE` | 20 | fits `max_tokens=24000` with EN + AR descriptions and specs |
| `BORDA_AI_REQUESTS_PER_MINUTE` | 8 | headroom under the 10/min cap for validator retries |
| `BORDA_AI_CONCURRENCY` | 3 | generation time is the bottleneck, not the request quota; 3 × ~7k tokens/min stays far under 100k |
| `BORDA_AI_TIME_BUDGET_MIN` | 100 | stop launching batches so the 170-min job always persists and commits; in-flight batches finish, the rest are reported as `deferred` and queued next run (0 = unlimited) |
| `BORDA_AI_MAX_ITEMS_PER_RUN` | 5000 | hard cap on products sent per run |

The endpoint serves the open-weight models through vLLM, whose Qwen chat template rejects more
than one leading `system` message (`400 System message must be at the beginning.`). The agent has
two instruction sources (the static prompt plus the per-profile market context), so `build_model`
uses Pydantic AI's vLLM model profile with `openai_chat_supports_multiple_system_messages=False`,
which merges them into one system turn – see
[pydantic/pydantic-ai#5812](https://github.com/pydantic/pydantic-ai/issues/5812). A wire-level
regression test asserts the request carries exactly `system, user`.

When identical official names merge two products, the survivor also receives the cache row so an
offline reindex replays the merge without a new model call.
Failures are best-effort per batch: a failed batch is logged and its products retried next run;
after three failures with nothing enriched the run stops calling the model. HTTP 429 pushes the
pacer back 60 s. Answers that would rename a product across an identity boundary (accessory noun,
memory/pin/chipset/revision variant) are rejected and not cached; confirming the *current*
canonical name is never treated as an identity change, so cached rows replay cleanly.

See [Arabic content and optional AI discovery](frontend.md) for cached translations, embedding compatibility and browser behavior.
