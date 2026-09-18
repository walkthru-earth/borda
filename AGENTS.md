# Working on Borda

Borda is an electronics comparison catalog with configurable country profiles; Egypt is the
default deployment. The Python package and CLI are `borda`; the public site is
https://walkthru.earth/borda. Read the relevant focused document before changing a contract: [architecture](docs/architecture.md),
[data](docs/data.md), [pipeline](docs/pipeline.md), [frontend](docs/frontend.md),
and [development](docs/development.md).

## Repository map

- `src/borda/`: Python scrapers, normalization, AI enrichment and Parquet exports.
- `frontend/`: Svelte 5 / SvelteKit static app, browser search and SEO generation.
- `tests/` and `frontend/tests/`: Python and Node regression tests.
- `data/`: checked-in history and generated snapshots; `diagnostics/`: pipeline reports.
- `docs/brand/` and `frontend/static/brand/`: brand materials and served assets.

## Contracts to preserve

- There is no required Python web server. Keep the static browser-to-Parquet architecture.
- Each snapshot/deployment serves one country profile. Egypt is the default; preserve separate
  data, diagnostics and checkpoint paths for other profiles. Never mix currencies or countries
  in a catalog, and do not imply that profile selection performs currency conversion.
- Product identity must preserve chipset, memory, pin, voltage and accessory distinctions.
  Bare PCBs, compatible boards and integrated application boards are not generic dev boards.
  Apply these safeguards to canonical rules, fuzzy matches and AI rename/merge paths.
- Retain seller aliases, listing ownership and redirects. Reindexing must remap historical
  prices per listing so corrected product splits also correct old charts.
- Seller, stock and budget conditions must match the same `current_offers` listing.
  Do not substitute historical minima for current prices or present unknown stock as sold out.
- Export comparisons in the selected country profile’s currency without mixing currencies or
  flagged price outliers. These excluded observations remain in raw history. Freshness is observational, not live stock.
- Keep optional columns backward compatible. Arabic product text falls back to English;
  interface localization and Arabic keyword search work without paid model calls.
- AI discovery is opt-in and failures must leave keyword search usable. Cached embedding
  vectors are valid only for matching product IDs and embedded-text hashes.
- Datasheet links must come from seller evidence. SEO prices must come from valid current
  offers; do not invent ratings, reviews or availability.
- Keep manifest hashes, byte counts, footer sizes and row counts synchronized with files.
  Publish static HTML and Parquet from the same snapshot.

## Local work and verification

Use Python 3.12+, uv, Node.js 24 and the pnpm version pinned in `frontend/package.json`.
Install with `uv sync --all-groups` and `pnpm install --frozen-lockfile` in `frontend/`.
Run checks appropriate to the change; CI runs:

```bash
uv run borda --help
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q
cd frontend
pnpm test
pnpm run check
pnpm run build
```

Add regression coverage for identity, pricing, filtering, cache or export-contract changes.
Use mocked stores and models for tests. Documentation-only changes need link and content
checks rather than a full data rebuild. Keep the root README short; put technical detail in docs.

## Data and deployment boundaries

`uv run borda rebuild --no-embeddings` rebuilds derived exports offline.
`uv run borda reindex --no-embeddings` replays identity rules and cached enrichment without
new AI calls. These commands write snapshots; use them when the task requires updated data.
A plain full pipeline run can scrape stores and invoke configured AI providers.

After Python data changes, run `pnpm run sync-data ../data` from `frontend/` for local previews.
Production builds synchronize data and generate crawlable product HTML automatically.
For the project subpath, keep `BASE_PATH=/borda`, `SITE_URL=https://walkthru.earth/borda`
and `VITE_SITE_ORIGIN=https://walkthru.earth` consistent. Preserve base-aware asset/navigation
URLs; test the production subpath when changing routing or deployment.

Keep provider credentials in pipeline environment/secrets, never in browser code or committed
files. Respect the user's requested scope for external actions and deployment.
