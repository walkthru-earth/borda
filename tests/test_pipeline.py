"""End-to-end: two mocked stores -> parquet history, catalog, series, stats, manifest, diagnostics."""

import json
from datetime import UTC, datetime

import httpx
import pyarrow.parquet as pq
import respx

from egmarket.pipeline import RunOptions, run_pipeline
from egmarket.scrapers import Store
from egmarket.storage import ParquetStore

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
    monkeypatch.setattr("egmarket.http.settings.max_retries", 0)
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

    stats = pq.read_table(data / "stats.parquet").to_pylist()
    s = next(r for r in stats if r["product_id"] == "arduino-uno-r3")
    assert (s["min"], s["max"], s["latest_min"], s["in_stock_sellers"]) == (380.0, 450.0, 420.0, 2)
    pts = pq.read_table(data / "series/bucket=ar/points.parquet")
    assert pts.num_rows == 4

    manifest = json.loads((data / "manifest.json").read_text())
    assert [r["run_id"] for r in manifest["runs"]] == store.run_ids()
    assert manifest["offers_total"] == 6 and manifest["parquet_format"] == "2.6"
    assert "catalog.parquet" in manifest["files"]
    assert (diag_dir / "runs" / "20251001T000000Z.json").exists() and (
        diag_dir / "latest.json"
    ).exists()


@respx.mock
async def test_all_stores_failing_exits_nonzero_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("egmarket.http.settings.max_retries", 0)
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
