<p align="center">
  <a href="https://walkthru.earth/borda"><img src="frontend/static/brand/borda-logo.png" width="160" alt="Borda | بوردة logo"></a>
</p>

<h1 align="center">Borda | بوردة</h1>

<p align="center">Find the right electronics part, at the right shop. Starting with Egypt.</p>

<p align="center">
  <a href="https://walkthru.earth/borda">Explore Borda</a> ·
  <a href="https://github.com/walkthru-earth/borda">Source</a>
</p>

- Search components in English or Arabic.
- Use the default Egypt catalog or configure a separate country deployment.
- Filter by category, seller, recorded stock and budget.
- Compare store listings and price history.
- Discover related parts, specifications and datasheets.

## Quick start

Requires Python 3.12+, uv, Node.js 24 and pnpm. The included data snapshot works without AI credentials.

```bash
uv sync --all-groups
cd frontend
pnpm install --frozen-lockfile
pnpm run sync-data ../data
pnpm dev
```

Open [localhost:5173](http://localhost:5173). Prices and stock are recorded observations;
check the seller before buying.

## Documentation

- [Architecture](docs/architecture.md)
- [Data and snapshot contracts](docs/data.md)
- [Country profiles and scraping](docs/pipeline.md)
- [Frontend, Arabic and search](docs/frontend.md)
- [Development and deployment](docs/development.md)
- [Brand assets](docs/brand/README.md)

Contributor and coding-agent guidance: [AGENTS.md](AGENTS.md).

License: [CC BY 4.0](LICENSE).

## Citation

```bibtex
@software{borda,
  author  = {Youssef Harby, Myagmarjargal Mendbayar},
  title   = {Borda: Electronic Component Search and Price Comparison},
  year    = {2026},
  url     = {https://github.com/walkthru-earth/borda},
  license = {CC-BY-4.0},
  note    = {Walkthru.Earth}
}
```
