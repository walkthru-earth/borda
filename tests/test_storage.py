from datetime import UTC, datetime
from decimal import Decimal

import pyarrow.parquet as pq

from egmarket import SCHEMA_VERSION
from egmarket.models import Availability, OfferRecord, Run, ScrapeStatus, StoreReport
from egmarket.normalize import Catalog, flag_offers
from egmarket.storage import ParquetStore, build_index, build_series, reference_prices, search


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
    assert meta.row_group(0).column(0).compression == "SNAPPY"

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
