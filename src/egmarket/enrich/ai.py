"""Pydantic AI enrichment: correct names to the manufacturer's original, write a short
technical description, add search tags – and unify products that turn out to be the
same thing. Local (Egyptian) spellings are never lost: they stay in `Product.raw_names`.

Token/cost discipline:
* one structured (tool-call) request per batch of N products, compact JSON-lines input
* results cached in `data/enrichment.parquet` by product id – an item is sent at most once
* an `output_validator` makes the model retry when it forgets/invents keys
* Qwen "thinking" is disabled (`chat_template_kwargs.enable_thinking=false`) – it would
  otherwise spend the whole output budget on hidden reasoning
* requests are paced to the provider's rate limit (Hetzner: 10 req / 60 s)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import settings
from ..models import ENRICHMENT_VERSION, Enrichment, Product, utcnow
from ..normalize import Catalog, slugify
from ..normalize import names as _names
from ..normalize.categories import assign_group
from ..storage import ParquetStore
from ..storage.parquet import read_enrichment_file

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
You normalise product listings scraped from Egyptian electronics/maker stores. Listings
use local spellings, Arabic, or seller jargon; map each to the manufacturer's official product.
For every input item return exactly one output item with the same `key`.
- canonical_name: the official English product name, concise, no seller names, no marketing
  words, no quantities/prices. Prefer the well-known part/model number
  (e.g. "ESP32-WROOM-32 DevKit V1", "HC-SR04 Ultrasonic Sensor", "LM2596 Buck Converter Module").
  Two listings of the same physical product MUST get an identical canonical_name.
- description: 2-3 technical sentences – what it is, what it is used for, key electrical
  specs (voltage, interface, range, current…). Use the seller text (`desc`) as a source but
  rewrite it: neutral, no marketing, no seller names, no prices, English.
- specs: up to 6 "Key: value" highlights actually supported by the name/seller text; omit
  guesses.
- mpn: manufacturer part number when identifiable (e.g. "ESP32-WROOM-32", "L298N"), else null.
- tags: up to 8 lowercase tags for search (family, interface, function, brand).
- brand: manufacturer if clearly known, else null.
- group: one of dev-boards, microcontrollers-ics, sensors, wireless-iot, displays-leds,
  motors-drivers, power, passive-components, semiconductors, connectors-cables, prototyping,
  tools-instruments, 3d-printing-cnc, robotics-kits, other.
Return only the structured output."""


class EnrichInput(BaseModel):
    key: str
    names: list[str] = Field(max_length=4)
    category: str | None = None
    brand: str | None = None
    desc: str | None = Field(default=None, description="seller description excerpt")


class EnrichmentBatch(BaseModel):
    """One enrichment per input key."""

    items: list[Enrichment]


@dataclass
class BatchDeps:
    expected_keys: frozenset[str]


# Model is resolved at run time so importing this module never needs credentials; tests use
# `enrichment_agent.override(model=...)`.
enrichment_agent: Agent[BatchDeps, EnrichmentBatch] = Agent(
    deps_type=BatchDeps,
    output_type=EnrichmentBatch,
    instructions=INSTRUCTIONS,
    retries=2,
    name="egmarket-enricher",
)


@enrichment_agent.output_validator
async def _keys_match(ctx: RunContext[BatchDeps], out: EnrichmentBatch) -> EnrichmentBatch:
    got = {e.key for e in out.items}
    missing = ctx.deps.expected_keys - got
    extra = got - ctx.deps.expected_keys
    if missing or extra:
        raise ModelRetry(
            f"Return one item per input key. Missing: {sorted(missing)[:10]}; "
            f"unexpected: {sorted(extra)[:10]}."
        )
    return out


def build_model(name: str | None = None) -> Model | str:
    """`hetzner:<model>` -> Hetzner Inference (OpenAI-compatible); anything else is passed to
    Pydantic AI's `provider:model` inference (openai:, anthropic:, google-gla:, test, ...)."""
    name = name or settings.ai_model
    if name.startswith("hetzner:"):
        token = os.environ.get("HETZNER_INFERENCE_TOKEN") or settings.hetzner_token
        if not token:
            raise RuntimeError("HETZNER_INFERENCE_TOKEN is not set")
        return OpenAIChatModel(
            name.split(":", 1)[1],
            provider=OpenAIProvider(base_url=settings.hetzner_base_url, api_key=token),
            settings=OpenAIChatModelSettings(
                temperature=0.0,
                max_tokens=12000,  # 20 items x (600-char description + specs) with headroom
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            ),
        )
    return name


def _excerpt(text: str, limit: int) -> str:
    """First `limit` chars of a seller description, cut at a sentence/line boundary."""
    t = " ".join(text.split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for sep in (". ", "; ", ", "):
        if (i := cut.rfind(sep)) > limit // 2:
            return cut[: i + 1]
    return cut


class RateLimiter:
    """Simple pacer: at most `n` calls per `window_s` seconds (evenly spaced)."""

    def __init__(self, n: int, window_s: float) -> None:
        self.interval = window_s / max(n, 1)
        self._next = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        if now < self._next:
            await asyncio.sleep(self._next - now)
        self._next = max(now, self._next) + self.interval


@dataclass
class EnrichReport:
    requested: int = 0
    enriched: int = 0
    cached: int = 0
    stale: int = 0  # cached under an older prompt version, queued for refresh
    merged: int = 0
    batches: int = 0
    failed_batches: int = 0
    error: str | None = None
    merges: list[tuple[str, str]] = field(default_factory=list)


class Enricher:
    def __init__(
        self,
        store: ParquetStore,
        agent: Agent[BatchDeps, EnrichmentBatch] | None = None,
        model: Model | str | None = None,
        requests_per_minute: int | None = None,
        key_aliases: dict[str, str] | None = None,
        mirror_path: Path | None = None,
        descriptions: dict[str, str] | None = None,
    ):
        self.store = store
        self.descriptions = descriptions or {}  # product id -> best seller description
        self.key_aliases = key_aliases or {}  # product id -> cache key (e.g. via redirects)
        self.mirror_path = mirror_path  # checkpoint copy, refreshed after every batch
        self.agent = agent or enrichment_agent
        self._model = model
        self.model_name = model if isinstance(model, str) else settings.ai_model
        self.cache: dict[str, Enrichment] = store.read_enrichment()
        if mirror_path and mirror_path.exists():
            extra = read_enrichment_file(mirror_path)
            new = {k: v for k, v in extra.items() if k not in self.cache}
            if new:
                log.info("enrichment: resumed %d cached items from checkpoint", len(new))
                self.cache.update(new)
        self.limiter = RateLimiter(requests_per_minute or settings.ai_requests_per_minute, 60)

    def save(self) -> None:
        self.store.write_enrichment(self.cache, self.model_name, utcnow())
        if self.mirror_path:
            self.mirror_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.store.enrichment_path, self.mirror_path)

    def _to_input(self, p: Product) -> EnrichInput:
        # shortest raw names first – they are usually the cleanest
        raw = sorted(set(p.raw_names) | {p.canonical_name}, key=len)[:4]
        desc = self.descriptions.get(p.id)
        return EnrichInput(
            key=p.id,
            names=raw,
            category=p.category,
            brand=p.brand,
            desc=_excerpt(desc, settings.ai_desc_chars) if desc else None,
        )

    async def enrich(
        self,
        catalog: Catalog,
        *,
        batch_size: int | None = None,
        limit: int | None = None,
    ) -> EnrichReport:
        report = EnrichReport()
        batch_size = batch_size or settings.ai_batch_size
        limit = settings.ai_max_items_per_run if limit is None else limit

        pending: list[Product] = []
        stale: list[Product] = []
        for p in catalog.sorted_products():
            if p.enriched:
                continue
            hit = self.cache.get(p.id) or self.cache.get(self.key_aliases.get(p.id, ""))
            if hit is not None and hit.version >= ENRICHMENT_VERSION:
                self._apply(catalog, p, hit, report)
                report.cached += 1
            elif hit is not None:
                self._apply(catalog, p, hit, report)  # keep the old answer until refreshed
                p.enriched = False
                stale.append(catalog.products.get(catalog.resolve_id(p.id), p))
            else:
                pending.append(p)
        # new products first, then one-off refresh of rows from an older prompt version
        pending.extend(x for x in stale if x.id in catalog.products and not x.enriched)
        report.stale = len(stale)
        pending = pending[:limit]
        report.requested = len(pending)
        if not pending:
            return report

        try:
            model = self._model or build_model()
        except Exception as exc:  # noqa: BLE001 - missing credentials must not kill the run
            report.error = f"{type(exc).__name__}: {exc}"
            log.warning("enrichment disabled: %s", report.error)
            return report

        n_batches = -(-len(pending) // batch_size)
        log.info(
            "enrichment: %d products in %d batches (model %s)",
            len(pending),
            n_batches,
            self.model_name,
        )
        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            inputs = [self._to_input(p) for p in batch]
            prompt = "\n".join(x.model_dump_json(exclude_none=True) for x in inputs)
            await self.limiter.wait()
            try:
                result = await self.agent.run(
                    prompt,
                    model=model,
                    deps=BatchDeps(expected_keys=frozenset(x.key for x in inputs)),
                )
            except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
                report.failed_batches += 1
                report.error = f"{type(exc).__name__}: {exc}"
                log.error("enrichment batch %d failed: %s", i // batch_size, report.error)
                if report.failed_batches >= 3 and report.enriched == 0:
                    break  # credentials / network – stop burning time
                if "429" in str(exc):
                    await asyncio.sleep(60)
                continue
            report.batches += 1
            if report.batches % 5 == 0 or report.batches == n_batches:
                log.info(
                    "enrichment: batch %d/%d done, enriched=%d merged=%d",
                    report.batches,
                    n_batches,
                    report.enriched + len(result.output.items),
                    report.merged,
                )
            for e in result.output.items:
                if (p := catalog.products.get(e.key)) is None:
                    continue
                self.cache[e.key] = e
                self._apply(catalog, p, e, report)
                report.enriched += 1
            self.save()
        return report

    def _apply(self, catalog: Catalog, p: Product, e: Enrichment, report: EnrichReport) -> None:
        if p.id not in catalog.products:
            return  # already merged away in this pass
        p.description = e.description
        p.specs = e.specs or p.specs
        p.mpn = e.mpn or p.mpn
        p.brand = p.brand or e.brand
        p.tags = sorted(set(p.tags) | set(e.tags))
        ruled = assign_group(name=e.canonical_name, tags=p.tags, store_category=p.category)
        vague = {"other", "prototyping"}
        p.group = e.group if e.group and (e.group not in vague or ruled in vague) else ruled
        p.enriched = True
        p.extra_metadata = {**p.extra_metadata, "enriched_at": utcnow().isoformat()}
        old_name = p.canonical_name
        new_name = e.canonical_name.strip()
        if new_name and _names.clean(new_name) != _names.clean(old_name):
            p.raw_names = [*p.raw_names, old_name]  # local spelling stays searchable
            p.canonical_name = new_name
            if p.id in catalog.new_ids:  # brand-new this run -> id can follow the official name
                other = catalog.aliases.get(_names.clean(new_name))
                if not other or other == p.id or other not in catalog.products:
                    old_id = p.id
                    new_id = catalog.unique_id(slugify(new_name), _names.clean(new_name))
                    catalog.rename(old_id, new_id)  # mutates p.id
                    self.cache[new_id] = self.cache.pop(old_id, e)
                    p = catalog.products[new_id]
        # Unify: another product already carries this official name -> merge into it.
        other = catalog.aliases.get(_names.clean(p.canonical_name))
        if other and other != p.id and other in catalog.products:
            catalog.merge(other, p.id)
            report.merged += 1
            report.merges.append((p.id, other))
        else:
            catalog.aliases[_names.clean(p.canonical_name)] = p.id
