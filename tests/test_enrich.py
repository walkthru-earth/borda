"""Pydantic AI enrichment with FunctionModel / TestModel – no real LLM calls."""

import json

import pytest
from pydantic import ValidationError
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from egmarket.enrich import BatchDeps, Enricher, EnrichmentBatch, build_model, enrichment_agent
from egmarket.models import Enrichment
from egmarket.normalize import Catalog
from egmarket.storage import ParquetStore


def _fake_llm(canonical_by_key: dict[str, str]):
    """FunctionModel that answers the structured tool call from the prompt's keys."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = messages[-1].parts[-1].content
        keys = [
            json.loads(line)["key"] for line in str(prompt).splitlines() if line.startswith("{")
        ]
        items = [
            {
                "key": k,
                "canonical_name": canonical_by_key.get(k, k),
                "description": f"desc {k}",
                "tags": ["Sensor", "I2C"],
                "brand": None,
            }
            for k in keys
        ]
        tool = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool, {"items": items})])

    return FunctionModel(respond)


async def test_enricher_corrects_names_unifies_products_and_caches(tmp_path, make_offer):
    store = ParquetStore(tmp_path)
    cat = Catalog()
    a, _ = cat.resolve(make_offer("s1", "ESP32 WROOM Dev Board", 300), fuzzy_threshold=93)
    b, _ = cat.resolve(make_offer("s2", "ESP-WROOM-32 Board 38 pin", 310), fuzzy_threshold=93)
    c, _ = cat.resolve(make_offer("s3", "DHT22 sensor", 120), fuzzy_threshold=93)
    assert len(cat.products) == 3

    model = _fake_llm({a: "ESP32 DevKit V1", b: "ESP32 DevKit V1", c: "DHT22 (AM2302)"})
    report = await Enricher(store, model=model, requests_per_minute=10_000).enrich(
        cat, batch_size=2
    )

    assert report.enriched == 3 and report.merged == 1 and report.batches == 2
    assert len(cat.products) == 2
    survivor = cat.resolve_id(b)
    assert (
        survivor == cat.resolve_id(a) == "esp32-devkit-v1"
    )  # new product -> id follows official name
    assert cat.resolve_id(c) == "dht22-am2302"
    p = cat.products[survivor]
    assert p.canonical_name == "ESP32 DevKit V1"
    assert p.description.startswith("desc") and {"sensor", "i2c"} <= set(p.tags)
    # both Egyptian spellings are kept as searchable aliases
    assert {"ESP32 WROOM Dev Board", "ESP-WROOM-32 Board 38 pin"} <= set(p.raw_names)
    assert p.enriched

    # cache persisted to parquet and reused without calling the model again
    cached = ParquetStore(tmp_path).read_enrichment()
    assert {"esp32-devkit-v1", "dht22-am2302"} <= set(cached) and len(cached) == 3
    fresh = Catalog.from_products(
        [pp.model_copy(update={"enriched": False}) for pp in cat.sorted_products()]
    )
    report2 = await Enricher(store, model=_fake_llm({}), requests_per_minute=10_000).enrich(fresh)
    assert report2.cached == 2 and report2.requested == 0


async def test_output_validator_forces_retry_on_missing_keys():
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        items = [{"key": "a", "canonical_name": "A", "description": "d", "tags": []}]
        if calls > 1:
            items.append({"key": "b", "canonical_name": "B", "description": "d", "tags": []})
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"items": items})])

    result = await enrichment_agent.run(
        '{"key":"a"}\n{"key":"b"}',
        model=FunctionModel(respond),
        deps=BatchDeps(expected_keys=frozenset("ab")),
    )
    assert calls == 2 and {e.key for e in result.output.items} == {"a", "b"}


async def test_agent_schema_is_valid_for_test_model():
    """TestModel generates schema-valid but key-less output -> our validator exhausts retries."""
    with enrichment_agent.override(model=TestModel()), pytest.raises(UnexpectedModelBehavior):
        await enrichment_agent.run('{"key":"zz"}', deps=BatchDeps(expected_keys=frozenset({"zz"})))


async def test_model_failure_is_reported_not_raised(tmp_path, make_offer):
    def boom(messages, info):
        raise RuntimeError("HTTP 429 rate limited")

    store = ParquetStore(tmp_path)
    cat = Catalog()
    cat.resolve(make_offer("s1", "Some Part", 1), fuzzy_threshold=93)
    enricher = Enricher(store, model=FunctionModel(boom), requests_per_minute=10_000)
    import asyncio

    async def no_sleep(_):
        return None

    asyncio_sleep = asyncio.sleep
    asyncio.sleep = no_sleep  # avoid the 60 s back-off in tests
    try:
        report = await enricher.enrich(cat)
    finally:
        asyncio.sleep = asyncio_sleep
    assert report.failed_batches == 1 and "429" in report.error and report.enriched == 0


def test_build_model_hetzner_requires_token(monkeypatch):
    monkeypatch.delenv("HETZNER_INFERENCE_TOKEN", raising=False)
    monkeypatch.setattr("egmarket.enrich.ai.settings.hetzner_token", None)
    with pytest.raises(RuntimeError):
        build_model("hetzner:Qwen/Qwen3.6-35B-A3B-FP8")
    monkeypatch.setenv("HETZNER_INFERENCE_TOKEN", "x")
    m = build_model("hetzner:Qwen/Qwen3.6-35B-A3B-FP8")
    assert m.model_name == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert m.settings["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert build_model("openai:gpt-5-mini") == "openai:gpt-5-mini"


def test_enrichment_model_normalises_tags():
    e = Enrichment(
        key="k", canonical_name="X", description="d", tags=["Wi-Fi", " I2C ", "Dev Board"]
    )
    assert e.tags == ["dev-board", "i2c", "wi-fi"]
    assert isinstance(EnrichmentBatch(items=[e]).items[0], Enrichment)
    with pytest.raises(ValidationError):  # > 8 tags
        Enrichment(key="k", canonical_name="X", description="d", tags=[f"t{i}" for i in range(9)])


async def test_legacy_cache_rows_are_refreshed_once(tmp_path, make_offer):
    from egmarket.models import ENRICHMENT_VERSION

    store = ParquetStore(tmp_path)
    cat = Catalog()
    pid, _ = cat.resolve(make_offer("s1", "TP4056 charger module", 15), fuzzy_threshold=93)
    legacy = Enrichment(
        key=pid, canonical_name="TP4056 Li-ion Charger Module", description="old", tags=["power"]
    )
    legacy.version = 1
    store.write_enrichment(
        {pid: legacy},
        "old-model",
        __import__("datetime").datetime(2025, 1, 1, tzinfo=__import__("datetime").UTC),
    )

    calls: list[str] = []

    def respond(messages, info):
        keys = [
            json.loads(line)["key"]
            for line in str(messages[-1].parts[-1].content).splitlines()
            if line.startswith("{")
        ]
        calls.extend(keys)
        items = [
            {
                "key": k,
                "canonical_name": "TP4056 Li-ion Charger Module",
                "description": "new richer text",
                "tags": ["power"],
                "specs": ["Input: 5V", "Charge current: 1A"],
                "mpn": "TP4056",
            }
            for k in keys
        ]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"items": items})])

    enricher = Enricher(
        store,
        model=FunctionModel(respond),
        requests_per_minute=10_000,
        descriptions={pid: "Seller says: 1A charger with protection"},
    )
    report = await enricher.enrich(cat)
    assert (
        report.stale == 1 and report.enriched == 1 and calls
    )  # refreshed exactly because of the version
    p = cat.products[cat.resolve_id(pid)]
    assert (
        p.specs == ["Input: 5V", "Charge current: 1A"]
        and p.mpn == "TP4056"
        and p.description == "new richer text"
    )
    assert ParquetStore(tmp_path).read_enrichment()[p.id].version == ENRICHMENT_VERSION

    report2 = await Enricher(
        store, model=FunctionModel(respond), requests_per_minute=10_000
    ).enrich(
        Catalog.from_products(
            [pp.model_copy(update={"enriched": False}) for pp in cat.sorted_products()]
        )
    )
    assert report2.stale == 0 and report2.cached == 1  # second time: served from cache
