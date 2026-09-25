"""Mecha-tronx JSON API scraper against mocked HTTP (respx), no network."""

import httpx
import pytest
import respx

from borda.http import Fetcher
from borda.models import Availability, ScrapeStatus
from borda.scrapers import BY_SLUG, PLATFORMS, Store, build_scraper

BASE = "https://mt.test"
LIST = f"{BASE}/api/products"


@pytest.fixture
def fetcher(tmp_path):
    return Fetcher(cache_dir=tmp_path / "cache", delay_s=0, retries=0)


@pytest.fixture
def store():
    return Store(slug="mecha-tronx", name="Mecha-tronx", base_url=BASE, platform="mechatronx")


def _item(pid, slug, **kw):
    base = {
        "id": pid,
        "name": slug.replace("-", " ").title(),
        "nameAr": "منتج",
        "slug": slug,
        "image": f"{BASE}/storage/{slug}.jpg",
        "price": 100,
        "originalPrice": None,
        "currency": "EGP",
        "priceTiers": [],
        "inStock": True,
        "availableOnline": True,
        "comingSoon": False,
        "isVariantParent": False,
        "category": "Arduino Boards",
        "categoryAr": "لوحات أردوينو",
    }
    return {**base, **kw}


def _page(items, page, last):
    return {
        "success": True,
        "data": items,
        "meta": {"current_page": page, "last_page": last, "per_page": 100, "total": 9},
        "region": "EG",
    }


def _mock_list(pages):
    last = len(pages)
    routes = []
    for n, items in enumerate(pages, start=1):
        routes.append(
            respx.get(LIST, params={"per_page": 100, "page": n}).mock(
                return_value=httpx.Response(200, json=_page(items, n, last))
            )
        )
    return routes


def test_platform_registered_for_profile_store():
    assert PLATFORMS["mechatronx"].platform == "mechatronx"
    assert BY_SLUG["mecha-tronx"].platform == "mechatronx"


@respx.mock
async def test_paginates_until_last_page_and_maps_price_and_stock(fetcher, store):
    routes = _mock_list(
        [
            [
                _item(1, "arduino-uno-r3", price=350, originalPrice=380),
                _item(2, "uno-cable", inStock=False),
            ],
            [
                _item(3, "screwdriver-set", availableOnline=False, inStock=True),
                _item(4, "soldering-stand", comingSoon=True, inStock=False),
                _item(5, "bms-3s", price=0, inStock=False, category=None, categoryAr=None),
            ],
        ]
    )
    beyond = respx.get(LIST, params={"per_page": 100, "page": 3}).mock(
        return_value=httpx.Response(200, json=_page([], 3, 2))
    )
    offers, report = await build_scraper(store, fetcher, max_pages=10).run()

    assert report.status == ScrapeStatus.OK and report.pages == 2 and report.offers == 5
    assert all(r.called for r in routes) and not beyond.called
    by = {o.url.path: o for o in offers}

    uno = by["/product/arduino-uno-r3"]
    assert str(uno.price) == "350.00" and uno.currency == "EGP"
    assert uno.availability == Availability.IN_STOCK
    assert uno.category == "Arduino Boards"
    assert uno.store_tags == ["Arduino Boards", "لوحات أردوينو"]
    assert uno.extra == {"mechatronx_id": 1, "on_sale": True}

    assert by["/product/uno-cable"].availability == Availability.OUT_OF_STOCK
    counter = by["/product/screwdriver-set"]
    assert counter.availability == Availability.IN_STOCK
    assert counter.extra["available_online"] is False
    soon = by["/product/soldering-stand"]
    assert soon.availability == Availability.PREORDER and soon.extra["coming_soon"] is True
    unpriced = by["/product/bms-3s"]
    assert unpriced.price is None and unpriced.store_tags == [] and unpriced.category is None


@respx.mock
async def test_max_pages_caps_listing(fetcher, store):
    _mock_list([[_item(1, "a-1")], [_item(2, "b-2")], [_item(3, "c-3")]])
    offers, report = await build_scraper(store, fetcher, max_pages=1).run()
    assert report.pages == 1 and [o.url.path for o in offers] == ["/product/a-1"]


@respx.mock
async def test_variant_parent_emits_one_offer_per_variation(fetcher, store):
    parent = _item(
        3362,
        "transparent-led-5mm",
        name="Transparent LED 5mm",
        price=0.5,
        inStock=False,
        isVariantParent=True,
        category="LEDs & LED Modules",
        categoryAr="ليدات",
    )
    _mock_list([[parent, _item(452, "arduino-uno-r3")]])
    detail = {
        "success": True,
        "data": {
            "id": 3362,
            "slug": "transparent-led-5mm",
            "name": "Transparent LED 5mm",
            "description": (
                "<p>Full product description goes here. Basic HTML tags are preserved.</p>"
            ),
            "shortDescription": (
                '<p>5mm clear LED. <a href="https://cdn.test/led-datasheet.pdf">Datasheet</a></p>'
            ),
            "image": f"{BASE}/storage/parent.png",
            "category": {"id": 539, "name": "LEDs & LED Modules", "nameAr": "ليدات"},
            "categories": [
                {"id": 539, "name": "LEDs & LED Modules", "nameAr": "ليدات"},
                {"id": 535, "name": "Optoelectronics", "nameAr": "إلكترونيات ضوئية"},
            ],
            "isVariantParent": True,
            "variantAttributes": {"Colour": ["Yellow", "Green"]},
            "variations": [
                {
                    "id": 68,
                    "sku": "55751 ",
                    "name": "(Yellow LED 5mm (Transparent",
                    "attributes": {"Colour": "Yellow"},
                    "price": 0.5,
                    "currency": "EGP",
                    "inStock": True,
                    "availableOnline": True,
                    "comingSoon": False,
                    "image": f"{BASE}/storage/yellow.png",
                },
                {
                    "id": 2720,
                    "sku": "55748",
                    "name": "Green Led 5mm (Transparent)",
                    "attributes": {"Colour": "Green"},
                    "price": 0.75,
                    "currency": "EGP",
                    "inStock": False,
                    "availableOnline": True,
                    "comingSoon": False,
                    "image": None,
                },
            ],
        },
    }
    route = respx.get(f"{BASE}/api/products/transparent-led-5mm").mock(
        return_value=httpx.Response(200, json=detail)
    )
    offers, report = await build_scraper(store, fetcher, max_pages=5).run()
    assert route.call_count == 1 and report.offers == 3
    variants = [o for o in offers if "parent_slug" in o.extra]
    assert {o.url.path for o in variants} == {"/product/68", "/product/2720"}
    assert len({o.listing_key for o in offers}) == 3

    yellow, green = sorted(variants, key=lambda o: o.url.path, reverse=True)
    # Child names that spell out their attribute are kept as the seller wrote them.
    assert yellow.raw_name == "(Yellow LED 5mm (Transparent"
    assert green.raw_name == "Green Led 5mm (Transparent)"
    assert yellow.availability == Availability.IN_STOCK
    assert green.availability == Availability.OUT_OF_STOCK
    assert str(green.price) == "0.75" and yellow.sku == "55751"
    assert str(green.image) == f"{BASE}/storage/parent.png"
    assert yellow.extra["variant"] == {"Colour": "Yellow"}
    assert yellow.extra["variant_name"] == "(Yellow LED 5mm (Transparent"
    assert "variable" not in yellow.extra
    assert yellow.category == "LEDs & LED Modules"
    assert yellow.store_tags == [
        "LEDs & LED Modules",
        "ليدات",
        "Optoelectronics",
        "إلكترونيات ضوئية",
    ]
    # English placeholder copy is dropped and the real short description wins.
    assert yellow.description == "5mm clear LED. Datasheet"
    assert yellow.links == ["https://cdn.test/led-datasheet.pdf"]


@respx.mock
async def test_placeholder_only_description_is_missing(fetcher, store):
    parent = _item(9, "resistor-2w", name="Resistor 2W", isVariantParent=True, inStock=False)
    _mock_list([[parent]])
    respx.get(f"{BASE}/api/products/resistor-2w").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 9,
                    "name": "Resistor 2W",
                    "description": "<p>Full product description goes here.</p>",
                    "shortDescription": "Short product summary",
                    "meta": {"description": "Short product summary"},
                    "variations": [
                        {"id": 31280, "attributes": {"Resistance": "0.33Ω"}, "price": 0.75},
                        {"id": 31035, "attributes": {"Resistance": "1"}, "price": 0.75},
                        {
                            "id": 1979,
                            "name": "Resistor 8.2 ohm - 5W",
                            "attributes": {"Resistance": "4.7k"},
                            "price": 1.5,
                        },
                    ],
                },
            },
        )
    )
    offers, _ = await build_scraper(store, fetcher, max_pages=5).run()
    assert [o.description for o in offers] == [None, None, None]
    assert offers[0].raw_name == "Resistor 2W - 0.33Ω"
    # A value that also occurs inside the parent name is still spelled out.
    assert offers[1].raw_name == "Resistor 2W - 1"
    # A child name that misses its own attribute value is not trusted.
    assert offers[2].raw_name == "Resistor 2W - 4.7k"
    assert offers[0].availability == Availability.UNKNOWN


@respx.mock
async def test_variant_parent_without_detail_is_flagged_not_sold_out(fetcher, store):
    parent = _item(7, "power-supply", price=289, inStock=False, isVariantParent=True)
    _mock_list([[parent]])
    respx.get(f"{BASE}/api/products/power-supply").mock(return_value=httpx.Response(503))
    offers, report = await build_scraper(store, fetcher, max_pages=5).run()
    assert report.status == ScrapeStatus.OK and len(offers) == 1
    o = offers[0]
    assert o.extra["variable"] is True and o.availability == Availability.UNKNOWN
    assert str(o.url) == f"{BASE}/product/power-supply" and str(o.price) == "289.00"
    assert any("power-supply" in w for w in report.warnings)
