"""Scrapers against mocked HTTP (respx) – no network."""

import html
import json

import httpx
import pytest
import respx

from borda.http import Fetcher
from borda.models import Availability, ScrapeStatus
from borda.scrapers import BY_SLUG, PLATFORMS, Store, build_scraper
from borda.scrapers.docs import extract_doc_links
from borda.scrapers.stores import STORES


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
async def test_elghazawy_scrapes_only_configured_categories(fetcher):
    store = Store(
        slug="elghazawy",
        name="ElGhazawy",
        base_url="https://elghazawy.test",
        platform="elghazawy",
        params={"categories": ["maintenance-tools", "electricity-connectors"]},
    )

    def card(pid, name, price, *, sold=False):
        label = "Sold Out" if sold else ""
        return f"""
        <div class="product_card" id="product-{pid}">
          <a class="flash-sale-card-wrap-anchor"
             href="https://elghazawy.test/en/product/{pid}/{name.replace(" ", "+")}">
            <div class="pro-frame">
              <img data-src="https://cdn.test/{pid}.webp" />
              <div class="sale-sold-label">{label}</div>
              <p>{name}</p><h4>{price} EGP</h4>
            </div>
          </a>
        </div>"""

    maintenance = card("10", "Soldering Iron", "1,250") + card("11", "Crimp Tool", "300")
    connectors = card("11", "Crimp Tool", "300") + card(
        "12", "Terminal Connector", "25.50", sold=True
    )
    maintenance_route = respx.get("https://elghazawy.test/en/sub-category/maintenance-tools").mock(
        return_value=httpx.Response(200, text=maintenance)
    )
    connectors_route = respx.get(
        "https://elghazawy.test/en/sub-category/electricity-connectors"
    ).mock(return_value=httpx.Response(200, text=connectors))

    offers, report = await build_scraper(store, fetcher, max_pages=5).run()

    assert report.status == ScrapeStatus.OK and report.pages == 2 and len(offers) == 3
    assert maintenance_route.called and connectors_route.called
    assert str(offers[0].price) == "1250.00"
    assert offers[0].category == "Maintenance Tools"
    assert offers[0].store_tags == ["maintenance-tools"]
    assert offers[0].availability == Availability.UNKNOWN
    assert offers[2].availability == Availability.OUT_OF_STOCK
    assert offers[2].extra["source_category"] == "electricity-connectors"


@respx.mock
async def test_elghazawy_requires_an_explicit_category_allowlist(fetcher):
    store = Store(
        slug="elghazawy",
        name="ElGhazawy",
        base_url="https://elghazawy.test",
        platform="elghazawy",
    )
    offers, report = await build_scraper(store, fetcher, max_pages=5).run()
    assert offers == [] and report.status == ScrapeStatus.FAILED
    assert "params.categories" in report.error


def _locafy_snapshot(products, *, page=1, more=False):
    encoded = [[p, {"s": "arr"}] for p in products]
    return json.dumps(
        {
            "data": {
                "perPage": 24,
                "page": page,
                "hasMorePages": more,
                "loadedProducts": [[encoded, {"s": "arr"}], {"s": "arr"}],
            },
            "memo": {"name": "product.product-listing"},
        },
        separators=(",", ":"),
    )


@respx.mock
async def test_locafy_livewire_catalog(fetcher):
    store = Store(
        slug="electra",
        name="Electra",
        base_url="https://electra.test",
        platform="locafy",
        params={"per_page": 96},
    )
    first = [
        {
            "id": 1,
            "name": "ESP32 DevKit V1",
            "slug": "esp32-devkit-v1",
            "sku": "  ESP-1 ",
            "price": "350.00",
            "special_price": "300.00",
            "image_url": "/storage/esp.jpg",
            "stock_status": "in_stock",
        }
    ]
    second = first + [
        {
            "id": 2,
            "name": "LM358 DIP",
            "slug": "lm358-dip",
            "sku": "LM358",
            "price": "15.00",
            "special_price": None,
            "image_url": "/storage/lm.jpg",
            "stock_status": "out_of_stock",
        }
    ]
    initial = _locafy_snapshot(first, more=True)
    page = (
        f'<div wire:snapshot="{html.escape(initial, quote=True)}"></div>'
        '<script data-csrf="token"></script>'
    )
    respx.get("https://electra.test/products").mock(return_value=httpx.Response(200, text=page))
    update = respx.post("https://electra.test/livewire/update", headers={"X-Livewire": "true"})
    update.side_effect = [
        httpx.Response(
            200, json={"components": [{"snapshot": _locafy_snapshot(first, more=True)}]}
        ),
        httpx.Response(
            200,
            json={"components": [{"snapshot": _locafy_snapshot(second, page=2, more=False)}]},
        ),
    ]

    offers, report = await build_scraper(store, fetcher, max_pages=5).run()

    assert report.status == ScrapeStatus.OK and report.pages == 2 and len(offers) == 2
    assert str(offers[0].price) == "300.00"
    assert offers[0].availability == Availability.IN_STOCK and offers[0].sku == "ESP-1"
    assert str(offers[0].image) == "https://electra.test/storage/esp.jpg"
    assert offers[1].availability == Availability.OUT_OF_STOCK
    assert update.call_count == 2
    assert update.calls[0].request.read()
    assert b'"perPage":96' in update.calls[0].request.content
    assert b'"method":"loadMore"' in update.calls[1].request.content


@respx.mock
async def test_wix_storefront_graphql_api(fetcher):
    store = Store(slug="wix", name="Wix", base_url="https://wix.test", platform="wix")
    respx.get("https://wix.test/_api/v2/dynamicmodel").mock(
        return_value=httpx.Response(
            200, json={"apps": {"1380b703-ce81-ff05-f115-39571d94dfcd": {"instance": "tok"}}}
        )
    )
    gql = respx.post(
        "https://wix.test/_api/wix-ecommerce-storefront-web/api", headers={"Authorization": "tok"}
    )
    gql.side_effect = [
        httpx.Response(
            200,
            json={
                "data": {
                    "catalog": {
                        "category": {
                            "productsWithMetaData": {
                                "totalCount": 2,
                                "list": [
                                    {
                                        "name": "5V Relay Module",
                                        "urlPart": "relay",
                                        "sku": "R5",
                                        "price": 80.0,
                                        "discountedPrice": 65.0,
                                        "isInStock": True,
                                        "inventory": {"status": "in_stock"},
                                        "brand": None,
                                        "ribbon": "Sale",
                                        "media": [{"fullUrl": "https://static.wix.test/r.jpg"}],
                                    },
                                    {
                                        "name": "LDR",
                                        "urlPart": "ldr",
                                        "price": 5.0,
                                        "discountedPrice": 5.0,
                                        "isInStock": False,
                                        "inventory": {"status": "out_of_stock"},
                                        "media": [],
                                    },
                                ],
                            }
                        }
                    }
                }
            },
        ),
    ]
    offers, report = await build_scraper(store, fetcher, max_pages=5).run()
    assert report.status == ScrapeStatus.OK and len(offers) == 2 and report.pages == 1
    assert str(offers[0].price) == "65.00" and offers[0].extra == {"ribbon": "Sale"}
    assert str(offers[0].url) == "https://wix.test/product-page/relay"
    assert str(offers[0].image) == "https://static.wix.test/r.jpg"
    assert offers[1].availability == Availability.OUT_OF_STOCK
    assert gql.call_count == 1  # 2 of 2 products fetched -> no second page


@respx.mock
async def test_wix_falls_back_to_sitemap_and_jsonld(fetcher):
    store = Store(
        slug="wix",
        name="Wix",
        base_url="https://wix.test",
        platform="wix",
        params={"max_products": 10},
    )
    respx.get("https://wix.test/_api/v2/dynamicmodel").mock(
        return_value=httpx.Response(200, json={"apps": {}})
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
    assert any("fallback" in w for w in report.warnings)
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


def test_malformed_document_href_is_ignored():
    html = '<a href="http://e%20%20http://www.example.com/data_sheet/74HC_HCT30.pdf">datasheet</a>'
    assert extract_doc_links(html, "https://shop.test/product") == []
