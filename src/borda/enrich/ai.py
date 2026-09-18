"""Pydantic AI enrichment: correct names to the manufacturer's original, write a short
technical description, add search tags – and unify products that turn out to be the
same thing. Local seller spellings are never lost: they stay in `Product.raw_names`.

Token/cost discipline:
* one structured (tool-call) request per batch of N products, compact JSON-lines input
* results cached in `data/enrichment.parquet` by product id and enrichment version
* an `output_validator` makes the model retry when it forgets/invents keys
* Qwen "thinking" is disabled (`chat_template_kwargs.enable_thinking=false`) – it would
  otherwise spend the whole output budget on hidden reasoning
* requests are paced to the provider's rate limit (Hetzner: 10 req / 60 s, 100k output
  tokens / 60 s) and a few batches run concurrently – generation time, not the request
  quota, is the bottleneck (~1 min per 20-item bilingual batch)
* a wall-clock budget stops launching new batches so the surrounding job always finishes and
  persists what it has; the remaining products are picked up by the next run
* the Hetzner endpoint serves Qwen through vLLM, whose chat template rejects more than one
  leading `system` message – Pydantic AI's vLLM profile merges the static and dynamic
  instructions into a single system message (pydantic/pydantic-ai#5812)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles import merge_profile
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.vllm import VLLMProvider

from ..config import settings
from ..models import ENRICHMENT_VERSION, Enrichment, Product, utcnow
from ..normalize import Catalog, slugify
from ..normalize import names as _names
from ..normalize.categories import assign_group
from ..profiles import PublicProfile, default_profile
from ..storage import ParquetStore
from ..storage.parquet import read_enrichment_file

if TYPE_CHECKING:
    import httpx2  # transport used by the OpenAI SDK; only needed for type hints here

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
You normalise product listings scraped from electronics/maker stores in the selected country. Listings
use local spellings, Arabic, or seller jargon; map each to the manufacturer's official product.
For every input item return exactly one output item with the same `key`.
- canonical_name: the official English product name, concise, no seller names, no marketing
  words, no quantities/prices. Prefer the well-known part/model number
  (e.g. "ESP32-WROOM-32 DevKit V1", "HC-SR04 Ultrasonic Sensor", "LM2596 Buck Converter Module").
  Two listings of the same physical product MUST get an identical canonical_name.
  Preserve identity-changing details: chipset/model revision, memory capacity, pin count,
  package, voltage/current rating and included accessories. Different capacities, chipsets
  or pin variants are separate products, even when sellers abbreviate the shared family.
  Never rename an unpopulated/bare PCB as an assembled working board, a shield/case/adapter
  as the board it fits, a kit as one component, or an accessory sold without a module as
  including that module. Keep PCB, kit and accessory qualifiers in the official name.
  When the source does not establish equivalence, retain the distinguishing seller wording
  instead of guessing that two listings are interchangeable.
- description: 2-3 technical sentences – what it is, what it is used for, key electrical
  specs (voltage, interface, range, current…). Use the seller text (`desc`) as a source but
  rewrite it: neutral, no marketing, no seller names, no prices, English.
- specs: up to 6 "Key: value" highlights actually supported by the name/seller text; omit
  guesses.
- canonical_name_ar, description_ar, specs_ar: provide faithful, clear Arabic translations
  of the English fields for local makers. Translate technical prose and spec labels,
  preserve brand names, model/part numbers (e.g. ESP32-WROOM-32, Arduino), numeric values,
  symbols and unit spellings (e.g. 3.3V, 1A, I2C) exactly as written in English. Do not
  transliterate identifiers, convert units, change specs, add claims or include HTML.
  Keep specs_ar aligned one-to-one and in the same order as specs; use [] when specs is [].
  Use null only when a faithful Arabic name or description cannot be supplied.
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
    profile: PublicProfile = field(default_factory=lambda: default_profile().public())


# Model is resolved at run time so importing this module never needs credentials; tests use
# `enrichment_agent.override(model=...)`.
enrichment_agent: Agent[BatchDeps, EnrichmentBatch] = Agent(
    deps_type=BatchDeps,
    output_type=EnrichmentBatch,
    instructions=INSTRUCTIONS,
    retries=2,
    name="borda-enricher",
)


@enrichment_agent.instructions
def country_context(ctx: RunContext[BatchDeps]) -> str:
    profile = ctx.deps.profile
    return (
        f"Market: {profile.country_name} ({profile.country_code}); "
        f"currency: {profile.currency}; locales: {profile.locale}, {profile.locale_ar}. "
        "Use this market context without changing technical product identities."
    )


@enrichment_agent.output_validator
async def _keys_match(ctx: RunContext[BatchDeps], out: EnrichmentBatch) -> EnrichmentBatch:
    got = {e.key for e in out.items}
    missing = ctx.deps.expected_keys - got
    extra = got - ctx.deps.expected_keys
    duplicates = len(out.items) != len(got)
    if missing or extra or duplicates:
        raise ModelRetry(
            f"Return one item per input key. Missing: {sorted(missing)[:10]}; "
            f"unexpected: {sorted(extra)[:10]}; duplicate keys: {duplicates}."
        )
    for item in out.items:
        if item.specs_ar and len(item.specs_ar) != len(item.specs):
            raise ModelRetry(
                f"For {item.key}, specs_ar must translate each specs entry in the same order."
            )
    return out


def hetzner_profile(model_name: str) -> OpenAIModelProfile:
    """Model profile for Hetzner Inference: the open-weight models are served through vLLM,
    so start from Pydantic AI's vLLM profile (Qwen/… schema handling, thinking support) and
    pin the one setting this run cannot live without – a single leading system message.
    Without it the static `INSTRUCTIONS` and the dynamic market-context instructions are sent
    as two `system` turns and the chat template answers HTTP 400
    `System message must be at the beginning.`"""
    return merge_profile(
        VLLMProvider.model_profile(model_name),
        OpenAIModelProfile(openai_chat_supports_multiple_system_messages=False),
    )


def build_model(
    name: str | None = None, *, http_client: httpx2.AsyncClient | None = None
) -> Model | str:
    """`hetzner:<model>` -> Hetzner Inference (OpenAI-compatible); anything else is passed to
    Pydantic AI's `provider:model` inference (openai:, anthropic:, google-gla:, test, ...).
    `http_client` lets tests capture the exact request the endpoint would receive."""
    name = name or settings.ai_model
    if name.startswith("hetzner:"):
        token = os.environ.get("HETZNER_INFERENCE_TOKEN") or settings.hetzner_token
        if not token:
            raise RuntimeError("HETZNER_INFERENCE_TOKEN is not set")
        model_name = name.split(":", 1)[1]
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                base_url=settings.hetzner_base_url, api_key=token, http_client=http_client
            ),
            profile=hetzner_profile(model_name),
            settings=OpenAIChatModelSettings(
                temperature=0.0,
                max_tokens=24000,  # bilingual descriptions/specs for a 20-item batch
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
    """Simple pacer: at most `n` calls per `window_s` seconds (evenly spaced).

    Safe for concurrent callers: each caller reserves its slot *before* sleeping, so two
    coroutines waking up together can never share one slot."""

    def __init__(self, n: int, window_s: float) -> None:
        self.interval = window_s / max(n, 1)
        self._next = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        slot = max(now, self._next)
        self._next = slot + self.interval
        if slot > now:
            await asyncio.sleep(slot - now)

    def penalize(self, seconds: float) -> None:
        """Push the next slot out (after an HTTP 429) without blocking in-flight work."""
        self._next = max(self._next, time.monotonic() + seconds)


@dataclass
class EnrichReport:
    requested: int = 0
    enriched: int = 0
    cached: int = 0
    stale: int = 0  # cached under an older prompt version, queued for refresh
    merged: int = 0
    batches: int = 0
    failed_batches: int = 0
    deferred: int = 0  # products not sent because the time budget ran out (next run picks them up)
    error: str | None = None
    merges: list[tuple[str, str]] = field(default_factory=list)


def _identity_labels(texts: list[str]) -> frozenset[str]:
    labels = set()
    for text in texts:
        labels.update(_names.accessory_signature(text))
        cleaned = _names.clean(text)
        if re.search(r"\bkit\b", cleaned) and not re.search(
            r"\b(?:dev|development) kit\b", cleaned
        ):
            labels.add("kit")
        if re.search(r"\b(?:not included|without (?:the )?(?:board|module))\b", cleaned):
            labels.add("module-not-included")
    return frozenset(labels)


def _explicit_variants(texts: list[str]) -> dict[str, set[str]]:
    """Only compare explicit corresponding specifications; absence is not a conflict.

    Full numeric signatures are deliberately not equated here: legitimate official aliases
    can replace ESP-WROOM-32 with ESP32, for example, or add a previously unspecified revision.
    """
    variants: dict[str, set[str]] = {}
    for text in texts:
        cleaned = _names.clean(text)
        for value in re.findall(r"\b\d+(?:\.\d+)?(?:kb|mb|gb|tb)\b", cleaned):
            variants.setdefault("memory", set()).add(value)
        for value in re.findall(r"\b(\d+)\s*-?\s*pins?\b", cleaned):
            variants.setdefault("pins", set()).add(value)
        for value in re.findall(r"\besp32(?:-?[sc]\d)?\b", cleaned):
            variants.setdefault("esp32-chipset", set()).add(value.replace("-", ""))
        if re.search(r"\b(?:arduino|uno)\b", cleaned):
            for value in re.findall(r"\br[34]\b", cleaned):
                variants.setdefault("arduino-revision", set()).add(value)
    chipsets = variants.get("esp32-chipset", set())
    if len(chipsets) > 1:
        chipsets.discard("esp32")  # a generic alias must not erase a known S3/C3 variant
    return variants


def _identities_conflict(left: list[str], right: list[str]) -> bool:
    if _identity_labels(left) != _identity_labels(right):
        return True
    a, b = _explicit_variants(left), _explicit_variants(right)
    return any(a[key].isdisjoint(b[key]) for key in a.keys() & b.keys())


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
        concurrency: int | None = None,
        time_budget_s: float | None = None,
    ):
        self.store = store
        self.concurrency = max(1, concurrency or settings.ai_concurrency)
        # 0 / None = unlimited. Measured in wall-clock seconds from the start of `enrich()`.
        self.time_budget_s = (
            settings.ai_time_budget_min * 60 if time_budget_s is None else time_budget_s
        )
        self.descriptions = descriptions or {}  # product id -> best seller description
        self.key_aliases = key_aliases or {}  # product id -> cache key (e.g. via redirects)
        self.mirror_path = mirror_path  # checkpoint copy, refreshed after every batch
        self.agent = agent or enrichment_agent
        self._model = model
        self.model_name = model if isinstance(model, str) else settings.ai_model
        self.cache: dict[str, Enrichment] = store.read_enrichment()
        if mirror_path and mirror_path.exists():
            extra = read_enrichment_file(mirror_path)
            new = {
                k: v
                for k, v in extra.items()
                if k not in self.cache or v.version > self.cache[k].version
            }
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
            hit = self.cache.get(p.id) or self.cache.get(self.key_aliases.get(p.id, ""))
            if p.enriched and (hit is None or hit.version >= ENRICHMENT_VERSION):
                continue
            if hit is not None and hit.version >= ENRICHMENT_VERSION:
                if self._apply(catalog, p, hit, report):
                    report.cached += 1
                else:
                    pending.append(p)
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

        batches = [pending[i : i + batch_size] for i in range(0, len(pending), batch_size)]
        n_batches = len(batches)
        log.info(
            "enrichment: %d products in %d batches (model %s, %d concurrent, budget %s)",
            len(pending),
            n_batches,
            self.model_name,
            self.concurrency,
            f"{self.time_budget_s / 60:.0f} min" if self.time_budget_s else "none",
        )
        started = time.monotonic()
        deadline = started + self.time_budget_s if self.time_budget_s else None
        public_profile = self.store.profile.public()

        async def run_batch(index: int) -> tuple[int, EnrichmentBatch | BaseException]:
            inputs = [self._to_input(p) for p in batches[index]]
            prompt = "\n".join(x.model_dump_json(exclude_none=True) for x in inputs)
            await self.limiter.wait()
            try:
                result = await self.agent.run(
                    prompt,
                    model=model,
                    deps=BatchDeps(
                        expected_keys=frozenset(x.key for x in inputs), profile=public_profile
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
                return index, exc
            return index, result.output

        # Bounded window of in-flight requests. Results are applied here, sequentially, because
        # `_apply` mutates the shared catalog (renames / merges) and the cache file.
        next_index = 0
        in_flight: set[asyncio.Task[tuple[int, EnrichmentBatch | BaseException]]] = set()
        stop = False
        while True:
            while not stop and next_index < n_batches and len(in_flight) < self.concurrency:
                if deadline is not None and time.monotonic() >= deadline:
                    stop = True
                    report.deferred = sum(len(b) for b in batches[next_index:])
                    log.warning(
                        "enrichment: time budget of %.0f min reached after %d/%d batches – "
                        "%d products deferred to the next run",
                        self.time_budget_s / 60,
                        next_index,
                        n_batches,
                        report.deferred,
                    )
                    break
                in_flight.add(asyncio.create_task(run_batch(next_index)))
                next_index += 1
            if not in_flight:
                break
            done, in_flight = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
            for task in sorted(done, key=lambda t: t.result()[0]):
                index, outcome = task.result()
                if isinstance(outcome, BaseException):
                    report.failed_batches += 1
                    report.error = f"{type(outcome).__name__}: {outcome}"
                    log.error("enrichment batch %d failed: %s", index, report.error)
                    if report.failed_batches >= 3 and report.enriched == 0:
                        stop = True  # credentials / endpoint contract – stop burning time
                    if "429" in str(outcome):
                        self.limiter.penalize(60)
                    continue
                report.batches += 1
                for e in outcome.items:
                    if (p := catalog.products.get(e.key)) is None:
                        continue
                    if self._apply(catalog, p, e, report):
                        self.cache[p.id] = e
                        report.enriched += 1
                self.save()
                if report.batches % 5 == 0 or report.batches + report.failed_batches == n_batches:
                    elapsed = time.monotonic() - started
                    log.info(
                        "enrichment: batch %d/%d done, enriched=%d merged=%d (%.1f min, %.0f items/min)",
                        report.batches,
                        n_batches,
                        report.enriched,
                        report.merged,
                        elapsed / 60,
                        report.enriched / max(elapsed / 60, 1e-6),
                    )
        # `stop` only prevents new launches – already running batches are still awaited and
        # applied above, so a run that hits the budget keeps every answer it paid for.
        return report

    def _apply(self, catalog: Catalog, p: Product, e: Enrichment, report: EnrichReport) -> bool:
        if p.id not in catalog.products:
            return False  # already merged away in this pass
        proposed_name = e.canonical_name.strip()
        source_names = [p.canonical_name, *p.raw_names]
        other_id = catalog.aliases.get(_names.clean(proposed_name))
        other = catalog.products.get(other_id or "")
        # Keeping the current canonical name cannot change identity: a cached answer whose
        # name already won (or the model confirming our name) must not be rejected just
        # because a seller spelling in `raw_names` mentions e.g. "relay", "PCB" or "1GB".
        renames = _names.clean(proposed_name) != _names.clean(p.canonical_name)
        conflict = renames and _identities_conflict(source_names, [proposed_name])
        source_variants, proposed_variants = (
            _explicit_variants(source_names),
            _explicit_variants([proposed_name]),
        )
        if renames and any(
            key in source_variants and key not in proposed_variants
            for key in ("memory", "arduino-revision")
        ):
            conflict = True
        if (
            renames
            and source_variants.get("esp32-chipset", set()) - {"esp32"}
            and "esp32-chipset" not in proposed_variants
        ):
            conflict = True
        if other is not None and other.id != p.id:
            conflict |= _identities_conflict(source_names, [other.canonical_name, *other.raw_names])
        if conflict:
            log.warning(
                "enrichment: rejected identity-changing name for %s: %s", p.id, proposed_name
            )
            return False
        p.description = e.description
        # Keep each translation tied to the English text from the same answer. Older
        # cache rows intentionally fall back to English rather than retaining stale Arabic.
        p.canonical_name_ar = e.canonical_name_ar
        p.description_ar = e.description_ar
        p.specs = e.specs or p.specs
        p.specs_ar = e.specs_ar if len(e.specs_ar) == len(p.specs) else []
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
        return True
