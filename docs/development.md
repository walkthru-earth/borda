# Development and operations

[Project home](../README.md) · [Architecture](architecture.md) · [Development](development.md)

## Requirements

Use Python 3.12+, uv, Node.js 24 and the pnpm version pinned in `frontend/package.json`.
The checked-in snapshot is sufficient to run the frontend; enrichment credentials are optional.

## Run locally

```bash
uv sync --all-groups
cp .env.example .env            # add HETZNER_INFERENCE_TOKEN (never committed)
uv run borda --profile egypt stores
uv run borda run --max-pages 2 --no-ai --dry-run      # quick scraper check
uv run borda run                                      # full run → data/ + diagnostics/
uv run borda search اردوينو اونو                        # local name → official product
uv run borda show arduino-uno-r3
uv run borda diff                                     # last two runs
uv run pytest -q
```

Runtime settings use `BORDA_*` (provider credentials retain their provider names): see `.env.example` and `src/borda/config.py`.

## Maintenance commands

```bash
uv run borda rebuild    # recompute groups, embeddings/neighbours, series, stats, manifest
uv run borda rebuild --no-embeddings  # offline: refresh derived data without model downloads
uv run borda reindex    # replay ALL history through current dedupe rules (rule/glossary fixes
                           # become retroactive); enrichment cache is re-applied via redirects
```

## Verification

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q
cd frontend
pnpm test
pnpm run check
pnpm run build
```

Frontend setup, data synchronization and SEO generation commands are documented in
[Frontend](frontend.md). Reindexing and full runs write data; use the offline rebuild option
when only derived exports need refreshing.

## GitHub Actions

- `monthly-scrape.yml` – cron on the 1st: uv (cached) → pytest → scrape → … → commit `data/` +
  `diagnostics/` → upload logs as artifact → job summary table. Data is committed whenever at
  least one store succeeded; the job then turns red only if a store failed **or enrichment was
  requested but produced nothing with an error** (the model/endpoint contract broke – e.g. every
  batch answering HTTP 400). Secrets: `HETZNER_INFERENCE_TOKEN`, optional `BORDA_PROXY_URL`.
  Manual dispatch accepts `stores`, `max_pages`, `ai`, `fresh`. This workflow explicitly uses
  `BORDA_PROFILE=egypt` to maintain the default published snapshot. Custom-country jobs must
  select their profile and corresponding data/diagnostic paths separately.
  - **Logs:** every phase is a collapsible `::group::` with its duration; stores log progress every
    10 pages; enrichment logs every 5 batches with items/min; failures surface as
    `::error::`/`::warning::` annotations and in the step summary (enrichment line shows
    requested / enriched / cached / stale / failed / deferred and the last error); the full log is
    the `diagnostics-<run>` artifact.
  - **Time budget:** enrichment stops *launching* batches after `BORDA_AI_TIME_BUDGET_MIN`
    (default 100) so the 170-min job always reaches persist + commit; in-flight batches are still
    applied and the summary reports the deferred count. Deferred products are queued again
    next month. The enrichment cache (`data/enrichment.parquet`) is committed with
    the run, so progress is never lost even if the checkpoint below has expired.
  - **Checkpoints / resume:** `.cache/checkpoints/egypt/<profile-fingerprint>/<YYYY-MM>/` keeps each finished store's raw
    offers and a mirror of the LLM cache after every batch. The cache is saved with
    `if: always()`, so after a failure or the 170-min timeout, **"Re-run failed jobs"** (or the
    next dispatch that month) skips finished stores and already-enriched products and continues.
    Cleared automatically once a run persists. `--fresh` / `fresh=true` ignores it. Fetched pages
    are also cached (`BORDA_HTTP_CACHE_TTL_H`, 20 h), so a retry the same day re-parses instead
    of re-downloading; the monthly cron is always outside that window.
  - **Run history (Sept 2026 bring-up):** run 1 enriched 1,500 products (sequential, ~25
    items/min); run 2 was cancelled by hand after 2 h at batch 85/150 (the timeout would have
    hit before 6,000 items); runs 3–5 scraped fine but every enrichment batch failed with
    `System message must be at the beginning.` because the Borda refactor added a second
    (dynamic) instructions block that Hetzner's vLLM chat template rejects. Fixed by the vLLM
    model profile in `enrich/ai.py`, covered by `tests/test_enrich.py`, and now caught by the
    red-job gate above.
- `ci.yml` – installed `borda --help` smoke test, ruff + pytest, Node 24 search/filter regression tests (`pnpm test`), svelte-check + build.
- `deploy-pages.yml` – rebuilds the SPA with the latest Parquet after each data commit.

Pre-commit (ruff format/lint, uv lock, secrets guard, pytest): `uv tool install pre-commit && pre-commit install`.
