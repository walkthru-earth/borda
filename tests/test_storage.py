import json
from datetime import UTC, datetime
from decimal import Decimal

import pyarrow.parquet as pq

from borda import SCHEMA_VERSION
from borda.models import Availability, OfferRecord, Run, ScrapeStatus, StoreReport
from borda.normalize import Catalog, flag_offers
from borda.storage import ParquetStore, build_index, build_series, reference_prices, search


def _run(run_id: str, ts: datetime, rows: list[tuple[str, str, float | None]]) -> Run:
    return Run(
        run_id=run_id,
        ts=ts,
        schema_version=SCHEMA_VERSION,
        pipeline_version="test",
        stores=[StoreReport(seller="s1", status=ScrapeStatus.OK, offers=len(rows))],
        offers=[
            OfferRecord(
                product_id=pid,
                seller=seller,
                raw_name=pid,
                url=f"https://{seller}.example/{pid}",
                price=price,
                availability=Availability.IN_STOCK,
            )
            for pid, seller, price in rows
        ],
    )


def test_parquet_history_is_partitioned_and_appends(tmp_path):
    store = ParquetStore(tmp_path)
    t1 = datetime(2025, 9, 1, tzinfo=UTC)
    t2 = datetime(2025, 10, 1, tzinfo=UTC)
    store.write_run(_run("r1", t1, [("esp32-devkit", "s1", 300.0), ("esp32-devkit", "s2", 320.0)]))
    store.write_run(_run("r2", t2, [("esp32-devkit", "s1", 350.0)]))

    assert (tmp_path / "offers/year=2025/month=09/r1.parquet").exists()
    assert (tmp_path / "offers/year=2025/month=10/r2.parquet").exists()
    assert (tmp_path / "store_runs/year=2025/month=10/r2.parquet").exists()
    meta = pq.read_metadata(tmp_path / "offers/year=2025/month=09/r1.parquet")
    assert meta.row_group(0).column(0).compression == "ZSTD"

    table = store.read_offers()
    assert table.num_rows == 3
    assert set(table.column_names) >= {"run_id", "ts", "product_id", "price", "year", "month"}
    assert store.run_ids() == ["r1", "r2"]


def test_catalog_roundtrip_keeps_redirects_and_local_names(tmp_path, make_offer):
    store = ParquetStore(tmp_path)
    cat = Catalog()
    a, _ = cat.resolve(
        make_offer("s1", "ESP32 Dev Kit", 300, image="https://img.example/a.jpg"),
        fuzzy_threshold=93,
    )
    b, _ = cat.resolve(make_offer("s2", "بورد ESP32 وايفاي", 310), fuzzy_threshold=93)
    cat.merge(a, b)
    store.write_catalog(cat)

    back = store.read_catalog()
    assert set(back.products) == {a}
    assert back.redirects == {b: a}
    assert "بورد ESP32 وايفاي" in back.products[a].raw_names
    assert str(back.products[a].image) == "https://img.example/a.jpg"
    assert back.resolve_id(b) == a


def test_series_and_stats_follow_redirects_and_skip_outliers(tmp_path, make_offer):
    store = ParquetStore(tmp_path)
    cat = Catalog()
    a, _ = cat.resolve(make_offer("s1", "DHT22 sensor", 120), fuzzy_threshold=93)
    old = "dht22-old-id"
    cat.redirects[old] = a
    t1, t2 = datetime(2025, 9, 1, tzinfo=UTC), datetime(2025, 10, 1, tzinfo=UTC)
    r1 = _run("r1", t1, [(old, "s1", 100.0), (a, "s2", 110.0)])
    r2 = _run("r2", t2, [(a, "s1", 120.0), (a, "s2", 9999.0)])
    r2.offers[1].flags = ["outlier_high"]
    store.write_run(r1)
    store.write_run(r2)

    series, stats = build_series(store.read_offers(), cat)
    assert {p["product_id"] for p in series} == {a}
    assert len(series) == 3  # outlier excluded from charts, kept in history
    st = stats[0]
    assert (st["min"], st["max"], st["median"]) == (100.0, 120.0, 110.0)
    assert st["latest_min"] == 120.0 and st["in_stock_sellers"] == 1

    n_points = store.write_series(series)
    store.write_stats(stats)
    assert n_points == 3
    pts = pq.read_table(tmp_path / "series.parquet", filters=[("product_id", "=", a)])
    assert pts.num_rows == 3
    meta = pq.read_metadata(tmp_path / "series.parquet")
    assert meta.row_group(0).sorting_columns[0].column_index == 0  # sorted by product_id
    assert meta.row_group(0).column(0).statistics.has_min_max
    assert reference_prices(store.read_offers(), cat) == {a: [100.0, 110.0, 120.0]}


def test_flag_offers_marks_outliers_and_missing_prices():
    offers = [
        OfferRecord(
            product_id="x", seller="a", raw_name="x", url="https://a/x", price=Decimal(100)
        ),
        OfferRecord(
            product_id="x", seller="b", raw_name="x", url="https://b/x", price=Decimal(105)
        ),
        OfferRecord(product_id="x", seller="c", raw_name="x", url="https://c/x", price=Decimal(95)),
        OfferRecord(
            product_id="x", seller="d", raw_name="x", url="https://d/x", price=Decimal(5000)
        ),
        OfferRecord(product_id="x", seller="e", raw_name="x", url="https://e/x", price=None),
    ]
    counts = flag_offers(offers, factor=8)
    assert counts == {"outlier_high": 1, "missing_price": 1}
    assert offers[3].flags == ["outlier_high"]


def test_search_finds_official_product_by_local_name(make_offer):
    cat = Catalog()
    pid, _ = cat.resolve(
        make_offer("s1", "Ultrasonic Distance Sensor HC-SR04", 45), fuzzy_threshold=93
    )
    cat.products[pid].raw_names = [*cat.products[pid].raw_names, "حساس الترا سونيك hcsr04"]
    idx = build_index(cat.sorted_products())
    assert search(idx, "hcsr04")[0][0] == pid
    assert search(idx, "الترا سونيك")[0][0] == pid
    assert search(idx, "ultras")[0][0] == pid  # prefix
    assert search(idx, "esp32") == []


def test_subset_runs_keep_untouched_sellers_and_export_current_offers(tmp_path, make_offer):
    cat = Catalog()
    a, _ = cat.resolve(make_offer("s1", "DHT22 sensor", 100), fuzzy_threshold=93)
    b, _ = cat.resolve(make_offer("s2", "Arduino Uno R3", 400), fuzzy_threshold=93)
    store = ParquetStore(tmp_path)
    t1, t2 = datetime(2025, 9, 1, tzinfo=UTC), datetime(2025, 10, 1, tzinfo=UTC)
    store.write_run(_run("r1", t1, [(a, "s1", 100), (b, "s2", 400)]))
    store.write_run(_run("r2", t2, [(a, "s1", 120)]))
    _, stats = build_series(store.read_offers(), cat)
    rows = {row["product_id"]: row for row in stats}
    assert rows[b]["latest_min"] == 400 and rows[b]["in_stock_sellers"] == 1
    assert rows[b]["latest_ts"] == t1
    assert rows[b]["group"] == "dev-boards"
    assert json.loads(rows[a]["current_offers"]) == [
        {
            "ts": t2.isoformat(),
            "seller": "s1",
            "price": 120,
            "currency": "EGP",
            "availability": "in_stock",
            "url": f"https://s1.example/{a}",
        }
    ]


def test_split_history_follows_listing_ownership(tmp_path, make_offer):
    cat = Catalog()
    a, _ = cat.resolve(make_offer("s1", "10k resistor", 10), fuzzy_threshold=93)
    b, _ = cat.resolve(make_offer("s2", "100k resistor", 20), fuzzy_threshold=93)
    store = ParquetStore(tmp_path)
    run = _run("r1", datetime(2025, 9, 1, tzinfo=UTC), [(a, "s1", 10), (a, "s2", 20)])
    run.offers[0].url = next(iter(cat.products[a].listings.values()))
    run.offers[1].url = next(iter(cat.products[b].listings.values()))
    store.write_run(run)
    series, stats = build_series(store.read_offers(), cat)
    assert {p["seller"]: p["product_id"] for p in series} == {"s1": a, "s2": b}
    assert {p["product_id"]: p["latest_min"] for p in stats} == {a: 10, b: 20}
    assert reference_prices(store.read_offers(), cat) == {a: [10], b: [20]}


def test_foreign_prices_do_not_pollute_egp_stats_or_outlier_detection(tmp_path, make_offer):
    cat = Catalog()
    pid, _ = cat.resolve(make_offer("s1", "DHT22 sensor", 100), fuzzy_threshold=93)
    run = _run(
        "r1",
        datetime(2025, 9, 1, tzinfo=UTC),
        [
            (pid, "s1", 100),
            (pid, "s2", 2),
            (pid, "s3", 3),
        ],
    )
    for offer in run.offers[1:]:
        offer.currency = "USD"
    assert flag_offers(run.offers) == {"foreign_currency": 2}
    store = ParquetStore(tmp_path)
    store.write_run(run)
    series, stats = build_series(store.read_offers(), cat)
    assert len(series) == 1
    assert stats[0]["latest_min"] == 100
    assert reference_prices(store.read_offers(), cat) == {pid: [100]}


def test_enrichment_cache_roundtrip_keeps_product_details(tmp_path):
    from borda.models import Enrichment

    store = ParquetStore(tmp_path)
    enrichment = Enrichment(
        key="part",
        canonical_name="TP4056 Charger",
        description="Battery charger.",
        specs=["Input: 5V"],
        mpn="TP4056",
        tags=["charger"],
        group="power",
    )
    store.write_enrichment({"part": enrichment}, "test", datetime(2025, 9, 1, tzinfo=UTC))
    assert store.read_enrichment()["part"] == enrichment


def test_rebuild_manifest_keeps_unchanged_exports(tmp_path, make_offer):
    from borda.pipeline import rebuild_exports

    cat = Catalog()
    pid, _ = cat.resolve(make_offer("s1", "DHT22 sensor", 100), fuzzy_threshold=93)
    store = ParquetStore(tmp_path)
    store.write_catalog(cat)
    store.write_listings({})
    store.write_embeddings({pid: (bytes(384), "hash")}, "test")
    store.write_run(_run("r1", datetime(2025, 9, 1, tzinfo=UTC), [(pid, "s1", 100)]))
    expected = {
        key: value
        for key, value in store.written.items()
        if key
        in {
            "listings.parquet",
            "embeddings.parquet",
        }
    }
    rebuild_exports(tmp_path, embeddings=False)
    files = json.loads((tmp_path / "manifest.json").read_text())["files"]
    assert all(files[key] == value for key, value in expected.items())
