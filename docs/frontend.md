# Frontend, languages and search

[Project home](../README.md) · [Architecture](architecture.md) · [Development](development.md)

## Search and filter contract

The browser builds a lexical index over official names, all seller aliases, brands, categories
and tags, including cached Arabic product names. Matching folds accents, Arabic diacritics
and alef variants, and Arabic-Indic digits. A deterministic electronics glossary translates
common queries such as `اردوينو اونو` and `حساس حرارة` into English search terms, so Arabic
queries also find English-only listings without an AI download.
Part-number prefixes match even when an exact shorter part number also exists. Every query
term must match; all lexical results are retrieved **before** filtering so the catalog is not
silently truncated at 5,000 products. Filter state is kept in the URL for shareable searches.



Seller, stock and budget filters follow the [listing-level data contract](data.md#listing-level-filters-and-freshness).

## Arabic interface and cached translations

The language switch provides an Arabic RTL interface with Cairo typography and localized
numbers, dates and price labels. Technical identifiers and store names keep their original
spelling. English remains available, and the language preference is stored in the browser.

The Python Pydantic AI enrichment schema produces `canonical_name_ar`, `description_ar` and
`specs_ar` alongside English content. Its prompt requests faithful Arabic prose while preserving
brands, part numbers, numeric values and units. Translated specs keep the English order and
count. Both language versions are persisted in `enrichment.parquet` and `catalog.parquet`;
no live model request is needed when a visitor switches languages.

Enrichment cache version **3** queues older entries for refresh during the normal configured
AI pipeline, respecting its item limit and rate settings. An offline rebuild does not create
translations or call a paid model. Until translated data is available, product names,
descriptions and specs fall back to English. Existing Parquet snapshots without the additive
Arabic columns remain readable. Interface translations and glossary-based Arabic search work
independently of product enrichment.

## Optional AI discovery

* **Pipeline:** `enrich/embed.py` runs the exact ONNX file transformers.js downloads for
  `Xenova/all-MiniLM-L6-v2` (`onnx/model_quantized.onnx`) through onnxruntime, mean-pools,
  L2-normalises and stores int8 vectors; text = official name + brand + group + tags +
  description. Vectors are cached by text hash, so only changed products are re-embedded.
  Top-8 cosine neighbours are precomputed into `catalog.similar` ("Similar products" needs no
  model in the browser).
* **Browser:** AI discovery lazy-loads transformers.js and the embedding matrix only after it
  is enabled with a query. It uses WebGPU when available, falls back to WASM if initialization
  fails, embeds the query with the same model and fuses semantic rankings with lexical results
  using reciprocal-rank fusion. Query inference runs locally in the browser; model files are
  downloaded from Hugging Face on first use.
* **Language and failures:** MiniLM here is English-focused; AI discovery works best with
  English descriptions. Arabic component names are supported by lexical search, without a
  model download. Failed model/data loads are retryable, and AI failure leaves regular search
  usable. AI discovery supplements keyword matches and does not infer stock or prices.

## Frontend (`frontend/`, SvelteKit · Svelte 5 · Lucide · hyparquet · transformers.js)

Static SPA, mobile-first, no server, with Lucide icons and a responsive filter sidebar.
`manifest.json` is fetched with cache revalidation on catalog initialization; every Parquet
URL is versioned by sha, whole-table files (catalog, stats, embeddings) are fetched once and kept
in the Cache API, `series.parquet` is read with range requests (exact footer size from the
manifest, Bloom + statistics pruning). Files are zstd-compressed and decoded with
[hyparquet-compressors](https://github.com/hyparam/hyparquet-compressors) (also brings WASM snappy). Browse by taxonomy group, tag, seller, stock, price;
search official names, local/Arabic names and tags with prefix matching; ✨ AI search adds
meaning-based results; product pages show a touch-friendly SVG price chart per seller,
min/median/max, all seller links, "also sold as" local names and similar products.

```bash
# Node.js 24 and the packageManager-pinned pnpm version
cd frontend
pnpm install --frozen-lockfile
pnpm run sync-data ../data          # copy the checked-in pipeline snapshot
pnpm dev                           # http://localhost:5173
pnpm test                          # search, Arabic normalization, same-offer filters
pnpm run check                     # Svelte / TypeScript diagnostics
pnpm run build                     # syncs ../data, builds app + crawlable HTML in build/
```

Builds automatically sync `../data`. During development, run `pnpm run sync-data ../data` again after rebuilding Python outputs if `static/data` is a directory rather than a symlink. Keep `manifest.json`
and the Parquet files from the same snapshot together when publishing.
`VITE_DATA_BASE` can point at a host with browser CORS access and HTTP range request support
(e.g. raw GitHub); otherwise the bundled `static/data` files are used. Set `BASE_PATH` when
building for a project subpath such as GitHub Pages. No AI provider credentials belong in the
frontend: optional enrichment tokens are used only by the Python pipeline.

### Search-engine indexing and static product pages

The production build also runs `frontend/scripts/generate-seo.mjs`. It reads the same bundled
catalog and stats Parquet snapshot and emits a real `product/<id>/index.html` for every product,
with a readable product overview, specifications, recorded seller offers, related product links,
unique title and description, canonical URL, social metadata, and initial-HTML `Product` JSON-LD.
The homepage includes category links and 20 products for discovery. These pages work without
JavaScript; when the app mounts, it replaces the static overview with the interactive interface.
All visitors receive the same HTML. This follows Google's guidance on
[JavaScript SEO](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics)
and [initial-HTML product markup](https://developers.google.com/search/docs/appearance/structured-data/product-snippet).

Structured prices come **only** from valid current seller offers in EGP, with recorded stock
states. Historical minima, missing prices, reviews and ratings are never invented. Snapshots
without `current_offers` still get product metadata, but omit offer markup. Rebuild and deploy
HTML and Parquet together so search metadata reflects the published data.

- Default production site: `https://walkthru.earth/borda`. Override `SITE_URL` for the generator,
  and keep it consistent with the app's `VITE_SITE_ORIGIN` and `BASE_PATH` build settings.
- Product canonicals and sitemap URLs end in `/`, matching GitHub Pages directory routes.
- `sitemap.xml` indexes `sitemap-pages.xml` and product sitemaps with at most 50,000 URLs each.
  Submit `https://walkthru.earth/borda/sitemap.xml` in Google Search Console and Bing Webmaster Tools.
- The generated `robots.txt` is useful when publishing at an origin root. On the `/borda` project
  subpath, crawlers instead use `https://walkthru.earth/robots.txt`; that root site's file should
  contain `Sitemap: https://walkthru.earth/borda/sitemap.xml`. A subpath robots file cannot control
  the whole origin.
- `404.html` retains the SPA bootstrap so older links can resolve catalog redirects, while its
  initial HTML uses `noindex,follow`. Truly missing addresses retain GitHub Pages' HTTP 404 status.

To regenerate just the static HTML after a build, run `node scripts/generate-seo.mjs` from
`frontend/`. Optional positional arguments select the build directory and Parquet data directory.
`pnpm test` includes regression coverage for HTML/JSON escaping, canonical URLs, truthful offers,
404 behavior, repeatable generation, and sitemap chunking. Generated HTML is build output, not
checked-in source. Search engines decide whether and when to index pages or show rich results.
