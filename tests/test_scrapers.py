"""Scrapers against mocked HTTP (respx) – no network."""

import json

import httpx
import pytest
import respx

from egmarket.http import Fetcher
from egmarket.models import Availability, ScrapeStatus
from egmarket.scrapers import BY_SLUG, PLATFORMS, Store, build_scraper
from egmarket.scrapers.stores import STORES


@pytest.fixture
def fetcher(tmp_path):
    return Fetcher(cache_dir=tmp_path / "cache", delay_s=0, retries=0)


def test_every_enabled_store_has_a_supported_platform():
    for s in STORES:
        if s.enabled:
            assert s.platform in PLATFORMS, s.slug
    assert len(BY_SLUG) == len(STORES)


@respx.mock
async def test_shopify_scraper_picks_cheapest_variant(fetcher):
    store = Store(slug="shop", name="Shop", base_url="https://shop.test", platform="shopify")
    page1 = {
        "products": [
            {
                "title": "ESP32 DevKit V1",
                "handle": "esp32-devkit-v1",
                "vendor": "Espressif",
                "product_type": "Dev Boards",
                "tags": ["esp32", "wifi"],
                "images": [{"src": "https://cdn.test/esp32.jpg"}],
                "variants": [
                    {"title": "38 pin", "price": "350.00", "available": False, "sku": "A"},
                    {"title": "30 pin", "price": "300.00", "available": True, "sku": "B"},
                ],
            }
        ]
    }
    respx.get("https://shop.test/products.json", params={"limit": 250, "page": 1}).mock(
        return_value=httpx.Response(200, json=page1)
    )
    respx.get("https://shop.test/products.json", params={"limit": 250, "page": 2}).mock(
        return_value=httpx.Response(200, json={"products": []})
    )
    offers, report = await build_scraper(store, fetcher, max_pages=5).run()
    assert report.status == ScrapeStatus.OK and report.offers == 1
    o = offers[0]
    assert str(o.price) == "300.00" and o.availability == Availability.IN_STOCK
    assert o.extra == {"variant": "30 pin"} and o.brand == "Espressif"
    assert str(o.url) == "https://shop.test/products/esp32-devkit-v1"


@respx.mock
async def test_woocommerce_minor_units_and_stock(fetcher):
    store = Store(slug="woo", name="Woo", base_url="https://woo.test", platform="woocommerce")
    products = [
        {
            "name": "Arduino Uno R3 <span>(SMD)</span>",
            "permalink": "https://woo.test/product/uno",
            "prices": {"price": "45000", "currency_code": "EGP", "currency_minor_unit": 2},
            "is_in_stock": False,
            "categories": [{"name": "Boards"}, {"name": "Arduino"}],
            "tags": [],
            "images": [],
            "type": "simple",
            "sku": "UNO-R3",
        }
    ]
    route = respx.get(url__regex=r"https://woo\.test/wp-json/wc/store/v1/products.*")
    route.side_effect = [httpx.Response(200, json=products), httpx.Response(200, json=[])]
    offers, report = await build_scraper(store, fetcher, max_pages=5).run()
    assert report.status == ScrapeStatus.OK
    o = offers[0]
    assert o.raw_name == "Arduino Uno R3 (SMD)"
    assert str(o.price) == "450.00" and o.availability == Availability.OUT_OF_STOCK
    assert o.category == "Arduino" and o.store_tags == ["Boards", "Arduino"]


@respx.mock
async def test_prestashop_out_of_stock_when_no_cart_url(fetcher):
    store = Store(
        slug="ps",
        name="PS",
        base_url="https://ps.test/en",
        platform="prestashop",
        params={"listing": "2-home"},
    )
    payload = {
        "pagination": {"pages_count": 1},
        "products": [
            {"name": "NE555", "url": "https://ps.test/en/1-ne555.html", "price_amount": 12.5, "add_to_cart_url": "x", "reference": "R1", "cover": {"large": {"url": "https://ps.test/1.jpg"}}},
            {"name": "LM358", "url": "https://ps.test/en/2-lm358.html", "price_amount": 9, "add_to_cart_url": None},
        ],
    }  # fmt: skip
    respx.get(url__regex=r"https://ps\.test/en/2-home.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    offers, report = await build_scraper(store, fetcher, max_pages=5).run()
    assert report.status == ScrapeStatus.OK and len(offers) == 2
    assert offers[0].availability == Availability.IN_STOCK
    assert offers[1].availability == Availability.OUT_OF_STOCK


@respx.mock
async def test_odoo_html_cards(fetcher):
    store = Store(slug="odoo", name="Odoo", base_url="https://odoo.test", platform="odoo")
    card = """
    <td class="oe_product"><form itemscope itemtype="http://schema.org/Product">
      <a itemprop="url" href="/shop/uno-r3-123?page=1"><img itemprop="image" src="/web/image/1"/></a>
      <span class="o_ribbon">Out of stock</span>
      <h6><a itemprop="name" href="/shop/uno-r3-123?page=1" content="Arduino Uno R3">Arduino Uno R3</a></h6>
      <span itemprop="price" content="450.0"><span class="oe_currency_value">450.00</span></span>
      <span itemprop="priceCurrency" content="EGP"/>
    </form></td>"""
    respx.get("https://odoo.test/shop/page/1").mock(
        return_value=httpx.Response(200, text=f"<table>{card}</table>")
    )
    respx.get("https://odoo.test/shop/page/2").mock(
        return_value=httpx.Response(200, text=f"<table>{card}</table>")
    )
    offers, report = await build_scraper(store, fetcher, max_pages=5).run()
    assert (
        report.status == ScrapeStatus.OK and len(offers) == 1
    )  # repeated last page stops the loop
    o = offers[0]
    assert str(o.url) == "https://odoo.test/shop/uno-r3-123"
    assert o.availability == Availability.OUT_OF_STOCK and str(o.price) == "450.00"


@respx.mock
async def test_wix_sitemap_and_jsonld(fetcher):
    store = Store(
        slug="wix",
        name="Wix",
        base_url="https://wix.test",
        platform="wix",
        params={"max_products": 10},
    )
    respx.get("https://wix.test/store-products-sitemap.xml").mock(
        return_value=httpx.Response(
            200, text="<urlset><url><loc>https://wix.test/product-page/relay</loc></url></urlset>"
        )
    )
    ld = {
        "@type": "Product",
        "name": "5V Relay Module",
        "sku": "R5",
        "image": [{"@type": "ImageObject", "contentUrl": "https://static.wix.test/r.jpg"}],
        "offers": {
            "@type": "Offer",
            "price": "65",
            "priceCurrency": "EGP",
            "availability": "https://schema.org/InStock",
        },
    }
    respx.get("https://wix.test/product-page/relay").mock(
        return_value=httpx.Response(
            200, text=f'<html><script type="application/ld+json">{json.dumps(ld)}</script></html>'
        )
    )
    offers, report = await build_scraper(store, fetcher, max_pages=5).run()
    assert report.status == ScrapeStatus.OK and len(offers) == 1
    assert str(offers[0].image) == "https://static.wix.test/r.jpg"
    assert str(offers[0].price) == "65.00"


@respx.mock
async def test_store_failure_is_isolated_and_partial_results_kept(fetcher):
    store = Store(slug="shop", name="Shop", base_url="https://shop.test", platform="shopify")
    ok = {
        "products": [
            {"title": "Widget", "handle": "x", "variants": [{"price": "1.00", "available": True}]}
        ]
        * 250
    }
    route = respx.get(url__regex=r"https://shop\.test/products\.json.*")
    route.side_effect = [httpx.Response(200, json=ok), httpx.Response(500)]
    offers, report = await build_scraper(store, fetcher, max_pages=5).run()
    assert report.status == ScrapeStatus.PARTIAL
    assert report.offers == 250 and "HTTP 500" in report.error


@respx.mock
async def test_invalid_rows_are_dropped_not_fatal(fetcher):
    store = Store(slug="shop", name="Shop", base_url="https://shop.test", platform="shopify")
    page = {
        "products": [
            {"title": "", "handle": "bad", "variants": [{"price": "1.00"}]},
            {"title": "Good", "handle": "good", "variants": [{"price": "2.00", "available": True}]},
        ]
    }
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json=page))
    offers, report = await build_scraper(store, fetcher, max_pages=1).run()
    assert report.status == ScrapeStatus.OK and len(offers) == 1 and len(report.warnings) == 1
