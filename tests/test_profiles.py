"""Country isolation and currencies are verified without network or real AI calls."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
import respx
from pydantic import HttpUrl, ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from borda.checkpoint import Checkpoint
from borda.cli import _diff, main
from borda.config import Settings, settings
from borda.enrich import Enricher
from borda.models import Availability, OfferRecord, Product, RawOffer, Run
from borda.normalize import Catalog, clean, flag_offers
from borda.pipeline import RunOptions, rebuild_exports, run_pipeline
from borda.profiles import (
    CountryProfile,
    active_profile,
    default_profile,
    load_profile,
    use_profile,
)
from borda.storage import ParquetStore, build_series, reference_prices


@pytest.fixture
def us_profile():
    return CountryProfile(
        id="united-states",
        country_code="US",
        country_name="United States",
        country_name_ar="الولايات المتحدة",
        currency="USD",
        locale="en-US",
        locale_ar="ar-US",
        timezone="America/New_York",
        languages=["en", "ar"],
        minimum_price="0.01",
        stores=[
            {
                "slug": "us-shop",
                "name": "US Store",
                "base_url": "https://us.test",
                "platform": "shopify",
            }
        ],
    )


def test_packaged_default_keeps_existing_market_and_paths(tmp_path):
    profile = load_profile("egypt")
    assert profile.currency == "EGP" and profile.country_code == "EG"
    assert len(profile.stores) == 17
    assert {s.currency for s in profile.configured_stores()} == {"EGP"}
    assert {"electra", "elghazawy"} <= {s.slug for s in profile.configured_stores()}
    cfg = Settings(repo_root=tmp_path, _env_file=None)
    assert cfg.paths(profile) == (
        tmp_path / "data",
        tmp_path / "diagnostics",
        tmp_path / ".cache/http",
    )
    assert set(profile.public().model_dump()) == {
        "id",
        "country_code",
        "country_name",
        "country_name_ar",
        "currency",
        "locale",
        "locale_ar",
        "timezone",
        "languages",
    }


def test_custom_profile_env_paths_cli_and_currency_defaults(
    tmp_path, us_profile, monkeypatch, capsys
):
    path = tmp_path / "us.json"
    path.write_text(us_profile.model_dump_json())
    monkeypatch.setenv("BORDA_PROFILE", str(path))
    cfg = Settings(repo_root=tmp_path, _env_file=None)
    assert cfg.country_profile == us_profile
    assert cfg.paths() == (
        tmp_path / "data/united-states",
        tmp_path / "diagnostics/united-states",
        tmp_path / ".cache/united-states/http",
    )
    cfg.cache_dir = tmp_path / "cache-override"
    cfg.diagnostics_dir = tmp_path / "diag-override"
    cfg.data_dir = tmp_path / "data-override"
    assert cfg.paths() == (
        tmp_path / "data-override",
        tmp_path / "diag-override/united-states",
        tmp_path / "cache-override/united-states",
    )
    monkeypatch.setattr(settings, "profile", "egypt")
    assert main(["--profile", str(path), "stores"]) == 0
    output = capsys.readouterr().out
    assert "us-shop" in output and "fut-electronics" not in output
    assert RawOffer(seller="us-shop", raw_name="Part", url="https://us.test/p").currency == "USD"


def test_cli_search_reads_custom_profile_once(tmp_path, us_profile, monkeypatch, capsys):
    profile_path = tmp_path / "us.json"
    profile_path.write_text(us_profile.model_dump_json())
    data_dir = tmp_path / "data"
    products = [
        Product(
            id=f"sensor-{i}",
            canonical_name=f"Sensor {i}",
            raw_names=[f"Sensor United States {i}", f"Temperature sensor {i}"],
            tags=["sensor", "temperature"],
            sellers=["us-shop"],
        )
        for i in range(20)
    ]
    ParquetStore(data_dir, profile=us_profile).write_catalog(Catalog.from_products(products))
    monkeypatch.setattr(settings, "profile", "egypt")
    monkeypatch.setattr(settings, "data_dir", data_dir)
    original_read = Path.read_text
    profile_reads = 0

    def count_reads(path, *args, **kwargs):
        nonlocal profile_reads
        if path == profile_path:
            profile_reads += 1
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", count_reads)
    previous_context = active_profile.get()
    assert main(["--profile", str(profile_path), "search", "sensor"]) == 0
    assert "sensor-0" in capsys.readouterr().out
    assert profile_reads == 1
    assert active_profile.get() is previous_context


@pytest.mark.parametrize(
    "update",
    [
        {"id": "../escape"},
        {"currency": "DOLLAR"},
        {"country_code": "USA"},
        {"timezone": "not/a-timezone"},
        {"languages": ["en", "en"]},
        {"languages": ["ar"]},
        {"minimum_price": -1},
    ],
)
def test_invalid_profiles_are_rejected(us_profile, update):
    with pytest.raises(ValidationError):
        CountryProfile.model_validate({**us_profile.model_dump(), **update})


def test_duplicate_store_slugs_are_rejected(us_profile):
    with pytest.raises(ValidationError, match="unique"):
        CountryProfile.model_validate({**us_profile.model_dump(), "stores": us_profile.stores * 2})


def test_profile_mismatch_cannot_relabel_existing_history(tmp_path, us_profile):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"profile": default_profile().public().model_dump()}))
    with pytest.raises(ValueError, match="belongs to profile egypt"):
        ParquetStore(tmp_path, profile=us_profile)
    path.write_text('{"products": 0}')  # a manifest from before country profiles
    ParquetStore(tmp_path, profile=default_profile())
    with pytest.raises(ValueError, match="belongs to profile egypt"):
        ParquetStore(tmp_path, profile=us_profile)
    path.unlink()
    (tmp_path / "catalog.parquet").write_bytes(b"legacy")
    with pytest.raises(ValueError, match="unlabelled legacy"):
        ParquetStore(tmp_path, profile=us_profile)
    with pytest.raises(ValueError, match="unlabelled legacy"):
        ParquetStore(tmp_path, profile=us_profile.model_copy(update={"id": "egypt"}))


def test_interrupted_first_custom_run_keeps_profile_ownership(tmp_path, us_profile):
    store = ParquetStore(tmp_path, profile=us_profile)
    store.write_series([])  # interruption before manifest generation
    assert not (tmp_path / "manifest.json").exists()
    assert (
        json.loads((tmp_path / ".borda-profile.json").read_text())
        == us_profile.public().model_dump()
    )
    ParquetStore(tmp_path, profile=us_profile)
    with pytest.raises(ValueError, match="belongs to profile"):
        ParquetStore(tmp_path, profile=default_profile())


def test_checkpoint_identity_includes_profile_configuration(tmp_path, us_profile):
    first = Checkpoint(tmp_path, "same-month", profile=us_profile)
    changed = us_profile.model_copy(update={"currency": "CAD"})
    second = Checkpoint(tmp_path, "same-month", profile=changed)
    third = Checkpoint(tmp_path, "same-month", profile=default_profile())
    assert len({first.dir, second.dir, third.dir}) == 3
    assert first.dir == tmp_path / us_profile.id / us_profile.fingerprint / "same-month"
    changed_stores = us_profile.model_copy(
        update={
            "stores": [
                us_profile.stores[0].model_copy(
                    update={"base_url": HttpUrl("https://changed.test")}
                )
            ]
        }
    )
    assert Checkpoint(tmp_path, "same-month", profile=changed_stores).dir != first.dir


def test_country_normalization_and_defaults_do_not_leak_between_profiles(us_profile):
    with use_profile(default_profile()):
        assert clean("Sensor Egypt") == "sensor"
        assert clean("Sensor United States") == "sensor united states"
        with use_profile(us_profile):
            assert clean("Sensor United States") == "sensor"
            assert clean("Sensor Egypt") == "sensor egypt"
            assert RawOffer(seller="shop", raw_name="Part", url="https://test/p").currency == "USD"
        assert clean("Sensor Egypt") == "sensor"
        assert RawOffer(seller="shop", raw_name="Part", url="https://test/p").currency == "EGP"


@respx.mock
async def test_custom_profile_pipeline_exports_its_currency_and_metadata(
    tmp_path, us_profile, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    for attr in ("data_dir", "cache_dir", "diagnostics_dir"):
        monkeypatch.setattr(settings, attr, None)
    respx.get(url__regex=r"https://us\.test/products\.json.*").side_effect = [
        httpx.Response(
            200,
            json={
                "products": [
                    {
                        "title": "DHT22 Sensor",
                        "handle": "sensor",
                        "variants": [{"price": "3.50", "available": True}],
                    }
                ]
            },
        ),
        httpx.Response(200, json={"products": []}),
    ]
    diag, code = await run_pipeline(RunOptions(profile=us_profile, ai=False, embeddings=False))
    assert code == 0 and diag.offers_kept == 1
    root = tmp_path / "data/united-states"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["profile"] == us_profile.public().model_dump()
    assert "stores" not in manifest["profile"] and "minimum_price" not in manifest["profile"]
    stats = pq.read_table(root / "stats.parquet").to_pylist()
    assert stats[0]["currency"] == "USD" and stats[0]["latest_min"] == 3.5
    assert json.loads(stats[0]["current_offers"])[0]["currency"] == "USD"
    assert (tmp_path / "diagnostics/united-states/latest.json").exists()
    assert not (tmp_path / "data/catalog.parquet").exists()
    rebuild_exports(root, embeddings=False, profile=us_profile)
    assert json.loads((root / "manifest.json").read_text())["profile"] == manifest["profile"]


def test_custom_currency_validation_series_and_cli_diff(tmp_path, us_profile, capsys):
    cat = Catalog()
    pid, _ = cat.resolve(
        RawOffer(seller="shop", raw_name="DHT22 Sensor", url="https://test/sensor", currency="USD"),
        fuzzy_threshold=93,
    )
    offers = [
        OfferRecord(
            product_id=pid,
            seller="shop",
            raw_name="Sensor",
            url="https://test/sensor",
            price=p,
            currency=c,
            availability=Availability.IN_STOCK,
        )
        for p, c in [("0.02", "USD"), ("0.01", "EGP"), ("0.01", "JPY")]
    ]
    assert flag_offers(offers, profile=us_profile) == {"foreign_currency": 2}
    higher_floor = us_profile.model_copy(update={"minimum_price": Decimal("0.03")})
    assert flag_offers(offers, profile=higher_floor) == {
        "implausible_price": 1,
        "foreign_currency": 2,
    }
    store = ParquetStore(tmp_path, profile=us_profile)
    store.write_catalog(cat)
    for month, usd in [(1, 10), (2, 12)]:
        offers[0].price = usd
        offers[0].flags = []
        offers[1].flags = []  # historic unflagged foreign data must still be excluded
        store.write_run(
            Run(
                run_id=f"r{month}",
                ts=datetime(2025, month, 1, tzinfo=UTC),
                schema_version=1,
                pipeline_version="test",
                stores=[],
                offers=offers,
            )
        )
    series, stats = build_series(store.read_offers(), cat, profile=us_profile)
    assert len(series) == 2 and stats[0]["latest_min"] == 12
    assert reference_prices(store.read_offers(), cat, profile=us_profile) == {pid: [10, 12]}
    assert _diff(store, 5) == 0
    assert json.loads(capsys.readouterr().out)["top_moves"][0]["pct"] == 20


async def test_unknown_store_fails_before_any_requests(tmp_path, us_profile):
    with pytest.raises(ValueError, match="Unknown stores"):
        await run_pipeline(
            RunOptions(profile=us_profile, stores=["typo"], data_dir=tmp_path, ai=False)
        )
    assert not (tmp_path / "manifest.json").exists()


async def test_ai_uses_selected_country_context(tmp_path, us_profile):
    seen = []

    def respond(messages, info):
        seen.append(str(messages))
        text = str(messages[-1].parts[-1].content)
        key = json.loads(next(line for line in text.splitlines() if line.startswith("{")))["key"]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "items": [
                            {
                                "key": key,
                                "canonical_name": "Test Part",
                                "description": "A component.",
                                "tags": [],
                            }
                        ]
                    },
                )
            ]
        )

    cat = Catalog()
    cat.resolve(
        RawOffer(
            seller="us-shop", raw_name="Test Part", url="https://us.test/part", currency="USD"
        ),
        fuzzy_threshold=93,
    )
    report = await Enricher(
        ParquetStore(tmp_path, profile=us_profile),
        model=FunctionModel(respond),
        requests_per_minute=10000,
    ).enrich(cat)
    assert report.enriched == 1 and "United States (US)" in seen[0] and "currency: USD" in seen[0]
