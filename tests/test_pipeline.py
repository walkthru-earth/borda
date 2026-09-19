"""End-to-end: two mocked stores -> parquet history, catalog, series, stats, manifest, diagnostics."""

import json
from datetime import UTC, datetime

import httpx
import pyarrow.parquet as pq
import respx

from borda.pipeline import RunOptions, run_pipeline
from borda.scrapers import Store
from borda.storage import ParquetStore

SHOP = Store(slug="shop-a", name="A", base_url="https://a.test", platform="shopify")
WOO = Store(slug="shop-b", name="B", base_url="https://b.test", platform="woocommerce")
DEAD = Store(slug="shop-dead", name="Dead", base_url="https://dead.test", platform="shopify")


def _mock_stores(uno_price_b: str):
    respx.get(url__regex=r"https://a\.test/products\.json.*").side_effect = [
        httpx.Response(
            200,
            json={
                "products": [
                    {
                        "title": "Arduino UNO R3 (original)",
                        "handle": "uno",
                        "variants": [{"price": "450.00", "available": True}],
                        "body_html": "<p>The classic <b>ATmega328P</b> board. "
                        "<a href='/files/uno_datasheet.pdf'>Datasheet</a> "
                        "<a href='https://facebook.com/x'>fb</a></p>",
                    },
                    {
                        "title": "HC-SR04 Ultrasonic",
                        "handle": "sr04",
                        "variants": [{"price": "45.00", "available": False}],
                    },
                ]
            },
        ),
        httpx.Response(200, json={"products": []}),
    ]
    respx.get(url__regex=r"https://b\.test/wp-json/wc/store/v1/products.*").side_effect = [
        httpx.Response(
            200,
            json=[
                {
                    "name": "اردوينو اونو R3 CH340",
                    "permalink": "https://b.test/product/uno",
                    "prices": {
                        "price": uno_price_b,
                        "currency_code": "EGP",
                        "currency_minor_unit": 2,
                    },
                    "is_in_stock": True,
                    "categories": [{"name": "Arduino"}],
                },
            ],
        ),
        httpx.Response(200, json=[]),
    ]
    respx.get(url__regex=r"https://dead\.test/.*").mock(return_value=httpx.Response(503))


@respx.mock
async def test_two_runs_build_history_and_exports(tmp_path, monkeypatch):
    monkeypatch.setattr("borda.http.settings.max_retries", 0)
    data, diag_dir = tmp_path / "data", tmp_path / "diag"
    opts = RunOptions(
        stores=[SHOP.slug, WOO.slug, DEAD.slug],
        extra_stores=[SHOP, WOO, DEAD],
        ai=False,
        data_dir=data,
        diagnostics_dir=diag_dir,
    )

    _mock_stores("38000")
    diag1, code = await run_pipeline(
        RunOptions(
            **{
                **opts.__dict__,
                "run_id": "20250901T000000Z",
                "ts": datetime(2025, 9, 1, tzinfo=UTC),
                "cache_dir": tmp_path / "c1",
            }
        )
    )
    assert code == 0
    assert {s.seller: s.status.value for s in diag1.stores} == {
        "shop-a": "ok",
        "shop-b": "ok",
        "shop-dead": "failed",
    }
    assert (
        diag1.offers_kept == 3 and diag1.products_total == 2
    )  # uno merged across sellers + Arabic name
    assert diag1.errors and "shop-dead" in diag1.errors[0]

    respx.reset()
    _mock_stores("42000")
    diag2, code = await run_pipeline(
        RunOptions(
            **{
                **opts.__dict__,
                "run_id": "20251001T000000Z",
                "ts": datetime(2025, 10, 1, tzinfo=UTC),
                "cache_dir": tmp_path / "c2",
            }
        )
    )
    assert code == 0 and diag2.products_new == 0

    store = ParquetStore(data)
    assert store.run_ids() == ["20250901T000000Z", "20251001T000000Z"]
    offers = store.read_offers()
    assert offers.num_rows == 6
    cat = store.read_catalog()
    uno = cat.products["arduino-uno-r3"]
    assert uno.sellers == ["shop-a", "shop-b"] and "اردوينو اونو R3 CH340" in uno.raw_names

    assert str(uno.datasheet_url) == "https://a.test/files/uno_datasheet.pdf"
    listings = pq.read_table(data / "listings.parquet").to_pylist()
    row = next(
        r for r in listings if r["seller"] == "shop-a" and r["product_id"] == "arduino-uno-r3"
    )
    assert row["description"] == "The classic ATmega328P board. Datasheet fb"
    assert row["links"] == ["https://a.test/files/uno_datasheet.pdf"]

    stats = pq.read_table(data / "stats.parquet").to_pylist()
    s = next(r for r in stats if r["product_id"] == "arduino-uno-r3")
    assert (s["min"], s["max"], s["latest_min"], s["in_stock_sellers"]) == (380.0, 450.0, 420.0, 2)
    pts = pq.read_table(data / "series.parquet", filters=[("product_id", "=", "arduino-uno-r3")])
    assert pts.num_rows == 4
    assert uno.group == "dev-boards"

    manifest = json.loads((data / "manifest.json").read_text())
    assert [r["run_id"] for r in manifest["runs"]] == store.run_ids()
    assert manifest["offers_total"] == 6 and manifest["parquet_format"] == "2.6"
    assert (
        manifest["files"]["catalog.parquet"]["footer"] > 0
        and manifest["files"]["series.parquet"]["rows"] == 6
    )
    assert manifest["groups"] == {"dev-boards": 1, "sensors": 1}
    assert (diag_dir / "runs" / "20251001T000000Z.json").exists() and (
        diag_dir / "latest.json"
    ).exists()


@respx.mock
async def test_all_stores_failing_exits_nonzero_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("borda.http.settings.max_retries", 0)
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(503))
    diag, code = await run_pipeline(
        RunOptions(
            stores=[DEAD.slug],
            extra_stores=[DEAD],
            ai=False,
            data_dir=tmp_path / "d",
            diagnostics_dir=tmp_path / "g",
            cache_dir=tmp_path / "c",
        )
    )
    assert code == 1 and not (tmp_path / "d").exists()
    assert (tmp_path / "g" / "latest.json").exists()


@respx.mock
async def test_reindex_replays_history_and_keeps_enrichment(tmp_path, monkeypatch):
    from borda.enrich import ai as ai_mod
    from borda.models import Enrichment
    from borda.pipeline import reindex

    monkeypatch.setattr("borda.http.settings.max_retries", 0)
    data = tmp_path / "data"
    _mock_stores("38000")
    opts = RunOptions(
        stores=[SHOP.slug, WOO.slug],
        extra_stores=[SHOP, WOO],
        ai=False,
        embeddings=False,
        data_dir=data,
        diagnostics_dir=tmp_path / "g",
        cache_dir=tmp_path / "c",
        run_id="20250901T000000Z",
        ts=datetime(2025, 9, 1, tzinfo=UTC),
    )
    await run_pipeline(opts)
    store = ParquetStore(data)
    cat = store.read_catalog()
    # pretend the LLM renamed the ultrasonic sensor and cache that under the new id
    old_id = next(p.id for p in cat.products.values() if "sr04" in p.id or "ultrasonic" in p.id)
    e = Enrichment(
        key=old_id,
        canonical_name="HC-SR04 Ultrasonic Distance Sensor",
        description="d",
        tags=["sensor"],
    )
    cache = {"hc-sr04-ultrasonic-distance-sensor": e}
    store.write_enrichment(cache, "test", datetime(2025, 9, 1, tzinfo=UTC))
    cat.redirects[old_id] = "hc-sr04-ultrasonic-distance-sensor"
    p = cat.products.pop(old_id)
    p.id = "hc-sr04-ultrasonic-distance-sensor"
    p.enriched = True
    cat.products[p.id] = p
    # Old public URLs must remain valid across repeated reindexes, even when their
    # ids can no longer be recreated by today's normalization rules.
    cat.redirects["legacy-ultrasonic-link"] = p.id
    cat.redirects["older-ultrasonic-link"] = "legacy-ultrasonic-link"
    store.write_catalog(cat)

    monkeypatch.setattr(ai_mod.settings, "ai_enabled", False)
    points, products = await reindex(data, ai=False, embeddings=False)
    cat2 = store.read_catalog()
    assert products == 2 and points == 3
    assert "hc-sr04-ultrasonic-distance-sensor" in cat2.products  # cache re-applied via redirect
    assert cat2.products["hc-sr04-ultrasonic-distance-sensor"].enriched
    assert cat2.resolve_id(old_id) == "hc-sr04-ultrasonic-distance-sensor"
    assert cat2.resolve_id("older-ultrasonic-link") == "hc-sr04-ultrasonic-distance-sensor"
    assert (
        str(cat2.products["arduino-uno-r3"].datasheet_url)
        == "https://a.test/files/uno_datasheet.pdf"
    )
    assert all(v in cat2.products and k not in cat2.products for k, v in cat2.redirects.items())


@respx.mock
async def test_checkpoint_resumes_completed_stores(tmp_path, monkeypatch):
    monkeypatch.setattr("borda.http.settings.max_retries", 0)
    cache = tmp_path / "cache" / "http"
    common = dict(
        stores=[SHOP.slug, WOO.slug],
        extra_stores=[SHOP, WOO],
        ai=False,
        embeddings=False,
        data_dir=tmp_path / "data",
        diagnostics_dir=tmp_path / "g",
        cache_dir=cache,
        run_id="20250901T000000Z",
        ts=datetime(2025, 9, 1, tzinfo=UTC),
        checkpoint_key="test",
    )
    # attempt 1: shop-a completes, shop-b dies -> only shop-a is checkpointed
    respx.get(url__regex=r"https://a\.test/products\.json.*").side_effect = [
        httpx.Response(
            200,
            json={
                "products": [
                    {
                        "title": "Arduino UNO R3",
                        "handle": "uno",
                        "variants": [{"price": "450.00", "available": True}],
                    }
                ]
            },
        ),
        httpx.Response(200, json={"products": []}),
    ]
    respx.get(url__regex=r"https://b\.test/.*").mock(return_value=httpx.Response(503))
    diag1, code = await run_pipeline(RunOptions(**common))
    assert code == 0 and diag1.resumed_stores == []
    from borda.checkpoint import Checkpoint
    from borda.profiles import default_profile

    profile = default_profile().model_copy(
        update={"stores": [*default_profile().stores, SHOP, WOO]}
    )
    ckpt = Checkpoint(tmp_path / "cache" / "checkpoints", "test", profile=profile)
    assert not ckpt.dir.exists()  # successful run clears its checkpoint
    ckpt = Checkpoint(tmp_path / "cache2" / "checkpoints", "test", profile=profile)

    # simulate a crash after shop-a finished: re-create the checkpoint, then re-run
    from borda.models import RawOffer, ScrapeStatus, StoreReport

    offers = [
        RawOffer(
            seller="shop-a", raw_name="Arduino UNO R3", url="https://a.test/products/uno", price=450
        )
    ]

    ckpt.save_store(
        SHOP.slug, offers, StoreReport(seller="shop-a", status=ScrapeStatus.OK, offers=1, pages=1)
    )
    respx.clear()  # new attempt: a.test would now fail, b.test works
    a_route = respx.get(url__regex=r"https://a\.test/.*").mock(return_value=httpx.Response(500))
    respx.get(url__regex=r"https://b\.test/wp-json/wc/store/v1/products.*").side_effect = [
        httpx.Response(
            200,
            json=[
                {
                    "name": "HC-SR04",
                    "permalink": "https://b.test/product/sr04",
                    "prices": {"price": "4500", "currency_code": "EGP", "currency_minor_unit": 2},
                    "is_in_stock": True,
                    "categories": [],
                }
            ],
        ),
        httpx.Response(200, json=[]),
    ]
    diag2, code = await run_pipeline(
        RunOptions(
            **{
                **common,
                "run_id": "20251001T000000Z",
                "ts": datetime(2025, 10, 1, tzinfo=UTC),
                "cache_dir": tmp_path / "cache2" / "http",
                "checkpoint_key": "test",
            }
        )
    )
    assert code == 0
    assert diag2.resumed_stores == ["shop-a"]  # served from checkpoint, a.test never called
    assert a_route.call_count == 0
    assert {s.seller: s.status.value for s in diag2.stores} == {"shop-a": "ok", "shop-b": "ok"}
    assert not ckpt.dir.exists()


@respx.mock
async def test_reindex_keeps_existing_ids_and_cache_keys_stable(tmp_path, monkeypatch):
    """A replay must not re-slug products that already exist just because the cached official
    name differs from the seller name: URLs, embeddings and cache rows stay keyed the same."""
    from borda.enrich import ai as ai_mod
    from borda.models import Enrichment
    from borda.pipeline import reindex

    monkeypatch.setattr("borda.http.settings.max_retries", 0)
    data = tmp_path / "data"
    _mock_stores("38000")
    await run_pipeline(
        RunOptions(
            stores=[SHOP.slug, WOO.slug],
            extra_stores=[SHOP, WOO],
            ai=False,
            embeddings=False,
            data_dir=data,
            diagnostics_dir=tmp_path / "g",
            cache_dir=tmp_path / "c",
            run_id="20250901T000000Z",
            ts=datetime(2025, 9, 1, tzinfo=UTC),
        )
    )
    store = ParquetStore(data)
    cat = store.read_catalog()
    pid = next(p.id for p in cat.products.values() if "sr04" in p.id or "ultrasonic" in p.id)
    store.write_enrichment(
        {
            pid: Enrichment(
                key=pid,
                canonical_name="HC-SR04 Ultrasonic Distance Sensor",
                description="d",
                tags=["sensor"],
                brand="Circuits Electronics",  # a shop echoed back is not a brand
            )
        },
        "test",
        datetime(2025, 9, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(ai_mod.settings, "ai_enabled", False)
    await reindex(data, ai=False, embeddings=False)
    cat2 = store.read_catalog()
    p = cat2.products[pid]
    assert p.canonical_name == "HC-SR04 Ultrasonic Distance Sensor" and p.enriched
    assert p.brand is None
    assert pid in store.read_enrichment() and pid not in cat2.redirects
