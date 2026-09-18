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
    cat.products[pid].enriched = True  # persisted products must also refresh stale cache rows
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


async def test_output_validator_retries_duplicate_keys():
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        item = {"key": "a", "canonical_name": "Part", "description": "d", "tags": []}
        items = [item, item] if calls == 1 else [item]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"items": items})])

    result = await enrichment_agent.run(
        '{"key":"a"}',
        model=FunctionModel(respond),
        deps=BatchDeps(expected_keys=frozenset({"a"})),
    )
    assert calls == 2 and len(result.output.items) == 1


async def test_arabic_translations_are_cached_and_preserve_identifiers(tmp_path, make_offer):
    store = ParquetStore(tmp_path)
    catalog = Catalog()
    pid, _ = catalog.resolve(make_offer("s1", "ESP32 DevKit V1", 300), fuzzy_threshold=93)
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "items": [
                            {
                                "key": pid,
                                "canonical_name": "ESP32 DevKit V1",
                                "canonical_name_ar": "لوحة تطوير ESP32 DevKit V1",
                                "description": "ESP32 development board operating at 3.3V.",
                                "description_ar": "لوحة تطوير ESP32 تعمل بجهد 3.3V.",
                                "specs": ["Voltage: 3.3V"],
                                "specs_ar": ["الجهد: 3.3V"],
                                "mpn": "ESP32",
                                "tags": ["esp32"],
                            }
                        ]
                    },
                )
            ]
        )

    enricher = Enricher(store, model=FunctionModel(respond), requests_per_minute=10_000)
    report = await enricher.enrich(catalog)
    assert report.enriched == 1
    product = catalog.products[catalog.resolve_id(pid)]
    assert product.canonical_name_ar == "لوحة تطوير ESP32 DevKit V1"
    assert product.description_ar == "لوحة تطوير ESP32 تعمل بجهد 3.3V."
    assert product.specs_ar == ["الجهد: 3.3V"]
    store.write_catalog(catalog)
    restored = store.read_catalog().products[product.id]
    assert restored.canonical_name_ar == product.canonical_name_ar
    assert restored.description_ar == product.description_ar
    assert restored.specs_ar == product.specs_ar
    assert store.read_enrichment()[product.id].specs_ar == product.specs_ar
    # Cached translations survive a new catalog with no translated fields set.
    fresh = Catalog.from_products(
        [
            product.model_copy(
                update={
                    "enriched": False,
                    "canonical_name_ar": None,
                    "description_ar": None,
                    "specs_ar": [],
                }
            )
        ]
    )
    cached = await Enricher(store, model=FunctionModel(respond)).enrich(fresh)
    assert calls == 1 and cached.cached == 1
    assert fresh.products[product.id].canonical_name_ar == product.canonical_name_ar


async def test_arabic_specs_must_correspond_to_english_specs():
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        item = {
            "key": "a",
            "canonical_name": "Part",
            "description": "Description",
            "tags": [],
            "specs": ["Voltage: 3.3V"],
            "specs_ar": ["الجهد: 3.3V", "التيار: 1A"] if calls == 1 else ["الجهد: 3.3V"],
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"items": [item]})])

    result = await enrichment_agent.run(
        '{"key":"a"}',
        model=FunctionModel(respond),
        deps=BatchDeps(expected_keys=frozenset({"a"})),
    )
    assert calls == 2 and result.output.items[0].specs_ar == ["الجهد: 3.3V"]


def test_legacy_parquet_without_arabic_columns_still_loads(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from egmarket.models import Product

    store = ParquetStore(tmp_path)
    catalog = Catalog.from_products([Product(id="part", canonical_name="Part")])
    store.write_catalog(catalog)
    table = pq.read_table(store.catalog_path).drop(
        ["canonical_name_ar", "description_ar", "specs_ar"]
    )
    pq.write_table(table, store.catalog_path)
    product = store.read_catalog().products["part"]
    assert product.canonical_name_ar is None and product.description_ar is None
    assert product.specs_ar == []
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "key": "part",
                    "canonical_name": "Part",
                    "description": "English description",
                    "tags": [],
                    "version": 2,
                }
            ]
        ),
        store.enrichment_path,
    )
    cached = store.read_enrichment()["part"]
    assert cached.version == 2 and cached.canonical_name_ar is None and cached.specs_ar == []


def test_newer_checkpoint_translations_override_older_persisted_cache(tmp_path):
    from egmarket.models import ENRICHMENT_VERSION, utcnow

    store = ParquetStore(tmp_path / "data")
    legacy = Enrichment(key="part", canonical_name="Part", description="Old", tags=[])
    legacy.version = 2
    store.write_enrichment({"part": legacy}, "old", utcnow())
    mirror = ParquetStore(tmp_path / "checkpoint")
    translated = legacy.model_copy(
        update={"version": ENRICHMENT_VERSION, "canonical_name_ar": "قطعة"}
    )
    mirror.write_enrichment({"part": translated}, "new", utcnow())
    enricher = Enricher(store, mirror_path=mirror.enrichment_path)
    assert enricher.cache["part"].canonical_name_ar == "قطعة"
    assert enricher.cache["part"].version == ENRICHMENT_VERSION


async def test_ai_cannot_merge_unpopulated_pcb_into_assembled_board(tmp_path, make_offer):
    store = ParquetStore(tmp_path)
    catalog = Catalog()
    assembled, _ = catalog.resolve(make_offer("s1", "Arduino Uno R3", 300), fuzzy_threshold=93)
    pcb, _ = catalog.resolve(make_offer("s2", "Arduino Uno R3 PCB", 20), fuzzy_threshold=93)
    assert assembled != pcb
    original_pcb = catalog.products[pcb].model_copy(deep=True)
    report = await Enricher(
        store,
        model=_fake_llm({assembled: "Arduino Uno R3", pcb: "Arduino Uno R3"}),
        requests_per_minute=10_000,
    ).enrich(catalog)
    assert len(catalog.products) == 2 and report.merged == 0
    assert catalog.products[pcb] == original_pcb
    assert pcb not in store.read_enrichment()  # never persist the contradictory new answer


async def test_old_cached_ai_name_cannot_undo_accessory_split(tmp_path, make_offer):
    from egmarket.models import utcnow

    store = ParquetStore(tmp_path)
    catalog = Catalog()
    assembled, _ = catalog.resolve(make_offer("s1", "Arduino Uno R3", 300), fuzzy_threshold=93)
    pcb, _ = catalog.resolve(make_offer("s2", "Arduino Uno R3 PCB", 20), fuzzy_threshold=93)
    wrong = Enrichment(key=pcb, canonical_name="Arduino Uno R3", description="Wrong", tags=[])
    wrong.version = 2
    store.write_enrichment({pcb: wrong}, "old", utcnow())
    report = await Enricher(store).enrich(catalog, limit=0)
    assert report.requested == 0 and report.merged == 0
    assert assembled in catalog.products and pcb in catalog.products
    assert catalog.products[pcb].canonical_name.endswith("PCB")
    assert catalog.products[pcb].description is None


@pytest.mark.parametrize(
    ("names", "proposed"),
    [
        (("Raspberry Pi 4 4GB", "Raspberry Pi 4 8GB"), "Raspberry Pi 4 4GB"),
        (
            ("ESP32-S3 Development Board", "ESP32-C3 Development Board"),
            "ESP32-S3 Development Board",
        ),
        (("Widget Board 30 pin", "Widget Board 38 pin"), "Widget Development Board"),
        (("Arduino Uno R3", "Arduino Uno R4"), "Arduino Uno R3"),
        (("Sensor Kit", "Sensor Module"), "Sensor Module"),
    ],
)
async def test_ai_cannot_merge_conflicting_explicit_variants(tmp_path, names, proposed):
    from egmarket.models import Product

    catalog = Catalog.from_products(
        [
            Product(id=f"part-{i}", canonical_name=name, raw_names=[name])
            for i, name in enumerate(names)
        ]
    )
    report = await Enricher(
        ParquetStore(tmp_path),
        model=_fake_llm({"part-0": proposed, "part-1": proposed}),
        requests_per_minute=10_000,
    ).enrich(catalog)
    assert len(catalog.products) == 2 and report.merged == 0
